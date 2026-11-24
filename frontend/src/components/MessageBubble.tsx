import { FileText, Files } from 'lucide-react'
import type { ComponentProps } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { sourceUrl, type Scope, type Source } from '../api/client'
import { t } from '../i18n/es'
import { linkCitations, sourceLabel } from '../utils/citations'

interface Props {
  role: 'user' | 'assistant'
  content: string
  sources?: Source[]
  /** Documentos a los que se limitó la búsqueda de la respuesta. */
  scope?: Scope | null
  streaming?: boolean
}

// Los enlaces de la respuesta (incluidas las citas) se abren en otra pestaña.
function ExternalLink({ node: _node, ...props }: ComponentProps<'a'> & { node?: unknown }) {
  return <a {...props} target="_blank" rel="noopener noreferrer" />
}

export function MessageBubble({
  role,
  content,
  sources = [],
  scope = null,
  streaming = false,
}: Props) {
  const isUser = role === 'user'
  return (
    <li
      data-role={role}
      aria-busy={streaming}
      className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}
    >
      <div
        className={`max-w-[90%] min-w-0 rounded-lg px-4 py-2 break-words md:max-w-[80%] ${
          isUser ? 'bg-marca text-sobre-marca' : 'border border-borde bg-fondo text-texto'
        }`}
      >
        <span className="sr-only">{isUser ? t.chat.you : t.chat.assistant}: </span>
        {isUser ? (
          <p className="whitespace-pre-wrap">{content}</p>
        ) : content ? (
          <div className="max-w-none [&_code]:font-mono [&_ol]:list-decimal [&_ol]:pl-5 [&_pre]:overflow-x-auto [&_pre]:font-mono [&_ul]:list-disc [&_ul]:pl-5">
            <Markdown remarkPlugins={[remarkGfm]} components={{ a: ExternalLink }}>
              {linkCitations(content, sources)}
            </Markdown>
          </div>
        ) : (
          <p className="text-texto-secundario italic">{t.chat.thinking}</p>
        )}
        {!isUser && scope && scope.documents.length > 0 && <ScopeNote scope={scope} />}
        {!isUser && sources.length > 0 && <SourceList sources={sources} />}
      </div>
    </li>
  )
}

function ScopeNote({ scope }: { scope: Scope }) {
  const names = scope.documents.map((d) => d.filename).join(', ')
  return (
    <p className="mt-2 text-xs text-texto-secundario">
      <Files aria-hidden size={14} className="inline align-[-2px]" /> {t.chat.scope.searchedIn}:{' '}
      {names}
      {scope.source === 'detected' && ` ${t.chat.scope.detected}`}
    </p>
  )
}

function SourceList({ sources }: { sources: Source[] }) {
  const title = sources.some((s) => s.cited) ? t.chat.sources : t.chat.consultedSources
  return (
    <section aria-label={title} className="mt-3 border-t border-borde pt-2">
      <h3 className="mb-1 text-xs font-semibold text-texto-secundario">{title}</h3>
      <ul className="flex flex-wrap gap-2">
        {sources.map((source) => {
          const url = sourceUrl(source)
          const label = sourceLabel(source)
          const chip = (
            <>
              <span className="font-semibold">[{source.number}]</span>{' '}
              <FileText aria-hidden size={14} className="inline align-[-2px]" /> {label}
            </>
          )
          return (
            <li key={source.number} className="max-w-full text-xs">
              {url ? (
                <a
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={source.snippet}
                  aria-label={t.chat.openSource(`[${source.number}] ${label}`)}
                  className="inline-block max-w-full rounded border border-borde-control bg-fondo-alt px-2 py-1 break-words hover:bg-marca-suave"
                >
                  {chip}
                </a>
              ) : (
                <span
                  title={source.snippet}
                  className="inline-block max-w-full rounded border border-dashed border-borde-control px-2 py-1 break-words text-texto-secundario"
                >
                  {chip} ({t.chat.deletedDocument})
                </span>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
