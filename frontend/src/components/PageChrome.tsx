import { useQuery } from '@tanstack/react-query'
import { getHealth } from '../api/client'
import { t } from '../i18n/es'

export const MAIN_ID = 'contenido'

/** Primer elemento enfocable: salta la cabecera y la navegación (WCAG 2.4.1). */
export function SkipLink() {
  return (
    <a
      href={`#${MAIN_ID}`}
      className="sr-only z-50 bg-fondo px-4 py-2 font-semibold focus:not-sr-only focus:absolute focus:top-2 focus:left-2"
    >
      {t.a11y.skipToContent}
    </a>
  )
}

/** Pie de página: aplicación y versión (y, si se configuran, más datos del servicio). */
export function AppFooter() {
  const health = useQuery({
    queryKey: ['health'],
    queryFn: ({ signal }) => getHealth(signal),
    staleTime: Infinity,
    retry: false,
  })
  return (
    <footer className="border-t border-borde bg-fondo px-4 py-2 text-xs text-texto-secundario md:px-6">
      {t.appName}
      {health.data && ` · ${t.footer.version(health.data.version)}`}
    </footer>
  )
}
