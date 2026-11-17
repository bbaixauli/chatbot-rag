import { API_BASE, apiGet, apiRequest } from './client'

export type DocumentStatus = 'pending' | 'processing' | 'ready' | 'error'

export interface DocumentItem {
  id: string
  filename: string
  kind: 'pdf' | 'markdown' | 'html'
  size_bytes: number
  status: DocumentStatus
  chunk_count: number
  error: string | null
  embedding_model: string | null
  uploaded_by: string
  created_at: string
  updated_at: string
}

export interface UploadResult {
  filename: string
  document: DocumentItem | null
  duplicate: boolean
  error: string | null
}

export interface IndexStats {
  documents: number
  chunks: number
  by_status: Partial<Record<DocumentStatus, number>>
  embedding_model: string
}

export const ACCEPTED_EXTENSIONS = ['.pdf', '.md', '.markdown', '.html', '.htm']

export const listDocuments = () => apiGet<DocumentItem[]>('/admin/documents')

export const getIndexStats = () => apiGet<IndexStats>('/admin/documents/stats')

export async function uploadDocument(file: File): Promise<UploadResult> {
  const body = new FormData()
  body.append('files', file, file.name)
  const [result] = await apiRequest<UploadResult[]>('/admin/documents', { method: 'POST', body })
  return result
}

export const deleteDocument = (id: string) =>
  apiRequest<void>(`/admin/documents/${id}`, { method: 'DELETE' })

export const reindexDocument = (id: string) =>
  apiRequest<DocumentItem>(`/admin/documents/${id}/reindex`, { method: 'POST' })

export const reindexAll = () =>
  apiRequest<{ scheduled: number }>('/admin/documents/reindex', { method: 'POST' })

export const documentFileUrl = (id: string) => `${API_BASE}/admin/documents/${id}/file`
