import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { AdminSession, SystemPrompt } from '../../api/admin'
import { health, jsonResponse, mockApi, renderApp } from '../../test/utils'

const anonymous: AdminSession = { authenticated: false, username: null, enabled: true }
const admin: AdminSession = { authenticated: true, username: 'admin', enabled: true }
const defaultPrompt: SystemPrompt = {
  content: 'Prompt por defecto',
  version_id: null,
  updated_at: null,
  is_default: true,
}
const version = (id: number, content: string) => ({
  id,
  content,
  created_by: 'admin',
  created_at: '2026-11-17T10:00:00Z',
})

function adminApi(overrides: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    'GET /health': health,
    'GET /conversations': () => jsonResponse([]),
    'GET /admin/session': () => jsonResponse(admin),
    'GET /admin/prompt': () => jsonResponse(defaultPrompt),
    'GET /admin/prompt/versions': () => jsonResponse([]),
    ...overrides,
  })
}

describe('Login', () => {
  it('redirige al login sin sesión y entra con credenciales válidas', async () => {
    let session = anonymous
    const api = adminApi({
      'GET /admin/session': () => jsonResponse(session),
      'POST /admin/session': (init) => {
        const body = JSON.parse(init!.body as string)
        if (body.password !== 'secreto') return jsonResponse({}, 401)
        session = admin
        return jsonResponse(admin)
      },
    })
    renderApp('/admin')

    expect(await screen.findByRole('heading', { name: 'Acceso de administración' })).toBeVisible()
    await userEvent.type(screen.getByLabelText('Usuario'), 'admin')
    await userEvent.type(screen.getByLabelText('Contraseña'), 'mala')
    await userEvent.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Usuario o contraseña incorrectos.')

    await userEvent.clear(screen.getByLabelText('Contraseña'))
    await userEvent.type(screen.getByLabelText('Contraseña'), 'secreto')
    await userEvent.click(screen.getByRole('button', { name: 'Entrar' }))

    expect(await screen.findByLabelText('Prompt de sistema')).toHaveValue('Prompt por defecto')
    expect(api).toHaveBeenCalledWith('/api/v1/admin/session', expect.anything())
  })

  it('muestra error genérico si el servidor falla', async () => {
    adminApi({
      'GET /admin/session': () => jsonResponse(anonymous),
      'POST /admin/session': () => jsonResponse({}, 500),
    })
    renderApp('/admin/login')
    await userEvent.type(await screen.findByLabelText('Usuario'), 'a')
    await userEvent.type(screen.getByLabelText('Contraseña'), 'b')
    await userEvent.click(screen.getByRole('button', { name: 'Entrar' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('No se pudo iniciar sesión')
  })

  it('avisa si la administración no está configurada', async () => {
    adminApi({ 'GET /admin/session': () => jsonResponse({ ...anonymous, enabled: false }) })
    renderApp('/admin/login')
    expect(await screen.findByRole('alert')).toHaveTextContent('CHATBOT_ADMIN_PASSWORD')
    expect(screen.queryByLabelText('Usuario')).toBeNull()
  })

  it('con sesión iniciada, el login redirige al prompt', async () => {
    adminApi()
    renderApp('/admin/login')
    expect(await screen.findByLabelText('Prompt de sistema')).toBeInTheDocument()
  })

  it('cierra sesión y vuelve al login', async () => {
    let session = admin
    adminApi({
      'GET /admin/session': () => jsonResponse(session),
      'DELETE /admin/session': () => {
        session = anonymous
        return jsonResponse(null, 204)
      },
    })
    renderApp('/admin/prompt')
    await userEvent.click(await screen.findByRole('button', { name: 'Cerrar sesión' }))
    expect(await screen.findByRole('heading', { name: 'Acceso de administración' })).toBeVisible()
  })

  it('el chat enlaza con la administración', async () => {
    adminApi()
    renderApp('/')
    await userEvent.click(screen.getByRole('link', { name: 'Administración' }))
    expect(await screen.findByLabelText('Prompt de sistema')).toBeInTheDocument()
  })
})

describe('PromptPage', () => {
  it('guarda el prompt y muestra la nueva versión vigente', async () => {
    let current = defaultPrompt
    let versions: ReturnType<typeof version>[] = []
    const api = adminApi({
      'GET /admin/prompt': () => jsonResponse(current),
      'GET /admin/prompt/versions': () => jsonResponse(versions),
      'PUT /admin/prompt': (init) => {
        const { content } = JSON.parse(init!.body as string)
        versions = [version(1, content)]
        current = { content, version_id: 1, updated_at: '', is_default: false }
        return jsonResponse(current)
      },
    })
    renderApp('/admin/prompt')

    const editor = await screen.findByLabelText('Prompt de sistema')
    const save = screen.getByRole('button', { name: 'Guardar' })
    expect(screen.getByText('Se está usando el prompt predeterminado del servidor.')).toBeVisible()
    expect(screen.getByText('Todavía no hay versiones guardadas.')).toBeVisible()
    expect(save).toBeDisabled()

    await userEvent.clear(editor)
    await userEvent.type(editor, '  Eres un experto.  ')
    expect(screen.getByText('20 caracteres')).toBeVisible()
    await userEvent.click(save)

    expect(await screen.findByRole('status')).toHaveTextContent('Prompt guardado.')
    const item = await screen.findByRole('listitem', { name: 'Versión 1' })
    expect(within(item).getByText('Vigente')).toBeVisible()
    expect(screen.getByLabelText('Prompt de sistema')).toHaveValue('Eres un experto.')
    expect(screen.queryByText('Se está usando el prompt predeterminado del servidor.')).toBeNull()
    const put = api.mock.calls.find(([, init]) => init?.method === 'PUT')
    expect(JSON.parse(put![1]!.body as string)).toEqual({ content: 'Eres un experto.' })
  })

  it('restaura una versión anterior tras confirmar', async () => {
    let current: SystemPrompt = { content: 'Dos', version_id: 2, updated_at: '', is_default: false }
    let versions = [version(2, 'Dos'), version(1, 'Uno')]
    adminApi({
      'GET /admin/prompt': () => jsonResponse(current),
      'GET /admin/prompt/versions': () => jsonResponse(versions),
      'POST /admin/prompt/versions/1/restore': () => {
        versions = [version(3, 'Uno'), ...versions]
        current = { content: 'Uno', version_id: 3, updated_at: '', is_default: false }
        return jsonResponse(current)
      },
    })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValue(true)
    renderApp('/admin/prompt')

    const old = await screen.findByRole('listitem', { name: 'Versión 1' })
    await userEvent.click(within(old).getByRole('button', { name: 'Restaurar' }))
    expect(screen.getByLabelText('Prompt de sistema')).toHaveValue('Dos')

    await userEvent.click(within(old).getByRole('button', { name: 'Restaurar' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Versión restaurada.')
    expect(screen.getByLabelText('Prompt de sistema')).toHaveValue('Uno')
    expect(await screen.findByRole('listitem', { name: 'Versión 3' })).toBeVisible()
    expect(confirm).toHaveBeenCalledWith('¿Restaurar la versión 1? Se creará una nueva versión.')
  })

  it('muestra errores de carga y de guardado', async () => {
    adminApi({ 'PUT /admin/prompt': () => jsonResponse({}, 500) })
    renderApp('/admin/prompt')
    const editor = await screen.findByLabelText('Prompt de sistema')
    await userEvent.type(editor, ' más')
    await userEvent.click(screen.getByRole('button', { name: 'Guardar' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('No se pudo guardar el prompt.')
  })

  it('muestra error si no se puede cargar el prompt', async () => {
    adminApi({ 'GET /admin/prompt': () => jsonResponse({}, 500) })
    renderApp('/admin/prompt')
    expect(
      await screen.findByText('No se pudo cargar el prompt.', {}, { timeout: 3000 }),
    ).toBeVisible()
  })

  it('si la sesión caduca vuelve al login', async () => {
    let session = admin
    adminApi({
      'GET /admin/session': () => jsonResponse(session),
      'GET /admin/prompt': () => {
        session = anonymous
        return jsonResponse({}, 401)
      },
    })
    renderApp('/admin/prompt')
    await waitFor(
      () => expect(screen.getByRole('heading', { name: 'Acceso de administración' })).toBeVisible(),
      { timeout: 3000 },
    )
  })
})
