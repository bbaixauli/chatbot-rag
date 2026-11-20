import { useEffect } from 'react'
import { t } from '../i18n/es'

/** Título de la pestaña: «Página – Aplicación» (WCAG 2.4.2). */
export function usePageTitle(page: string | null | undefined) {
  useEffect(() => {
    document.title = page ? `${page} – ${t.appName}` : t.appName
  }, [page])
}
