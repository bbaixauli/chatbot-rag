import { sourceUrl, type Source } from '../api/client'
import { t } from '../i18n/es'

/** «fichero · pág. N» o «fichero — sección». */
export function sourceLabel(source: Source): string {
  if (source.page) return `${source.filename} · ${t.chat.page(source.page)}`
  if (source.heading) return `${source.filename} — ${source.heading}`
  return source.filename
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
