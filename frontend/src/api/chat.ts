import { API_BASE, ApiError, type Scope, type SearchScope, type Source } from './client'

export type ChatEvent =
  | { event: 'conversation'; data: { id: string; title: string; user_message_id: number } }
  | { event: 'scope'; data: Scope }
  | { event: 'token'; data: { content: string } }
  | { event: 'sources'; data: { sources: Source[] } }
  | { event: 'done'; data: { message_id: number } }
  | { event: 'error'; data: { detail: string } }

interface RawEvent {
  event: string
  data: string
}

/** Lee un flujo Server-Sent Events y devuelve cada evento completo. */
export async function* readSSE(stream: ReadableStream<Uint8Array>): AsyncGenerator<RawEvent> {
  const reader = stream.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value, { stream: !done }).replaceAll('\r\n', '\n')
    let boundary = buffer.indexOf('\n\n')
    while (boundary !== -1) {
      const parsed = parseBlock(buffer.slice(0, boundary))
      buffer = buffer.slice(boundary + 2)
      if (parsed) yield parsed
      boundary = buffer.indexOf('\n\n')
    }
    if (done) break
  }
  const tail = parseBlock(buffer)
  if (tail) yield tail
}

function parseBlock(block: string): RawEvent | null {
  let event = 'message'
  const data: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
  }
  return data.length ? { event, data: data.join('\n') } : null
}

/**
 * Envía un mensaje y devuelve los eventos de la respuesta en streaming. Con `scope` se limita
 * la búsqueda a esos documentos y categorías (y se guarda en la conversación); sin indicarlo,
 * se mantiene la limitación que ya tenga la conversación.
 */
export async function* streamChat(
  message: string,
  conversationId: string | null,
  signal?: AbortSignal,
  scope?: SearchScope,
): AsyncGenerator<ChatEvent> {
  const body: Record<string, unknown> = { message, conversation_id: conversationId }
  if (scope) {
    body.document_ids = scope.documentIds
    body.category_ids = scope.categoryIds
  }
  const response = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
    signal,
  })
  if (!response.ok || !response.body) {
    throw new ApiError(response.status, `POST /chat → ${response.status}`)
  }
  for await (const raw of readSSE(response.body)) {
    yield { event: raw.event, data: JSON.parse(raw.data) } as ChatEvent
  }
}
