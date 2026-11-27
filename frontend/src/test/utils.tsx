import { QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { vi } from 'vitest'
import { AppRoutes } from '../App'
import { createQueryClient } from '../queryClient'

export function renderApp(path = '/') {
  const queryClient = createQueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

export const jsonResponse = (body: unknown, status = 200) =>
  new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })

export function sseResponse(events: Array<[string, unknown]>, split = false) {
  const text = events.map(([e, d]) => `event: ${e}\ndata: ${JSON.stringify(d)}\n\n`).join('')
  const encoder = new TextEncoder()
  const parts = split ? text.match(/[\s\S]{1,7}/g)! : [text]
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const part of parts) controller.enqueue(encoder.encode(part))
      controller.close()
    },
  })
  return new Response(body, { status: 200, headers: { 'Content-Type': 'text/event-stream' } })
}

type Handler = (init: RequestInit | undefined, url: string) => Response | Promise<Response>

/** Simula `fetch` con manejadores por "MÉTODO ruta" (la ruta admite prefijos con `*`). */
export function mockApi(routes: Record<string, Handler>) {
  return vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const url = typeof input === 'string' ? input : (input as Request).url
    const method = init?.method ?? 'GET'
    const key = `${method} ${url.replace('/api/v1', '')}`
    const handler =
      routes[key] ??
      Object.entries(routes).find(([k]) => k.endsWith('*') && key.startsWith(k.slice(0, -1)))?.[1]
    if (!handler) return jsonResponse({ detail: `sin mock: ${key}` }, 500)
    return handler(init, url)
  })
}

export const health = () =>
  jsonResponse({
    status: 'ok',
    app: 'Chatbot RAG',
    version: '0.1.0',
    database: 'ok',
    vector_store: 'ok',
    embeddings: 'ok',
  })
