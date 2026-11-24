import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { streamChat } from '../api/chat'
import { ApiError, listMessages, type Scope, type Source } from '../api/client'
import { t } from '../i18n/es'

export interface PendingExchange {
  question: string
  answer: string
  /** Id del mensaje del usuario ya guardado; los mensajes desde él se muestran como pendientes. */
  userMessageId: number | null
  sources: Source[]
  /** Documentos a los que se limitó la búsqueda, si se limitó. */
  scope: Scope | null
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
    async (question: string, documentIds?: string[]) => {
      const controller = new AbortController()
      abortRef.current = controller
      streamingFor.current = conversationId
      setError(null)
      setPending({ question, answer: '', userMessageId: null, sources: [], scope: null })
      let activeId = conversationId
      let finished = false
      try {
        const events = streamChat(question, conversationId, controller.signal, documentIds)
        for await (const event of events) {
          if (event.event === 'conversation') {
            activeId = event.data.id
            const userMessageId = event.data.user_message_id
            setPending((p) => p && { ...p, userMessageId })
            if (activeId !== conversationId) {
              streamingFor.current = activeId
              onConversationCreated(activeId)
            }
            void queryClient.invalidateQueries({ queryKey: ['conversations'] })
          } else if (event.event === 'scope') {
            const scope = event.data
            setPending((p) => p && { ...p, scope })
          } else if (event.event === 'token') {
            const delta = event.data.content
            setPending((p) => p && { ...p, answer: p.answer + delta })
          } else if (event.event === 'sources') {
            const sources = event.data.sources
            setPending((p) => p && { ...p, sources })
          } else if (event.event === 'error') {
            finished = true
            setError(event.data.detail)
          } else if (event.event === 'done') {
            finished = true
          }
        }
        // La conexión se cortó sin `done` ni `error` (p. ej. fallo del servidor).
        if (!finished) setError(t.chat.sendError)
        if (activeId) {
          // Se cargan los mensajes guardados antes de retirar el intercambio en curso
          // (aunque la vista de la conversación nueva aún no se haya montado).
          // Antes se cancela cualquier carga en curso: pudo empezar antes de que se guardara
          // la respuesta y fetchQuery la reutilizaría.
          const id = activeId
          await queryClient.cancelQueries({ queryKey: ['messages', id] })
          await queryClient
            .fetchQuery({
              queryKey: ['messages', id],
              queryFn: () => listMessages(id),
              staleTime: 0,
            })
            .catch(() => queryClient.invalidateQueries({ queryKey: ['messages', id] }))
        }
        setPending(null)
      } catch (err) {
        if (!controller.signal.aborted) {
          const limited = err instanceof ApiError && err.status === 429
          setError(limited ? t.chat.rateLimited : t.chat.sendError)
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
