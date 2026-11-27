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
  /** Sin ellos, el chat responde sin documentación (#98). */
  vector_store: 'ok' | 'error'
  embeddings: 'ok' | 'error'
}

export const getHealth = (signal?: AbortSignal) => apiGet<HealthResponse>('/health', { signal })

export interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
  /** Documentos a los que se limita la búsqueda (vacía o ausente: todos). */
  document_ids?: string[]
  /** Categorías a cuyos documentos se limita la búsqueda (#85). */
  category_ids?: number[]
}

/** Dónde buscar: documentos sueltos y categorías (ambos vacíos: en todos). */
export interface SearchScope {
  documentIds: string[]
  categoryIds: number[]
}

export const ALL_DOCUMENTS: SearchScope = { documentIds: [], categoryIds: [] }

export const isLimited = (scope: SearchScope) =>
  scope.documentIds.length > 0 || scope.categoryIds.length > 0

/** La limitación guardada en una conversación. */
export const conversationScope = (conversation?: Conversation): SearchScope => ({
  documentIds: conversation?.document_ids ?? [],
  categoryIds: conversation?.category_ids ?? [],
})

/** Formatos de documento admitidos. */
export type DocumentKind =
  | 'pdf'
  | 'markdown'
  | 'html'
  | 'docx'
  | 'pptx'
  | 'xlsx'
  | 'odt'
  | 'odp'
  | 'ods'
  /** Página web añadida por su dirección. */
  | 'web'

/** Documento indexado, para elegir dónde buscar. */
export interface IndexedDocument {
  id: string
  filename: string
  title: string
  kind: DocumentKind
  category: string | null
  category_id?: number | null
}

/** Documentos a los que se limitó la búsqueda de una respuesta. */
export interface Scope {
  /** `title`: título actual del documento (las respuestas anteriores a #75 no lo tienen). */
  documents: { id: string; filename: string; title?: string }[]
  /** Categorías elegidas, con su nombre actual (las respuestas anteriores a #85 no las tienen). */
  categories?: { id: number; name: string }[]
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
  /** Dirección web del documento, si se añadió por ella: la referencia enlaza a ella. */
  url?: string | null
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
  /** Falló la búsqueda en la documentación al generar la respuesta (#95). */
  retrieval_failed?: boolean
}

/** URL para ver el original de una fuente (los PDF se abren en la página citada). */
/** Las presentaciones numeran diapositivas en lugar de páginas. */
export const isSlides = (filename: string) => /\.(pptx|odp)$/i.test(filename)

export function sourceUrl(source: Source): string | null {
  if (!source.document_id) return null
  // Solo los PDF se abren en la página citada; los demás formatos se muestran o descargan.
  const page = source.page && /\.pdf$/i.test(source.filename) ? `#page=${source.page}` : ''
  // Los documentos añadidos por su dirección enlazan a la original.
  if (source.url) return `${source.url}${page}`
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

/** Limita la búsqueda de la conversación a esos documentos y categorías (vacíos: todos). */
export const updateConversationScope = (id: string, scope: SearchScope) =>
  apiRequest<Conversation>(`/conversations/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ document_ids: scope.documentIds, category_ids: scope.categoryIds }),
  })

export const listIndexedDocuments = () => apiGet<IndexedDocument[]>('/documents')

export const deleteConversation = (id: string) =>
  apiRequest<void>(`/conversations/${id}`, { method: 'DELETE' })

export const listMessages = (conversationId: string) =>
  apiGet<Message[]>(`/conversations/${conversationId}/messages`)
