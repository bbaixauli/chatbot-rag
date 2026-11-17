import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { t } from '../i18n/es'

interface Props {
  role: 'user' | 'assistant'
  content: string
  streaming?: boolean
}

export function MessageBubble({ role, content, streaming = false }: Props) {
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
          <div className="prose prose-sm max-w-none [&_pre]:overflow-x-auto [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:list-decimal [&_ol]:pl-5">
            <Markdown remarkPlugins={[remarkGfm]}>{content}</Markdown>
          </div>
        ) : (
          <p className="text-gray-500 italic">{t.chat.thinking}</p>
        )}
      </div>
    </li>
  )
}
