import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { streamChat } from '../api/chat'
import { listMessages, type Source } from '../api/client'
import { t } from '../i18n/es'

export interface PendingExchange {
  question: string
  answer: string
  /** Id del mensaje del usuario ya guardado; los mensajes desde él se muestran como pendientes. */
  userMessageId: number | null
  sources: Source[]
}

interface Options {
  conversationId: string | null
  onConversationCreated: (id: string) => void
}

/** Envía mensajes y expone el intercambio en curso mientras llega la respuesta. */
export function useChat({ conversationId, onConversationCreated }: Options) {
  const queryClient = useQueryClient()
  const [pending, setPending] = useState<PendingExchange | null>(null)
  const [error, setError] = useState<string | null>(null)
  const streamingFor = useRef<string | null>(null)
  const abortRef = useRef<AbortController | null>(null)

  // Al cambiar de conversación (salvo la que se acaba de crear) se descarta el estado.
  useEffect(() => {
    if (conversationId !== streamingFor.current) {
      abortRef.current?.abort()
      setPending(null)
      setError(null)
    }
  }, [conversationId])

  useEffect(() => () => abortRef.current?.abort(), [])

  const send = useCallback(
    async (question: string) => {
      const controller = new AbortController()
      abortRef.current = controller
      streamingFor.current = conversationId
      setError(null)
      setPending({ question, answer: '', userMessageId: null, sources: [] })
      let activeId = conversationId
      try {
        for await (const event of streamChat(question, conversationId, controller.signal)) {
          if (event.event === 'conversation') {
            activeId = event.data.id
            const userMessageId = event.data.user_message_id
            setPending((p) => p && { ...p, userMessageId })
            if (activeId !== conversationId) {
              streamingFor.current = activeId
              onConversationCreated(activeId)
            }
            void queryClient.invalidateQueries({ queryKey: ['conversations'] })
          } else if (event.event === 'token') {
            const delta = event.data.content
            setPending((p) => p && { ...p, answer: p.answer + delta })
          } else if (event.event === 'sources') {
            const sources = event.data.sources
            setPending((p) => p && { ...p, sources })
          } else if (event.event === 'error') {
            setError(event.data.detail)
          }
        }
        if (activeId) {
          // Se cargan los mensajes guardados antes de retirar el intercambio en curso
          // (aunque la vista de la conversación nueva aún no se haya montado).
          const id = activeId
          await queryClient
            .fetchQuery({
              queryKey: ['messages', id],
              queryFn: () => listMessages(id),
              staleTime: 0,
            })
            .catch(() => queryClient.invalidateQueries({ queryKey: ['messages', id] }))
        }
        setPending(null)
      } catch {
        if (!controller.signal.aborted) {
          setError(t.chat.sendError)
          setPending(null)
        }
      } finally {
        streamingFor.current = null
      }
    },
    [conversationId, onConversationCreated, queryClient],
  )

  return { send, pending, error, isStreaming: pending !== null }
}
