import { useQuery } from '@tanstack/react-query'
import { getAppearance, type PublicAppearance } from '../api/appearance'
import { t } from '../i18n/es'

export const APPEARANCE_KEY = ['appearance'] as const

/** Valores mientras se carga la apariencia o si no se puede cargar. */
export const DEFAULT_APPEARANCE: PublicAppearance = {
  app_name: t.appName,
  organization: '',
  header_style: 'clara',
  logo_url: null,
  logo_alt: '',
  footer: { unit: '', accessibility_url: '', support_text: '', support_url: '' },
  login_notice: '',
  ai_notice: '',
}

/** Nombre, logo y textos configurados en la administración. */
export function useAppearance(): PublicAppearance {
  const appearance = useQuery({
    queryKey: APPEARANCE_KEY,
    queryFn: getAppearance,
    staleTime: 5 * 60_000,
    retry: false,
  })
  return appearance.data ?? DEFAULT_APPEARANCE
}
