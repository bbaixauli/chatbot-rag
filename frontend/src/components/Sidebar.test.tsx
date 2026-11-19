import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { health, jsonResponse, mockApi, renderApp } from '../test/utils'

const conv = (id: string, title: string) => ({
  id,
  title,
  created_at: '2026-01-01T00:00:00',
  updated_at: '2026-01-01T00:00:00',
})

describe('Sidebar', () => {
  it('muestra mensaje si no hay conversaciones', async () => {
    mockApi({ 'GET /health': health, 'GET /conversations': () => jsonResponse([]) })
    renderApp()
    expect(await screen.findByText('Aún no hay conversaciones.')).toBeInTheDocument()
  })

  it('muestra error si falla la carga', async () => {
    mockApi({ 'GET /health': health, 'GET /conversations': () => jsonResponse({}, 500) })
    renderApp()
    expect(await screen.findByText('No se pudo cargar el historial.')).toBeInTheDocument()
  })

  it('lista conversaciones y renombra con Intro', async () => {
    let title = 'Antiguo'
    const api = mockApi({
      'GET /health': health,
      'GET /conversations': () => jsonResponse([conv('c1', title)]),
      'PATCH /conversations/c1': (init) => {
        title = JSON.parse(init!.body as string).title
        return jsonResponse(conv('c1', title))
      },
    })
    renderApp()
    const nav = await screen.findByRole('navigation', { name: 'Historial de conversaciones' })
    expect(await within(nav).findByRole('link', { name: 'Antiguo' })).toHaveAttribute(
      'href',
      '/c/c1',
    )

    await userEvent.click(within(nav).getByRole('button', { name: 'Renombrar: Antiguo' }))
    const input = within(nav).getByLabelText('Nuevo título')
    await userEvent.clear(input)
    await userEvent.type(input, 'Nuevo{Enter}')

    expect(await within(nav).findByRole('link', { name: 'Nuevo' })).toBeInTheDocument()
    expect(api).toHaveBeenCalledWith('/api/v1/conversations/c1', expect.anything())
  })

  it('cancela el renombrado con Escape o con título vacío', async () => {
    const api = mockApi({
      'GET /health': health,
      'GET /conversations': () => jsonResponse([conv('c1', 'Título')]),
    })
    renderApp()
    const rename = await screen.findByRole('button', { name: 'Renombrar: Título' })

    await userEvent.click(rename)
    await userEvent.type(screen.getByLabelText('Nuevo título'), 'x{Escape}')
    await userEvent.click(screen.getByRole('button', { name: 'Renombrar: Título' }))
    await userEvent.clear(screen.getByLabelText('Nuevo título'))
    await userEvent.tab()
    await userEvent.click(screen.getByRole('button', { name: 'Renombrar: Título' }))
    await userEvent.type(screen.getByLabelText('Nuevo título'), '{Enter}')

    expect(screen.getByRole('link', { name: 'Título' })).toBeInTheDocument()
    expect(api.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false)
  })

  it('elimina tras confirmar y vuelve a inicio si era la activa', async () => {
    let items = [conv('c1', 'Uno'), conv('c2', 'Dos')]
    mockApi({
      'GET /health': health,
      'GET /conversations': () => jsonResponse(items),
      'GET /conversations/c1/messages': () => jsonResponse([]),
      'DELETE /conversations/c1': () => {
        items = items.filter((c) => c.id !== 'c1')
        return jsonResponse(null, 204)
      },
    })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValue(true)
    renderApp('/c/c1')

    const remove = await screen.findByRole('button', { name: 'Eliminar: Uno' })
    await userEvent.click(remove)
    expect(screen.getByRole('link', { name: 'Uno' })).toBeInTheDocument()

    await userEvent.click(remove)
    await waitFor(() => expect(screen.queryByRole('link', { name: 'Uno' })).toBeNull())
    expect(confirm).toHaveBeenCalledWith('¿Eliminar la conversación «Uno»?')
    expect(await screen.findByText('¿En qué puedo ayudarte?')).toBeInTheDocument()
  })

  it('elimina una conversación que no es la activa sin navegar', async () => {
    let items = [conv('c1', 'Uno'), conv('c2', 'Dos')]
    mockApi({
      'GET /health': health,
      'GET /conversations': () => jsonResponse(items),
      'GET /conversations/c1/messages': () =>
        jsonResponse([{ id: 1, role: 'user', content: 'hola', created_at: '' }]),
      'DELETE /conversations/c2': () => {
        items = items.filter((c) => c.id !== 'c2')
        return jsonResponse(null, 204)
      },
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderApp('/c/c1')

    await userEvent.click(await screen.findByRole('button', { name: 'Eliminar: Dos' }))

    await waitFor(() => expect(screen.queryByRole('link', { name: 'Dos' })).toBeNull())
    expect(screen.getByText('hola')).toBeInTheDocument()
  })

  it('el botón de nueva conversación vuelve a la pantalla inicial', async () => {
    mockApi({
      'GET /health': health,
      'GET /conversations': () => jsonResponse([conv('c1', 'Uno')]),
      'GET /conversations/c1/messages': () =>
        jsonResponse([{ id: 1, role: 'user', content: 'hola', created_at: '' }]),
    })
    renderApp('/c/c1')
    expect(await screen.findByText('hola')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Nueva conversación' }))

    expect(await screen.findByText('¿En qué puedo ayudarte?')).toBeInTheDocument()
  })
})
