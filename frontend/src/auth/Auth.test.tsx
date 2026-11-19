import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { AuthSession } from '../api/auth'
import { health, jsonResponse, mockApi, renderApp } from '../test/utils'

const loggedOut: AuthSession = { mode: 'ldap', authenticated: false, user: null }
const ana: AuthSession = {
  mode: 'ldap',
  authenticated: true,
  user: { id: 'ana', display_name: 'Ana López', is_admin: true },
}
const luis: AuthSession = {
  mode: 'ldap',
  authenticated: true,
  user: { id: 'luis', display_name: 'Luis Pérez', is_admin: false },
}

async function signIn(username: string, password: string) {
  await userEvent.type(await screen.findByLabelText('Usuario'), username)
  await userEvent.type(screen.getByLabelText('Contraseña'), password)
  await userEvent.click(screen.getByRole('button', { name: 'Entrar' }))
}

describe('Inicio de sesión con LDAP', () => {
  it('redirige al login, valida las credenciales y muestra el chat del usuario', async () => {
    let session = loggedOut
    mockApi({
      'GET /health': health,
      'GET /auth/session': () => jsonResponse(session),
      'POST /auth/session': (init) => {
        const body = JSON.parse(init!.body as string)
        if (body.password !== 'clave-luis') return jsonResponse({}, 401)
        session = luis
        return jsonResponse(luis)
      },
      'GET /conversations': () =>
        session.authenticated
          ? jsonResponse([{ id: 'c1', title: 'Mi conversación', created_at: '', updated_at: '' }])
          : jsonResponse({}, 401),
      'GET /chat/config': () => jsonResponse({ welcome_message: null, suggested_questions: [] }),
    })
    renderApp('/c/c1')

    expect(await screen.findByRole('heading', { name: 'Iniciar sesión' })).toBeVisible()
    expect(screen.getByText('Accede con tu usuario y contraseña corporativos.')).toBeVisible()

    await signIn('luis', 'mala')
    expect(await screen.findByRole('alert')).toHaveTextContent('Usuario o contraseña incorrectos.')

    await userEvent.clear(screen.getByLabelText('Contraseña'))
    await userEvent.type(screen.getByLabelText('Contraseña'), 'clave-luis')
    await userEvent.click(screen.getByRole('button', { name: 'Entrar' }))

    const banner = await screen.findByRole('banner')
    expect(await within(banner).findByText('Luis Pérez')).toBeVisible()
    // Sin permisos de administración no se ofrece el enlace.
    expect(within(banner).queryByRole('link', { name: 'Administración' })).toBeNull()
    expect(await screen.findByRole('link', { name: 'Mi conversación' })).toBeVisible()
  })

  it('los administradores ven el enlace y cerrar sesión vuelve al login', async () => {
    let session = ana
    mockApi({
      'GET /health': health,
      'GET /auth/session': () => jsonResponse(session),
      'DELETE /auth/session': () => {
        session = loggedOut
        return jsonResponse(null, 204)
      },
      'GET /conversations': () => jsonResponse([]),
      'GET /chat/config': () => jsonResponse({ welcome_message: null, suggested_questions: [] }),
    })
    renderApp()

    const banner = await screen.findByRole('banner')
    expect(await within(banner).findByRole('link', { name: 'Administración' })).toBeVisible()
    expect(within(banner).getByText('Ana López')).toHaveAttribute('title', 'ana')

    await userEvent.click(within(banner).getByRole('button', { name: 'Cerrar sesión' }))

    expect(await screen.findByRole('heading', { name: 'Iniciar sesión' })).toBeVisible()
  })

  it('si la sesión caduca mientras se usa, una respuesta 401 lleva al login', async () => {
    let session = ana
    mockApi({
      'GET /health': health,
      'GET /auth/session': () => jsonResponse(session),
      'GET /conversations': () =>
        jsonResponse([{ id: 'c1', title: 'Antigua', created_at: '', updated_at: '' }]),
      'GET /chat/config': () => jsonResponse({ welcome_message: null, suggested_questions: [] }),
      // La cookie caduca: el servidor ya no reconoce al usuario.
      'GET /conversations/c1/messages': () => {
        session = loggedOut
        return jsonResponse({}, 401)
      },
    })
    renderApp()
    await userEvent.click(await screen.findByRole('link', { name: 'Antigua' }))

    expect(await screen.findByRole('heading', { name: 'Iniciar sesión' })).toBeVisible()
  })

  it.each([
    [503, 'No se puede validar el usuario en este momento'],
    [429, 'Demasiados intentos'],
    [500, 'No se pudo iniciar sesión'],
  ])('muestra el error %i del inicio de sesión', async (status, message) => {
    mockApi({
      'GET /auth/session': () => jsonResponse(loggedOut),
      'POST /auth/session': () => jsonResponse({}, status),
    })
    renderApp('/login')
    await signIn('ana', 'x')
    expect(await screen.findByRole('alert')).toHaveTextContent(message)
  })

  it('sin LDAP, la página de login redirige al chat', async () => {
    mockApi({
      'GET /health': health,
      'GET /auth/session': () => jsonResponse({ mode: 'none', authenticated: false, user: null }),
      'GET /conversations': () => jsonResponse([]),
    })
    renderApp('/login')

    expect(await screen.findByText('¿En qué puedo ayudarte?')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Administración' })).toBeVisible()
  })
})

describe('Administración con LDAP', () => {
  const adminSession = { authenticated: false, username: null, enabled: true }

  it('indica que se use el usuario corporativo y avisa si no tiene permisos', async () => {
    let session = loggedOut
    mockApi({
      'GET /auth/session': () => jsonResponse(session),
      'GET /admin/session': () => jsonResponse(adminSession),
      'POST /admin/session': () => {
        session = luis
        return jsonResponse({ detail: 'sin permisos' }, 403)
      },
    })
    renderApp('/admin/login')

    expect(await screen.findByText(/Usa tu usuario corporativo/)).toBeVisible()
    await signIn('luis', 'clave-luis')

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Tu usuario no tiene permisos de administración.',
    )
  })

  it('un administrador del directorio entra en la administración', async () => {
    let admin = adminSession
    mockApi({
      'GET /auth/session': () => jsonResponse(loggedOut),
      'GET /admin/session': () => jsonResponse(admin),
      'POST /admin/session': () => {
        admin = { authenticated: true, username: 'ana', enabled: true } as never
        return jsonResponse(admin)
      },
      'GET /admin/prompt': () =>
        jsonResponse({ content: 'P', version_id: null, updated_at: null, is_default: true }),
      'GET /admin/prompt/versions': () => jsonResponse([]),
    })
    renderApp('/admin/login')
    await signIn('ana', 'clave-ana')

    await waitFor(() => expect(screen.getByLabelText('Prompt de sistema')).toHaveValue('P'))
    expect(screen.getByText('ana')).toBeVisible()
  })
})

