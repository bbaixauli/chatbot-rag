import { isSlides, sourceUrl, type Source } from '../api/client'
import { t } from '../i18n/es'

/**
 * «documento · pág. N» (o «diap. N»), o «documento — sección», con el título del documento.
 * En las páginas con apartado (PDF, #74) o diapositivas con título, se añade el último:
 * «documento · pág. N — 4.2 Altas».
 */
export function sourceLabel(source: Source): string {
  const name = source.title || source.filename
  if (source.page) {
    const location = isSlides(source.filename) ? t.chat.slide : t.chat.page
    const section = source.heading?.split(' > ').at(-1)
    return `${name} · ${location(source.page)}${section ? ` — ${section}` : ''}`
  }
  // La sección no se repite si es el propio título.
  if (source.heading && source.heading !== name) return `${name} — ${source.heading}`
  return name
}

const CITATION = /\[(\d+)\](?!\()/g

/**
 * Convierte las citas `[n]` de la respuesta en enlaces Markdown al documento citado.
 * Las citas sin fuente conocida (o de documentos eliminados) se dejan como texto.
 */
export function linkCitations(markdown: string, sources: Source[]): string {
  if (!sources.length) return markdown
  const byNumber = new Map(sources.map((s) => [s.number, s]))
  return markdown.replace(CITATION, (match, n: string) => {
    const source = byNumber.get(Number(n))
    const url = source && sourceUrl(source)
    return url ? `[\\[${n}\\]](${url} "${sourceLabel(source).replaceAll('"', "'")}")` : match
  })
}
