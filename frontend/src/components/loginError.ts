import { t } from '../i18n/es'

/** Mensaje de error de un inicio de sesión según el estado HTTP. */
export function loginErrorMessage(status: number | null, failed: boolean): string | null {
  if (!failed) return null
  if (status === 401) return t.login.invalid
  if (status === 403) return t.login.forbidden
  if (status === 429) return t.login.tooManyAttempts
  if (status === 503) return t.login.unavailable
  return t.login.error
}