describe('Acceso anónimo con LDAP', () => {
  const anonymous: AuthSession = {
    mode: 'ldap',
    authenticated: true,
    user: { id: 'anon:1', display_name: 'Anónimo', is_admin: false, is_anonymous: true },
    anonymous_allowed: true,
  }

  function api(session: () => AuthSession, extra: Parameters<typeof mockApi>[0] = {}) {
    return mockApi({
      'GET /health': health,
      'GET /auth/session': () => jsonResponse(session()),
      'GET /conversations': () => jsonResponse([]),
      'GET /chat/config': () => jsonResponse({ welcome_message: null, suggested_questions: [] }),
      ...extra,
    })
  }

  it('sin permiso no se ofrece el enlace', async () => {
    api(() => ({ ...loggedOut, anonymous_allowed: false }))
    renderApp()

    expect(await screen.findByRole('heading', { name: 'Iniciar sesión' })).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Acceder de forma anónima' })).toBeNull()
  })

  it('con permiso se entra sin cuenta y se muestra la sesión anónima', async () => {
    let session: AuthSession = { ...loggedOut, anonymous_allowed: true }
    const fetchMock = api(() => session, {
      'POST /auth/anonymous': () => {
        session = anonymous
        return jsonResponse(anonymous)
      },
    })
    renderApp('/login')

    await userEvent.click(await screen.findByRole('button', { name: 'Acceder de forma anónima' }))

    const banner = await screen.findByRole('banner')
    const badge = await within(banner).findByText('Anónimo')
    expect(badge).toHaveAttribute('title', expect.stringContaining('no podrás volver a ver'))
    // Un acceso anónimo no administra.
    expect(within(banner).queryByRole('link', { name: 'Administración' })).toBeNull()
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url).endsWith('/auth/anonymous') && init?.method === 'POST',
      ),
    ).toBe(true)
  })

  it('al cerrar la sesión anónima se vuelve al login, que sigue ofreciendo el acceso', async () => {
    let session = anonymous
    api(() => session, {
      'DELETE /auth/session': () => {
        session = { ...loggedOut, anonymous_allowed: true }
        return jsonResponse(null, 204)
      },
    })
    renderApp()

    const banner = await screen.findByRole('banner')
    await userEvent.click(await within(banner).findByRole('button', { name: 'Cerrar sesión' }))

    expect(await screen.findByRole('heading', { name: 'Iniciar sesión' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Acceder de forma anónima' })).toBeVisible()
  })

  it('si el servidor lo rechaza, se informa', async () => {
    api(() => ({ ...loggedOut, anonymous_allowed: true }), {
      'POST /auth/anonymous': () => jsonResponse({}, 403),
    })
    renderApp('/login')

    await userEvent.click(await screen.findByRole('button', { name: 'Acceder de forma anónima' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'No se pudo acceder de forma anónima.',
    )
  })
})
