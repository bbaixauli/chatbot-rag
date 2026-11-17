export const API_BASE = '/api/v1'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export async function apiGet<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { Accept: 'application/json', ...init?.headers },
  })
  if (!response.ok) {
    throw new ApiError(response.status, `GET ${path} → ${response.status}`)
  }
  return (await response.json()) as T
}

export interface HealthResponse {
  status: 'ok' | 'degraded'
  app: string
  version: string
  database: 'ok' | 'error'
}

export const getHealth = (signal?: AbortSignal) => apiGet<HealthResponse>('/health', { signal })
