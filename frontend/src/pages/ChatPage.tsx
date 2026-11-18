import { useQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useRef } from 'react'
import { useNavigate, useParams } from 'react-router'
import { ApiError, getChatConfig, listMessages } from '../api/client'
import { Composer } from '../components/Composer'
import { MessageBubble } from '../components/MessageBubble'
import { useChat } from '../hooks/useChat'
import { t } from '../i18n/es'

export function ChatPage() {
  const { conversationId = null } = useParams()
  const navigate = useNavigate()
  const bottomRef = useRef<HTMLDivElement>(null)

  const messages = useQuery({
    queryKey: ['messages', conversationId],
    queryFn: () => listMessages(conversationId!),
    enabled: conversationId !== null,
  })

  // Si no se puede cargar, el chat funciona igual sin mensaje inicial ni sugerencias.
  const chatConfig = useQuery({
    queryKey: ['chat-config'],
    queryFn: getChatConfig,
    staleTime: 60_000,
  })
  const welcome = chatConfig.data?.welcome_message ?? null

  const onConversationCreated = useCallback((id: string) => navigate(`/c/${id}`), [navigate])
  const { send, pending, error, isStreaming } = useChat({ conversationId, onConversationCreated })

  const pendingFrom = pending?.userMessageId
  const history = (conversationId ? (messages.data ?? []) : []).filter(
    (m) => !pending || pendingFrom == null || m.id < pendingFrom,
  )

  useEffect(() => {
    bottomRef.current?.scrollIntoView?.({ block: 'end' })
  }, [history.length, pending?.answer])

  const notFound = messages.error instanceof ApiError && messages.error.status === 404
  const isEmpty = history.length === 0 && !pending && !messages.isLoading
  const suggestions = isEmpty && !notFound ? (chatConfig.data?.suggested_questions ?? []) : []

  return (
    <section className="flex min-h-0 flex-1 flex-col">
      <div className="flex-1 overflow-y-auto p-6">
        {notFound ? (
          <p role="alert" className="text-center text-red-700">
            {t.chat.notFound}
          </p>
        ) : messages.isError ? (
          <p role="alert" className="text-center text-red-700">
            {t.chat.loadError}
          </p>
        ) : isEmpty && !welcome ? (
          <div className="mt-24 text-center">
            <h2 className="text-2xl font-semibold">{t.chat.emptyTitle}</h2>
            <p className="mt-2 text-gray-600">{t.chat.emptyHint}</p>
          </div>
        ) : (
          <ol aria-label={t.chat.messages} className="mx-auto flex max-w-3xl flex-col gap-4">
            {welcome && !messages.isLoading && <MessageBubble role="assistant" content={welcome} />}
            {history.map((m) => (
              <MessageBubble key={m.id} role={m.role} content={m.content} sources={m.sources} />
            ))}
            {pending && (
              <>
                <MessageBubble role="user" content={pending.question} />
                <MessageBubble
                  role="assistant"
                  content={pending.answer}
                  sources={pending.sources}
                  streaming
                />
              </>
            )}
          </ol>
        )}
        {error && (
          <p role="alert" className="mx-auto mt-4 max-w-3xl text-center text-red-700">
            {error}
          </p>
        )}
        <div ref={bottomRef} />
      </div>
      <Composer
        disabled={isStreaming || notFound}
        onSend={(text) => void send(text)}
        suggestions={suggestions}
      />
    </section>
  )
}
