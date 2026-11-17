export const API_BASE = '/api/v1'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export async function apiRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (typeof init.body === 'string') headers['Content-Type'] = 'application/json'
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...headers, ...(init.headers as Record<string, string> | undefined) },
  })
  if (!response.ok) {
    throw new ApiError(response.status, `${init.method ?? 'GET'} ${path} → ${response.status}`)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

export const apiGet = <T>(path: string, init?: RequestInit) => apiRequest<T>(path, init)

export interface HealthResponse {
  status: 'ok' | 'degraded'
  app: string
  version: string
  database: 'ok' | 'error'
}

export const getHealth = (signal?: AbortSignal) => apiGet<HealthResponse>('/health', { signal })

export interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export interface Message {
  id: number
  role: 'user' | 'assistant'
  content: string
  created_at: string
}

export const listConversations = () => apiGet<Conversation[]>('/conversations')

export const renameConversation = (id: string, title: string) =>
  apiRequest<Conversation>(`/conversations/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  })

export const deleteConversation = (id: string) =>
  apiRequest<void>(`/conversations/${id}`, { method: 'DELETE' })

export const listMessages = (conversationId: string) =>
  apiGet<Message[]>(`/conversations/${conversationId}/messages`)
