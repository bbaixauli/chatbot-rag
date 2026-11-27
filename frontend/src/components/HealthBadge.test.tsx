import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { HealthBadge } from './HealthBadge'

function mockFetch(response: Partial<Response> | Error) {
  return vi
    .spyOn(globalThis, 'fetch')
    .mockImplementation(() =>
      response instanceof Error ? Promise.reject(response) : Promise.resolve(response as Response),
    )
}

const okResponse = (body: unknown): Partial<Response> => ({
  ok: true,
  status: 200,
  json: () => Promise.resolve(body),
})

describe('HealthBadge', () => {
  it('muestra el estado disponible', async () => {
    const fetchSpy = mockFetch(
      okResponse({
        status: 'ok',
        app: 'Chatbot RAG',
        version: '0.1.0',
        database: 'ok',
        vector_store: 'ok',
        embeddings: 'ok',
      }),
    )
    render(<HealthBadge />)

    expect(await screen.findByText('Servicio disponible')).toBeInTheDocument()
    expect(fetchSpy).toHaveBeenCalledWith('/api/v1/health', expect.anything())
  })

  it('muestra servicio degradado', async () => {
    mockFetch(
      okResponse({
        status: 'degraded',
        app: 'x',
        version: '0',
        database: 'error',
        vector_store: 'error',
        embeddings: 'ok',
      }),
    )
    render(<HealthBadge />)
    expect(await screen.findByText('Servicio degradado')).toBeInTheDocument()
  })

  it('indica si solo falla la documentación (índice o embeddings)', async () => {
    mockFetch(
      okResponse({
        status: 'degraded',
        app: 'x',
        version: '0',
        database: 'ok',
        vector_store: 'ok',
        embeddings: 'error',
      }),
    )
    render(<HealthBadge />)
    const status = await screen.findByText('Documentación no disponible')
    expect(status).toHaveAttribute('data-state', 'degraded')
  })

  it('muestra sin conexión si la API devuelve error HTTP', async () => {
    mockFetch({ ok: false, status: 502 })
    render(<HealthBadge />)
    expect(await screen.findByText('Sin conexión con el servidor')).toBeInTheDocument()
  })

  it('cancela la petición al desmontar sin marcar sin conexión', async () => {
    let rejectFetch: (reason: unknown) => void = () => {}
    vi.spyOn(globalThis, 'fetch').mockImplementation(
      () => new Promise((_, reject) => (rejectFetch = reject)),
    )
    const { unmount } = render(<HealthBadge />)
    const status = screen.getByRole('status')
    unmount()
    rejectFetch(new DOMException('aborted', 'AbortError'))
    await Promise.resolve()

    expect(status).toHaveAttribute('data-state', 'checking')
  })

  it('muestra sin conexión si falla la red', async () => {
    mockFetch(new TypeError('network'))
    render(<HealthBadge />)
    expect(await screen.findByText('Sin conexión con el servidor')).toBeInTheDocument()
  })
})
