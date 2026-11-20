import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router'
import { getHealth } from '../api/client'
import { useAppearance } from '../hooks/useAppearance'
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

/**
 * Identificador de la cabecera: logo (si lo hay), separador vertical y nombre de la
 * aplicación (manual de estilos, 2.6). El logo debería medir al menos 40 px de alto.
 */
export function Brand({ section }: { section?: string }) {
  const appearance = useAppearance()
  return (
    <Link to="/" className="flex min-w-0 items-center gap-3 text-sobre-cabecera no-underline">
      {appearance.logo_url && (
        <>
          <img src={appearance.logo_url} alt={appearance.logo_alt} className="h-10 w-auto" />
          <span aria-hidden className="h-8 w-px shrink-0 bg-current opacity-60" />
        </>
      )}
      <span className="text-lg font-semibold">
        {appearance.app_name}
        {section && ` · ${section}`}
      </span>
    </Link>
  )
}

/** Pie: aplicación, versión y, si se configuran, unidad, accesibilidad y soporte (4.1). */
export function AppFooter() {
  const appearance = useAppearance()
  const health = useQuery({
    queryKey: ['health'],
    queryFn: ({ signal }) => getHealth(signal),
    staleTime: Infinity,
    retry: false,
  })
  const { unit, accessibility_url, support_text, support_url } = appearance.footer
  const items = [
    <span key="app">
      {appearance.app_name}
      {health.data && ` · ${t.footer.version(health.data.version)}`}
    </span>,
    unit && <span key="unit">{unit}</span>,
    accessibility_url && (
      <a key="a11y" href={accessibility_url} className="text-texto-secundario">
        {t.footer.accessibility}
      </a>
    ),
    support_url ? (
      <a key="support" href={support_url} className="text-texto-secundario">
        {support_text || t.footer.support}
      </a>
    ) : (
      support_text && <span key="support">{support_text}</span>
    ),
  ].filter(Boolean)
  return (
    <footer className="flex flex-wrap gap-x-4 gap-y-1 border-t border-borde bg-fondo px-4 py-2 text-xs text-texto-secundario md:px-6">
      {items}
    </footer>
  )
}
