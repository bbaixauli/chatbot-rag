import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { AccessConfig, AdminSession } from '../../api/admin'
import type { ChatConfig } from '../../api/client'
import { health, jsonResponse, mockApi, renderApp } from '../../test/utils'

const admin: AdminSession = { authenticated: true, username: 'admin', enabled: true }

function api(overrides: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    'GET /health': health,
    'GET /conversations': () => jsonResponse([]),
    'GET /admin/session': () => jsonResponse(admin),
    'GET /admin/chat-config': () =>
      jsonResponse({ welcome_message: 'Hola', suggested_questions: ['Una'] }),
    'GET /admin/access': () => jsonResponse(access('none')),
    ...overrides,
  })
}

const access = (mode: 'none' | 'ldap', overrides: Partial<AccessConfig> = {}): AccessConfig => ({
  mode,
  anonymous_allowed: false,
  private_sessions: false,
  ...overrides,
})

const question = (n: number) => screen.getByLabelText(`Pregunta sugerida ${n}`)

describe('ChatStartPage', () => {
  it('se accede desde la navegación y carga la configuración actual', async () => {
    api()
    renderApp('/admin/prompt')
    await userEvent.click(await screen.findByRole('link', { name: 'Mensaje inicial' }))

    expect(await screen.findByLabelText('Mensaje inicial')).toHaveValue('Hola')
    expect(question(1)).toHaveValue('Una')
    expect(question(5)).toHaveValue('')
    expect(screen.queryByLabelText('Pregunta sugerida 6')).toBeNull()
  })

  it('guarda el mensaje y las preguntas descartando las vacías', async () => {
    let sent: ChatConfig | null = null
    api({
      'PUT /admin/chat-config': (init) => {
        sent = JSON.parse(init!.body as string) as ChatConfig
        return jsonResponse(sent)
      },
    })
    renderApp('/admin/chat')
    const welcome = await screen.findByLabelText('Mensaje inicial')

    await userEvent.clear(welcome)
    await userEvent.type(welcome, '  Bienvenido  ')
    await userEvent.clear(question(1))
    await userEvent.type(question(3), '¿Horario?')
    await userEvent.type(question(5), '¿Teletrabajo?')
    await userEvent.click(screen.getByRole('button', { name: 'Guardar' }))

    expect(await screen.findByRole('status')).toHaveTextContent('Configuración guardada.')
    expect(sent).toEqual({
      welcome_message: 'Bienvenido',
      suggested_questions: ['¿Horario?', '¿Teletrabajo?'],
    })
    // Las preguntas guardadas se reordenan sin huecos.
    expect(question(1)).toHaveValue('¿Horario?')
    expect(question(2)).toHaveValue('¿Teletrabajo?')

    await userEvent.type(question(3), 'x')
    expect(screen.queryByRole('status')).toBeNull()
  })

  it('un mensaje vacío desactiva el mensaje inicial', async () => {
    let sent: ChatConfig | null = null
    api({
      'PUT /admin/chat-config': (init) => {
        sent = JSON.parse(init!.body as string) as ChatConfig
        return jsonResponse(sent)
      },
    })
    renderApp('/admin/chat')
    await userEvent.clear(await screen.findByLabelText('Mensaje inicial'))
    await userEvent.click(screen.getByRole('button', { name: 'Guardar' }))

    await waitFor(() =>
      expect(sent).toEqual({ welcome_message: null, suggested_questions: ['Una'] }),
    )
  })

  it('impide guardar un mensaje demasiado largo', async () => {
    api({
      'GET /admin/chat-config': () =>
        jsonResponse({ welcome_message: 'x'.repeat(2001), suggested_questions: [] }),
    })
    renderApp('/admin/chat')

    expect(await screen.findByText('2001 / 2000 caracteres')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Guardar' })).toBeDisabled()
  })

  it('muestra los errores de carga y de guardado', async () => {
    api({ 'PUT /admin/chat-config': () => jsonResponse({}, 500) })
    renderApp('/admin/chat')
    await screen.findByLabelText('Mensaje inicial')
    await userEvent.click(screen.getByRole('button', { name: 'Guardar' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'No se pudo guardar la configuración.',
    )
  })

  it('vuelve al login si la sesión ha caducado', async () => {
    let session = admin
    api({
      'GET /admin/session': () => jsonResponse(session),
      'GET /admin/chat-config': () => {
        session = { authenticated: false, username: null, enabled: true }
        return jsonResponse({}, 401)
      },
    })
    renderApp('/admin/chat')

    expect(await screen.findByRole('heading', { name: 'Acceso de administración' })).toBeVisible()
  })

  it('muestra un error si falla la carga', async () => {
    api({ 'GET /admin/chat-config': () => jsonResponse({}, 500) })
    renderApp('/admin/chat')
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'No se pudo cargar la configuración.',
    )
  })

  describe('acceso al chat', () => {
    const anonymousBox = () => screen.getByRole('checkbox', { name: 'Permitir el acceso anónimo' })
    const privateBox = () =>
      screen.getByRole('checkbox', { name: 'Sesiones privadas y temporales' })

    function saving(mode: 'none' | 'ldap', sent: object[]) {
      let current = access(mode)
      return {
        'GET /admin/access': () => jsonResponse(current),
        'PUT /admin/access': (init: RequestInit | undefined) => {
          const change = JSON.parse(init!.body as string)
          sent.push(change)
          current = { ...current, ...change }
          return jsonResponse(current)
        },
      }
    }

    it('sin LDAP ofrece las sesiones privadas y las activa al momento', async () => {
      const sent: object[] = []
      api(saving('none', sent))
      renderApp('/admin/chat')

      expect(await screen.findByRole('heading', { name: 'Acceso al chat' })).toBeVisible()
      expect(privateBox()).not.toBeChecked()
      expect(screen.queryByRole('checkbox', { name: 'Permitir el acceso anónimo' })).toBeNull()
      expect(screen.getByText(/se vuelve al historial compartido/)).toBeVisible()

      await userEvent.click(privateBox())
      expect(await screen.findByText('Acceso actualizado.')).toBeVisible()
      expect(privateBox()).toBeChecked()
      await userEvent.click(privateBox())
      await waitFor(() => expect(privateBox()).not.toBeChecked())
      expect(sent).toEqual([{ private_sessions: true }, { private_sessions: false }])
    })

    it('con LDAP ofrece el acceso anónimo y lo activa al momento', async () => {
      const sent: object[] = []
      api(saving('ldap', sent))
      renderApp('/admin/chat')

      expect(await screen.findByRole('heading', { name: 'Acceso al chat' })).toBeVisible()
      expect(anonymousBox()).not.toBeChecked()
      expect(screen.queryByRole('checkbox', { name: 'Sesiones privadas y temporales' })).toBeNull()
      expect(screen.getByText(/podrá usar el chat y abrir los documentos/)).toBeVisible()

      await userEvent.click(anonymousBox())
      expect(await screen.findByText('Acceso actualizado.')).toBeVisible()
      expect(anonymousBox()).toBeChecked()
      await userEvent.click(anonymousBox())
      await waitFor(() => expect(anonymousBox()).not.toBeChecked())
      expect(sent).toEqual([{ anonymous_allowed: true }, { anonymous_allowed: false }])
    })

    it('informa si no se puede guardar', async () => {
      api({
        'GET /admin/access': () => jsonResponse(access('ldap')),
        'PUT /admin/access': () => jsonResponse({}, 500),
      })
      renderApp('/admin/chat')
      await userEvent.click(await screen.findByRole('checkbox'))
      expect(await screen.findByRole('alert')).toHaveTextContent('No se pudo cambiar el acceso.')
      expect(anonymousBox()).not.toBeChecked()
    })

    it('informa si falla la carga', async () => {
      api({ 'GET /admin/access': () => jsonResponse({}, 500) })
      renderApp('/admin/chat')
      expect(await screen.findByRole('alert')).toHaveTextContent(
        'No se pudo cargar la configuración de acceso.',
      )
    })
  })
})
