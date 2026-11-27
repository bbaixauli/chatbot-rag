import { API_BASE, apiGet, apiRequest } from './client'

export interface DayStats {
  day: string // AAAA-MM-DD
  sessions: number
  users: number
  questions: number
  /** Preguntas en las que no se pudo consultar la documentación (#99). */
  retrieval_failed: number
}

export interface DashboardSummary {
  start: string
  end: string
  timezone: string
  sessions: number
  users: number
  questions: number
  answered: number
  answered_ratio: number | null
  no_info: number
  errors: number
  without_sources: number
  /** Preguntas con el prompt recortado para caber en la ventana del modelo. */
  trimmed: number
  /** Preguntas en las que no se pudo consultar la documentación (#99). */
  retrieval_failed: number
  avg_response_ms: number | null
  median_response_ms: number | null
  avg_first_token_ms: number | null
  days: DayStats[]
}

export type QuestionResult = 'answered' | 'no_info' | 'error' | 'cancelled'

export interface LoggedQuestion {
  id: number
  asked_at: string // hora local con su desfase (ISO 8601)
  question: string
  answer: string | null
  result: QuestionResult
  response_ms: number | null
  first_token_ms: number | null
  sources_cited: number
}

export interface QuestionPage {
  total: number
  page: number
  size: number
  items: LoggedQuestion[]
}

export interface NoInfoPhrases {
  phrases: string[]
  defaults: string[]
}

export interface DateRange {
  from: string
  to: string
}

const rangeQuery = ({ from, to }: DateRange) => new URLSearchParams({ from, to }).toString()

export const getDashboardSummary = (range: DateRange) =>
  apiGet<DashboardSummary>(`/admin/dashboard/summary?${rangeQuery(range)}`)

export const getLoggedQuestions = (range: DateRange, page: number, size: number) =>
  apiGet<QuestionPage>(`/admin/dashboard/questions?${rangeQuery(range)}&page=${page}&size=${size}`)

/** Enlace de descarga (la sesión de administración va en la cookie). */
export const exportUrl = (range: DateRange, format: 'csv' | 'xlsx') =>
  `${API_BASE}/admin/dashboard/questions.${format}?${rangeQuery(range)}`

export const getNoInfoPhrases = () => apiGet<NoInfoPhrases>('/admin/dashboard/no-info-phrases')

export const saveNoInfoPhrases = (phrases: string[]) =>
  apiRequest<NoInfoPhrases>('/admin/dashboard/no-info-phrases', {
    method: 'PUT',
    body: JSON.stringify({ phrases }),
  })
