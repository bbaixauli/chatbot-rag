import { apiGet, apiRequest } from './client'

export interface AuthUser {
  id: string
  display_name: string
  is_admin: boolean
  /** Acceso anónimo (con LDAP): sus conversaciones solo se ven durante esta sesión. */
  is_anonymous?: boolean
}

/**
 * Sesión del usuario del chat. Con `mode: 'none'` el chat es anónimo: historial compartido o,
 * con `private_sessions`, una sesión privada por visitante.
 */
export interface AuthSession {
  mode: 'none' | 'ldap'
  authenticated: boolean
  user: AuthUser | null
  /** Con LDAP, si el login ofrece entrar de forma anónima (ausente = no). */
  anonymous_allowed?: boolean
  /** Sin LDAP, si cada visitante usa su propia sesión privada (ausente = no). */
  private_sessions?: boolean
}

export const getAuthSession = () => apiGet<AuthSession>('/auth/session')

export const userLogin = (username: string, password: string) =>
  apiRequest<AuthSession>('/auth/session', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })

export const anonymousLogin = () => apiRequest<AuthSession>('/auth/anonymous', { method: 'POST' })

export const userLogout = () => apiRequest<void>('/auth/session', { method: 'DELETE' })
