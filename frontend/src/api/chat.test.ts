import { describe, expect, it, vi } from 'vitest'
import { sseResponse } from '../test/utils'
import { ApiError } from './client'
import { readSSE, streamChat } from './chat'

function streamOf(...parts: string[]) {
  const encoder = new TextEncoder()
  return new ReadableStream<Uint8Array>({
    start(controller) {
      parts.forEach((p) => controller.enqueue(encoder.encode(p)))
      controller.close()
    },
  })
}

async function collect<T>(iterable: AsyncIterable<T>) {
  const out: T[] = []
  for await (const item of iterable) out.push(item)
  return out
}

describe('readSSE', () => {
  it('une eventos partidos entre fragmentos', async () => {
    const events = await collect(
      readSSE(streamOf('event: tok', 'en\ndata: {"a"', ':1}\n\nevent: done\ndata: {}\n\n')),
    )
    expect(events).toEqual([
      { event: 'token', data: '{"a":1}' },
      { event: 'done', data: '{}' },
    ])
  })

  it('acepta CRLF, datos multilínea, evento por defecto y bloque final sin separador', async () => {
    const events = await collect(
      readSSE(streamOf(': comentario\r\n\r\ndata: a\r\ndata: b\r\n\r\nevent: x\ndata: fin')),
    )
    expect(events).toEqual([
      { event: 'message', data: 'a\nb' },
      { event: 'x', data: 'fin' },
    ])
  })
})

describe('streamChat', () => {
  it('envía la petición y devuelve los eventos tipados', async () => {
    const fetchSpy = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(sseResponse([['token', { content: 'hola' }]], true))

    const events = await collect(streamChat('pregunta', 'c1'))

    expect(events).toEqual([{ event: 'token', data: { content: 'hola' } }])
    const [url, init] = fetchSpy.mock.calls[0]
    expect(url).toBe('/api/v1/chat')
    expect(JSON.parse(init!.body as string)).toEqual({
      message: 'pregunta',
      conversation_id: 'c1',
    })
  })

  it('lanza ApiError si la respuesta no es correcta', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response('{}', { status: 404 }))
    await expect(collect(streamChat('x', 'nope'))).rejects.toBeInstanceOf(ApiError)
  })
})
