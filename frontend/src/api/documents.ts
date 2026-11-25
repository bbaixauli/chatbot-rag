import { API_BASE, apiGet, apiRequest, type DocumentKind } from './client'

export type DocumentStatus = 'pending' | 'processing' | 'ready' | 'error'

export interface DocumentItem {
  id: string
  filename: string
  /** Lo que se muestra en las referencias y se pasa al indexar. */
  title: string
  category_id: number | null
  kind: DocumentKind
  size_bytes: number
  status: DocumentStatus
  chunk_count: number
  error: string | null
  embedding_model: string | null
  /** Troceado con el que se indexó (null si fue antes de guardarlo). */
  chunk_size_chars: number | null
  chunk_overlap_chars: number | null
  uploaded_by: string
  created_at: string
  updated_at: string
  /** Dirección web, si se añadió por ella. */
  source_url?: string | null
  /** Actualización periódica de los documentos web: última comprobación y último cambio. */
  source_checked_at?: string | null
  source_changed_at?: string | null
  /** Error de la última comprobación. */
  source_error?: string | null
  /** Comprobaciones seguidas en las que no se ha encontrado (se borra al llegar al límite). */
  source_not_found?: number
  /** Indexado con otro modelo, troceado, preprocesado, título o categoría. */
  outdated: boolean
}

export interface Category {
  id: number
  name: string
  /** Documentos de la categoría. */
  documents: number
}

export interface UploadOptions {
  /** Título: el nombre del fichero en lugar del de sus metadatos. */
  useFilename?: boolean
  categoryId?: number | null
}

export interface UploadResult {
  /** En un ZIP, la ruta del fichero dentro de él. */
  filename: string
  /** ZIP del que viene el fichero. */
  archive?: string | null
  document: DocumentItem | null
  duplicate: boolean
  error: string | null
}

export interface IndexStats {
  documents: number
  chunks: number
  by_status: Partial<Record<DocumentStatus, number>>
  embedding_model: string
  /** Documentos indexados con otro modelo, troceado, preprocesado, título o categoría. */
  outdated: number
}

export const ACCEPTED_EXTENSIONS = [
  '.pdf',
  '.md',
  '.markdown',
  '.html',
  '.htm',
  '.docx',
  '.pptx',
  '.xlsx',
  '.odt',
  '.odp',
  '.ods',
  '.zip',
]

export const listDocuments = () => apiGet<DocumentItem[]>('/admin/documents')

export const getIndexStats = () => apiGet<IndexStats>('/admin/documents/stats')

/** Sube un fichero: un resultado, o uno por fichero si es un ZIP. */
export async function uploadDocument(
  file: File,
  { useFilename = false, categoryId = null }: UploadOptions = {},
): Promise<UploadResult[]> {
  const body = new FormData()
  body.append('files', file, file.name)
  if (useFilename) body.append('use_filename', 'true')
  if (categoryId !== null) body.append('category_id', String(categoryId))
  return apiRequest<UploadResult[]>('/admin/documents', { method: 'POST', body })
}

/** Como mucho, direcciones que se pueden añadir de una vez (igual que el servidor). */
export const MAX_URLS = 50

/** Descarga y da de alta varias direcciones web: un resultado por dirección, en orden. */
export const addUrls = (
  urls: string[],
  { useFilename = false, categoryId = null }: UploadOptions = {},
) =>
  apiRequest<UploadResult[]>('/admin/documents/urls', {
    method: 'POST',
    body: JSON.stringify({ urls, use_filename: useFilename, category_id: categoryId }),
  })

/** Cambia el título o la categoría (`null`: sin categoría). */
export const updateDocument = (
  id: string,
  changes: { title?: string; category_id?: number | null },
) =>
  apiRequest<DocumentItem>(`/admin/documents/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(changes),
  })

export const deleteDocument = (id: string) =>
  apiRequest<void>(`/admin/documents/${id}`, { method: 'DELETE' })

export const reindexDocument = (id: string) =>
  apiRequest<DocumentItem>(`/admin/documents/${id}/reindex`, { method: 'POST' })

/** Comprueba ahora si ha cambiado un documento web. */
export const refreshDocument = (id: string) =>
  apiRequest<DocumentItem>(`/admin/documents/${id}/refresh`, { method: 'POST' })

export const reindexAll = () =>
  apiRequest<{ scheduled: number }>('/admin/documents/reindex', { method: 'POST' })

export const reindexOutdated = () =>
  apiRequest<{ scheduled: number }>('/admin/documents/reindex-outdated', { method: 'POST' })

export const listCategories = () => apiGet<Category[]>('/admin/categories')

export const createCategory = (name: string) =>
  apiRequest<Category>('/admin/categories', { method: 'POST', body: JSON.stringify({ name }) })

export const renameCategory = (id: number, name: string) =>
  apiRequest<Category>(`/admin/categories/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ name }),
  })

export const deleteCategory = (id: number) =>
  apiRequest<void>(`/admin/categories/${id}`, { method: 'DELETE' })

export const documentFileUrl = (id: string) => `${API_BASE}/admin/documents/${id}/file`
