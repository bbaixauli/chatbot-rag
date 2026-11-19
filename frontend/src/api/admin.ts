import { apiGet, apiRequest, type ChatConfig } from './client'

export interface AdminSession {
  authenticated: boolean
  username: string | null
  enabled: boolean
}

export interface SystemPrompt {
  content: string
  version_id: number | null
  updated_at: string | null
  is_default: boolean
}

export interface PromptVersion {
  id: number
  content: string
  created_by: string
  created_at: string
}

export const getAdminSession = () => apiGet<AdminSession>('/admin/session')

export const login = (username: string, password: string) =>
  apiRequest<AdminSession>('/admin/session', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })

export const logout = () => apiRequest<void>('/admin/session', { method: 'DELETE' })

export const getPrompt = () => apiGet<SystemPrompt>('/admin/prompt')

export const savePrompt = (content: string) =>
  apiRequest<SystemPrompt>('/admin/prompt', { method: 'PUT', body: JSON.stringify({ content }) })

export const listPromptVersions = () => apiGet<PromptVersion[]>('/admin/prompt/versions')

export const restorePromptVersion = (id: number) =>
  apiRequest<SystemPrompt>(`/admin/prompt/versions/${id}/restore`, { method: 'POST' })

export interface AccessConfig {
  anonymous_allowed: boolean
  /** Solo con LDAP: sin LDAP el chat ya es anónimo y la opción no se ofrece. */
  available: boolean
}

export const getAccessConfig = () => apiGet<AccessConfig>('/admin/access')

export const saveAccessConfig = (anonymous_allowed: boolean) =>
  apiRequest<AccessConfig>('/admin/access', {
    method: 'PUT',
    body: JSON.stringify({ anonymous_allowed }),
  })

export const getAdminChatConfig = () => apiGet<ChatConfig>('/admin/chat-config')

export const saveChatConfig = (config: ChatConfig) =>
  apiRequest<ChatConfig>('/admin/chat-config', { method: 'PUT', body: JSON.stringify(config) })
