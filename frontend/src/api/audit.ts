import { API_BASE, apiGet, apiRequest } from './client'

export type AlertStatus = 'new' | 'reviewed' | 'dismissed'
export type AlertCategory =
  'malicious_user' | 'inappropriate_response' | 'sensitive_information' | 'retrieval_failure'
export type Severity = 'low' | 'medium' | 'high'

export interface AuditRun {
  id: number
  started_at: string
  finished_at: string | null
  trigger: 'scheduled' | 'manual' | 'cli'
  triggered_by: string | null
  status: 'running' | 'ok' | 'partial' | 'failed'
  reviewed: number
  reviewed_by_model: number
  model_errors: number
  alerts: number
  anonymized: number
  used_model: string | null
  error: string | null
  notification_error: string | null
}

export interface AuditSummary {
  new_alerts: number
  new_by_severity: Record<Severity, number>
  running: AuditRun | null
  last_run: AuditRun | null
}

export interface AuditEntry {
  id: number
  asked_at: string
  user_id: string | null
  user_display_name: string | null
  access_kind: string
  client_ip: string | null
  user_agent: string | null
  question: string
  answer: string | null
  status: string
  response_ms: number | null
  model: string | null
  prompt_version_id: number | null
  search_query: string | null
  cited_documents:
    | {
        document_id: string | null
        filename: string
        /** Título del documento al responder (no está en las preguntas anteriores a #75). */
        title?: string
        page: number | null
        cited: boolean
      }[]
    | null
  error_detail: string | null
  /** Tamaño del prompt y recortes para caber en la ventana del modelo (#73). */
  prompt_tokens_estimated?: number | null
  prompt_tokens?: number | null
  prompt_trim?: PromptTrim | null
  /** Falló la búsqueda en la documentación (#95). */
  retrieval_failed?: boolean
  /** Dónde se buscó, si se limitó la búsqueda (#58, #85). */
  searched_in?: SearchedIn | null
}

/** Documentos o categorías a los que se limitó la búsqueda de una pregunta. */
export interface SearchedIn {
  source: 'selector' | 'detected'
  categories: string[]
  /** Títulos de los documentos (vacío si se eligieron categorías). */
  documents: string[]
  document_count: number
}

/** Lo que se quitó del prompt para que cupiera en la ventana del modelo. */
export interface PromptTrim {
  full_document?: boolean
  sources?: number
  history?: number
  server?: boolean
  /** El servidor contó más tokens que el límite: la estimación se quedó corta (#101). */
  underestimated?: boolean
}

export interface AuditAlert {
  id: number
  created_at: string
  run_id: number
  category: AlertCategory
  severity: Severity
  source: 'rule' | 'model'
  reason: string
  excerpt: string | null
  status: AlertStatus
  reviewed_by: string | null
  reviewed_at: string | null
  note: string | null
  entry: AuditEntry
}

export interface Page<T> {
  total: number
  page: number
  size: number
  items: T[]
}

export interface AlertFilters {
  status?: AlertStatus
  category?: AlertCategory
  severity?: Severity
}

export interface LogFilters {
  from: string
  to: string
  user?: string
  ip?: string
}

const query = (params: Record<string, string | number | undefined>) =>
  new URLSearchParams(
    Object.entries(params)
      .filter((e): e is [string, string | number] => e[1] !== undefined && e[1] !== '')
      .map(([k, v]) => [k, String(v)]),
  ).toString()

export const getAuditSummary = () => apiGet<AuditSummary>('/admin/audit/summary')

export const listAuditRuns = () => apiGet<AuditRun[]>('/admin/audit/runs')

export const startAuditRun = () => apiRequest<AuditRun>('/admin/audit/runs', { method: 'POST' })

export const listAlerts = (filters: AlertFilters, page: number, size: number) =>
  apiGet<Page<AuditAlert>>(`/admin/audit/alerts?${query({ ...filters, page, size })}`)

export const updateAlert = (id: number, status: AlertStatus, note: string | null) =>
  apiRequest<AuditAlert>(`/admin/audit/alerts/${id}`, {
    method: 'PATCH',
    body: JSON.stringify({ status, note }),
  })

export const listAuditLog = (filters: LogFilters, page: number, size: number) =>
  apiGet<Page<AuditEntry>>(`/admin/audit/log?${query({ ...filters, page, size })}`)

export const auditLogExportUrl = (filters: LogFilters, format: 'csv' | 'xlsx') =>
  `${API_BASE}/admin/audit/log.${format}?${query({ ...filters })}`

export const sendTestEmail = () => apiRequest<void>('/admin/audit/test-email', { method: 'POST' })
