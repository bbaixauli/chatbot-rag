import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router'
import {
  ALL_DOCUMENTS,
  ApiError,
  conversationScope,
  getChatConfig,
  isLimited,
  listConversations,
  listMessages,
  updateConversationScope,
  type Conversation,
  type SearchScope,
} from '../api/client'
import { Composer } from '../components/Composer'
import { MessageBubble } from '../components/MessageBubble'
import { ScopePicker } from '../components/ScopePicker'
import { useChat } from '../hooks/useChat'
import { useAppearance } from '../hooks/useAppearance'
import { usePageTitle } from '../hooks/usePageTitle'
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

  // Un único h1 por página: el título de la conversación (o «Nueva conversación»).
  const conversations = useQuery({ queryKey: ['conversations'], queryFn: listConversations })
  const conversation = conversations.data?.find((c) => c.id === conversationId)
  const title = conversationId
    ? (conversation?.title ?? t.chat.conversation)
    : t.chat.newConversation

  // En qué documentos buscar: en una conversación, lo guardado en ella; en una nueva, lo
  // elegido hasta enviar la primera pregunta (que lo guarda).
  const queryClient = useQueryClient()
  const [draftScope, setDraftScope] = useState<SearchScope>(ALL_DOCUMENTS)
  // Al volver a «Nueva conversación» se empieza buscando en todos.
  const [shownConversation, setShownConversation] = useState(conversationId)
  if (conversationId !== shownConversation) {
    setShownConversation(conversationId)
    if (conversationId === null) setDraftScope(ALL_DOCUMENTS)
  }
  const scope = conversationId ? conversationScope(conversation) : draftScope
  const saveScope = useMutation({
    mutationFn: ({ id, scope }: { id: string; scope: SearchScope }) =>
      updateConversationScope(id, scope),
    onMutate: ({ id, scope }) => {
      queryClient.setQueryData<Conversation[]>(['conversations'], (list) =>
        list?.map((c) =>
          c.id === id
            ? { ...c, document_ids: scope.documentIds, category_ids: scope.categoryIds }
            : c,
        ),
      )
    },
    onError: () => void queryClient.invalidateQueries({ queryKey: ['conversations'] }),
  })
  const changeScope = (scope: SearchScope) => {
    if (conversationId) saveScope.mutate({ id: conversationId, scope })
    else setDraftScope(scope)
  }
  usePageTitle(title)
  const { ai_notice: aiNotice } = useAppearance()

  const notFound = messages.error instanceof ApiError && messages.error.status === 404
  const isEmpty = history.length === 0 && !pending && !messages.isLoading
  const suggestions = isEmpty && !notFound ? (chatConfig.data?.suggested_questions ?? []) : []

  return (
    <section className="flex min-h-0 flex-1 flex-col">
      <h1 className="sr-only">{title}</h1>
      <div className="flex-1 overflow-y-auto p-4 md:p-6">
        {notFound ? (
          <p role="alert" className="text-center text-error">
            {t.chat.notFound}
          </p>
        ) : messages.isError ? (
          <p role="alert" className="text-center text-error">
            {t.chat.loadError}
          </p>
        ) : isEmpty && !welcome ? (
          <div className="mt-12 text-center md:mt-24">
            <h2 className="text-2xl font-semibold">{t.chat.emptyTitle}</h2>
            <p className="mt-2 text-texto-secundario">{t.chat.emptyHint}</p>
          </div>
        ) : (
          <ol aria-label={t.chat.messages} className="mx-auto flex max-w-3xl flex-col gap-4">
            {welcome && !messages.isLoading && <MessageBubble role="assistant" content={welcome} />}
            {history.map((m) => (
              <MessageBubble
                key={m.id}
                role={m.role}
                content={m.content}
                sources={m.sources}
                scope={m.scope}
                retrievalFailed={m.retrieval_failed}
              />
            ))}
            {pending && (
              <>
                <MessageBubble role="user" content={pending.question} />
                <MessageBubble
                  role="assistant"
                  content={pending.answer}
                  sources={pending.sources}
                  scope={pending.scope}
                  retrievalFailed={pending.retrievalFailed}
                  streaming
                />
              </>
            )}
          </ol>
        )}
        {error && (
          <p role="alert" className="mx-auto mt-4 max-w-3xl text-center text-error">
            {error}
          </p>
        )}
        <div ref={bottomRef} />
      </div>
      <Composer
        disabled={isStreaming || notFound}
        onSend={(text) =>
          // En una conversación nueva se envía lo elegido; en una existente ya está guardado.
          void send(text, !conversationId && isLimited(draftScope) ? draftScope : undefined)
        }
        suggestions={suggestions}
        aiNotice={aiNotice}
        toolbar={
          !notFound && (
            <ScopePicker
              selected={scope}
              onChange={changeScope}
              disabled={isStreaming || saveScope.isPending}
              error={saveScope.isError ? t.chat.scope.saveError : null}
            />
          )
        }
      />
    </section>
  )
}
