import { API_BASE, ApiError, apiGet, apiRequest } from './client'

export type HeaderStyle = 'clara' | 'marca'

export interface FooterConfig {
  unit: string
  accessibility_url: string
  support_text: string
  support_url: string
}

/** Datos públicos de la apariencia (también en la pantalla de inicio de sesión). */
export interface PublicAppearance {
  app_name: string
  organization: string
  header_style: HeaderStyle
  logo_url: string | null
  logo_alt: string
  footer: FooterConfig
  login_notice: string
  ai_notice: string
}

/** Configuración completa que edita la administración. */
export interface AppearanceInput {
  app_name: string
  organization: string
  header_style: HeaderStyle
  colors: Record<string, string>
  font_sans: string
  font_mono: string
  logo_alt: string
  footer: FooterConfig
  login_notice: string
  ai_notice: string
}

export interface ContrastWarning {
  foreground: string
  background: string
  ratio: number
  required: number
  usage: string
}

export interface AdminAppearance extends AppearanceInput {
  logo_url: string | null
  warnings: ContrastWarning[]
  sans_fonts: Record<string, string>
  mono_fonts: Record<string, string>
}

export const STYLESHEET_PATH = `${API_BASE}/appearance.css`

export const getAppearance = () => apiGet<PublicAppearance>('/appearance')

export const getAdminAppearance = () => apiGet<AdminAppearance>('/admin/appearance')

export const saveAppearance = (input: AppearanceInput) =>
  apiRequest<AdminAppearance>('/admin/appearance', {
    method: 'PUT',
    body: JSON.stringify(input),
  })

export const resetAppearance = () =>
  apiRequest<AdminAppearance>('/admin/appearance', { method: 'DELETE' })

export const deleteLogo = () =>
  apiRequest<AdminAppearance>('/admin/appearance/logo', { method: 'DELETE' })

/** Sube el logo. Los errores de validación (422) traen un mensaje para mostrar. */
export async function uploadLogo(file: File): Promise<AdminAppearance> {
  const body = new FormData()
  body.append('file', file)
  const response = await fetch(`${API_BASE}/admin/appearance/logo`, { method: 'PUT', body })
  if (!response.ok) {
    const detail = await response
      .json()
      .then((data: { detail?: unknown }) => (typeof data.detail === 'string' ? data.detail : null))
      .catch(() => null)
    throw new ApiError(response.status, detail ?? `PUT /admin/appearance/logo → ${response.status}`)
  }
  return (await response.json()) as AdminAppearance
}

/** Vuelve a cargar la hoja de estilos de la apariencia (tras guardarla). */
export function reloadStylesheet() {
  const link = document.querySelector<HTMLLinkElement>('link[data-appearance]')
  if (link) link.href = `${STYLESHEET_PATH}?v=${Date.now()}`
}
