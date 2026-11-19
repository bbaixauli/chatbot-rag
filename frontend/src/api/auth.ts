import { apiGet, apiRequest } from './client'

export interface AuthUser {
  id: string
  display_name: string
  is_admin: boolean
}

/** Sesión del usuario del chat. Con `mode: 'none'` el chat es anónimo. */
export interface AuthSession {
  mode: 'none' | 'ldap'
  authenticated: boolean
  user: AuthUser | null
}

export const getAuthSession = () => apiGet<AuthSession>('/auth/session')

export const userLogin = (username: string, password: string) =>
  apiRequest<AuthSession>('/auth/session', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })

export const userLogout = () => apiRequest<void>('/auth/session', { method: 'DELETE' })
