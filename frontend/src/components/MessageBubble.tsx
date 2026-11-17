import type { ComponentProps } from 'react'
import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { sourceUrl, type Source } from '../api/client'
import { t } from '../i18n/es'
import { linkCitations, sourceLabel } from '../utils/citations'

interface Props {
  role: 'user' | 'assistant'
  content: string
  sources?: Source[]
  streaming?: boolean
}

// Los enlaces de la respuesta (incluidas las citas) se abren en otra pestaña.
function ExternalLink({ node: _node, ...props }: ComponentProps<'a'> & { node?: unknown }) {
  return <a {...props} target="_blank" rel="noopener noreferrer" />
}

export function MessageBubble({ role, content, sources = [], streaming = false }: Props) {
  const isUser = role === 'user'
  return (
    <li
      data-role={role}
      aria-busy={streaming}
      className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}
    >
      <div
        className={`max-w-[80%] rounded-2xl px-4 py-2 ${
          isUser ? 'bg-blue-600 text-white' : 'border border-gray-200 bg-white text-gray-900'
        }`}
      >
        <span className="sr-only">{isUser ? t.chat.you : t.chat.assistant}: </span>
        {isUser ? (
          <p className="whitespace-pre-wrap">{content}</p>
        ) : content ? (
          <div className="prose prose-sm max-w-none [&_a]:text-blue-700 [&_a]:hover:underline [&_ol]:list-decimal [&_ol]:pl-5 [&_pre]:overflow-x-auto [&_ul]:list-disc [&_ul]:pl-5">
            <Markdown remarkPlugins={[remarkGfm]} components={{ a: ExternalLink }}>
              {linkCitations(content, sources)}
            </Markdown>
          </div>
        ) : (
          <p className="text-gray-500 italic">{t.chat.thinking}</p>
        )}
        {!isUser && sources.length > 0 && <SourceList sources={sources} />}
      </div>
    </li>
  )
}

function SourceList({ sources }: { sources: Source[] }) {
  const title = sources.some((s) => s.cited) ? t.chat.sources : t.chat.consultedSources
  return (
    <section aria-label={title} className="mt-3 border-t border-gray-100 pt-2">
      <h4 className="mb-1 text-xs font-semibold text-gray-500 uppercase">{title}</h4>
      <ul className="flex flex-wrap gap-2">
        {sources.map((source) => {
          const url = sourceUrl(source)
          const label = sourceLabel(source)
          const chip = (
            <>
              <span className="font-semibold">[{source.number}]</span> 📄 {label}
            </>
          )
          return (
            <li key={source.number} className="text-xs">
              {url ? (
                <a
                  href={url}
                  target="_blank"
                  rel="noopener noreferrer"
                  title={source.snippet}
                  aria-label={t.chat.openSource(`[${source.number}] ${label}`)}
                  className="inline-block rounded-full border border-gray-200 bg-gray-50 px-2 py-1 text-gray-700 hover:border-blue-300 hover:bg-blue-50"
                >
                  {chip}
                </a>
              ) : (
                <span
                  title={source.snippet}
                  className="inline-block rounded-full border border-dashed border-gray-200 px-2 py-1 text-gray-500"
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
