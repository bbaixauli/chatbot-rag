export const API_BASE = '/api/v1'

export class ApiError extends Error {
  readonly status: number
  /** Explicación del servidor (`detail` de FastAPI), si es un texto. */
  readonly detail: string | null

  constructor(status: number, message: string, detail: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

async function errorDetail(response: Response): Promise<string | null> {
  try {
    const data = (await response.json()) as { detail?: unknown }
    return typeof data.detail === 'string' ? data.detail : null
  } catch {
    return null
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
    throw new ApiError(
      response.status,
      `${init.method ?? 'GET'} ${path} → ${response.status}`,
      await errorDetail(response),
    )
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
  /** Documentos a los que se limita la búsqueda (vacía o ausente: todos). */
  document_ids?: string[]
}

/** Formatos de documento admitidos. */
export type DocumentKind =
  'pdf' | 'markdown' | 'html' | 'docx' | 'pptx' | 'xlsx' | 'odt' | 'odp' | 'ods'

/** Documento indexado, para elegir dónde buscar. */
export interface IndexedDocument {
  id: string
  filename: string
  title: string
  kind: DocumentKind
  category: string | null
}

/** Documentos a los que se limitó la búsqueda de una respuesta. */
export interface Scope {
  /** `title`: título actual del documento (las respuestas anteriores a #75 no lo tienen). */
  documents: { id: string; filename: string; title?: string }[]
  /** `selector`: elegidos en el chat; `detected`: nombrados en la pregunta. */
  source: 'selector' | 'detected'
}

/** Como mucho, documentos a los que se puede limitar la búsqueda (igual que el servidor). */
export const MAX_SCOPE_DOCUMENTS = 5

export interface Source {
  number: number
  document_id: string | null
  filename: string
  /** Título actual del documento (o el nombre del fichero si se borró). */
  title?: string
  page: number | null
  heading: string | null
  snippet: string
  cited: boolean
}

export interface Message {
  id: number
  role: 'user' | 'assistant'
  content: string
  created_at: string
  sources?: Source[]
  scope?: Scope | null
}

/** URL para ver el original de una fuente (los PDF se abren en la página citada). */
/** Las presentaciones numeran diapositivas en lugar de páginas. */
export const isSlides = (filename: string) => /\.(pptx|odp)$/i.test(filename)

export function sourceUrl(source: Source): string | null {
  if (!source.document_id) return null
  // Solo los PDF se abren en la página citada; los demás formatos se muestran o descargan.
  const page = source.page && /\.pdf$/i.test(source.filename) ? `#page=${source.page}` : ''
  return `${API_BASE}/documents/${source.document_id}/file${page}`
}

/** Inicio de las conversaciones configurado en la administración. */
export interface ChatConfig {
  welcome_message: string | null
  suggested_questions: string[]
}

export const getChatConfig = () => apiGet<ChatConfig>('/chat/config')

export const listConversations = () => apiGet<Conversation[]>('/conversations')

export const renameConversation = (id: string, title: string) =>
  apiRequest<Conversation>(`/conversations/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ title }),
  })

/** Limita la búsqueda de la conversación a esos documentos (lista vacía: todos). */
export const updateConversationScope = (id: string, documentIds: string[]) =>
  apiRequest<Conversation>(`/conversations/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ document_ids: documentIds }),
  })

export const listIndexedDocuments = () => apiGet<IndexedDocument[]>('/documents')

export const deleteConversation = (id: string) =>
  apiRequest<void>(`/conversations/${id}`, { method: 'DELETE' })

export const listMessages = (conversationId: string) =>
  apiGet<Message[]>(`/conversations/${conversationId}/messages`)
