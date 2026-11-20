import { useEffect } from 'react'
import { useAppearance } from './useAppearance'

/** Título de la pestaña: «Página – Aplicación – Organización» (WCAG 2.4.2; manual, 4.1). */
export function usePageTitle(page: string | null | undefined) {
  const { app_name, organization } = useAppearance()
  useEffect(() => {
    document.title = [page, app_name, organization].filter(Boolean).join(' – ')
  }, [page, app_name, organization])
}
