import { apiGet, apiRequest } from './client'

/** Grupos de la configuración editable (ver backend/app/core/runtime_config.py). */
export type ConfigGroupName =
  'llm' | 'embeddings' | 'indexing' | 'rag' | 'chat' | 'limits' | 'users' | 'audit'

/** Estado de un secreto: la API nunca devuelve su valor. */
export interface SecretState {
  configured: boolean
  /** Guardado con otra clave de sesión: hay que volver a introducirlo. */
  unreadable: boolean
}

export type ConfigValue = string | number | boolean | null | SecretState

export interface ConfigGroup {
  values: Record<string, ConfigValue>
  secret_fields: string[]
  reindex_fields: string[]
  /** `env` si los valores se copiaron de las variables de entorno. */
  updated_by: string
  updated_at: string | null
}

export interface AppConfig {
  groups: Record<ConfigGroupName, ConfigGroup>
  providers: { llm: string[]; embeddings: string[] }
}

export interface ConfigSaveResult {
  group: ConfigGroupName
  config: ConfigGroup
  requires_reindex: boolean
  warnings: string[]
}

export interface ConnectionCheck {
  ok: boolean
  detail: string
}

/**
 * Cambios de un grupo. En los secretos, un texto vacío conserva el actual;
 * `clear_secrets` los quita.
 */
export type ConfigChanges = Record<string, string | number | boolean | null | string[]>

export const APP_CONFIG_KEY = ['admin', 'config'] as const

/** Valor numérico de la configuración, o `fallback` si no está disponible. */
export function configNumber(
  config: AppConfig | undefined,
  group: ConfigGroupName,
  field: string,
  fallback: number,
): number {
  const value = config?.groups[group]?.values[field]
  return typeof value === 'number' ? value : fallback
}

export const getAppConfig = () => apiGet<AppConfig>('/admin/config')

export const saveConfigGroup = (group: ConfigGroupName, changes: ConfigChanges) =>
  apiRequest<ConfigSaveResult>(`/admin/config/${group}`, {
    method: 'PUT',
    body: JSON.stringify(changes),
  })

export const checkConnection = (group: 'llm' | 'embeddings' | 'users', changes: ConfigChanges) =>
  apiRequest<ConnectionCheck>(`/admin/config/${group}/test`, {
    method: 'POST',
    body: JSON.stringify(changes),
  })
