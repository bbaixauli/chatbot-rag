import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { getAdminChatConfig, saveChatConfig } from '../../api/admin'
import { ApiError, type ChatConfig } from '../../api/client'
import { t } from '../../i18n/es'
import { ADMIN_SESSION_KEY } from './session'

const CONFIG_KEY = ['admin', 'chat-config'] as const
// Deben coincidir con los límites del servidor (CHATBOT_CHAT_*).
const MAX_QUESTIONS = 5
const WELCOME_MAX_LENGTH = 2000
const QUESTION_MAX_LENGTH = 300

/** Administración del inicio del chat: mensaje de bienvenida y preguntas sugeridas. */
export function ChatStartPage() {
  const queryClient = useQueryClient()
  const config = useQuery({ queryKey: CONFIG_KEY, queryFn: getAdminChatConfig })

  // Si la sesión ha caducado, se vuelve a comprobar y el guard redirige al login.
  const unauthorized = config.error instanceof ApiError && config.error.status === 401
  useEffect(() => {
    if (unauthorized) void queryClient.invalidateQueries({ queryKey: ADMIN_SESSION_KEY })
  }, [unauthorized, queryClient])

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <div>
        <h2 className="text-xl font-semibold">{t.chatStart.title}</h2>
        <p className="text-sm text-gray-600">{t.chatStart.description}</p>
      </div>
      {config.isPending ? (
        <p className="text-gray-600">{t.common.loading}</p>
      ) : config.isError ? (
        <p role="alert" className="text-red-700">
          {t.chatStart.loadError}
        </p>
      ) : (
        <ChatStartForm
          initial={config.data}
          onSaved={(data) => {
            queryClient.setQueryData(CONFIG_KEY, data)
            // El chat vuelve a leer la configuración pública.
            void queryClient.invalidateQueries({ queryKey: ['chat-config'] })
          }}
        />
      )}
    </div>
  )
}

const padQuestions = (questions: string[]) =>
  Array.from({ length: MAX_QUESTIONS }, (_, i) => questions[i] ?? '')

function ChatStartForm(props: { initial: ChatConfig; onSaved: (data: ChatConfig) => void }) {
  const [welcome, setWelcome] = useState(props.initial.welcome_message ?? '')
  const [questions, setQuestions] = useState(() => padQuestions(props.initial.suggested_questions))
  const [saved, setSaved] = useState(false)

  const save = useMutation({
    mutationFn: saveChatConfig,
    onSuccess: (data) => {
      props.onSaved(data)
      setWelcome(data.welcome_message ?? '')
      setQuestions(padQuestions(data.suggested_questions))
      setSaved(true)
    },
  })

  const tooLong =
    welcome.trim().length > WELCOME_MAX_LENGTH ||
    questions.some((q) => q.trim().length > QUESTION_MAX_LENGTH)

  function edited() {
    setSaved(false)
    save.reset()
  }

  return (
    <form
      className="flex flex-col gap-6"
      onSubmit={(e) => {
        e.preventDefault()
        if (save.isPending || tooLong) return
        save.mutate({
          welcome_message: welcome.trim() || null,
          suggested_questions: questions.map((q) => q.trim()).filter(Boolean),
        })
      }}
    >
      <div className="flex flex-col gap-2">
        <label htmlFor="welcome-message" className="font-medium">
          {t.chatStart.welcomeLabel}
        </label>
        <p className="text-sm text-gray-600">{t.chatStart.welcomeHint}</p>
        <textarea
          id="welcome-message"
          value={welcome}
          onChange={(e) => {
            setWelcome(e.target.value)
            edited()
          }}
          rows={5}
          className="rounded-lg border border-gray-300 p-3 text-sm focus:border-blue-500 focus:outline-none"
        />
        <span
          className={`text-xs ${welcome.trim().length > WELCOME_MAX_LENGTH ? 'text-red-700' : 'text-gray-500'}`}
        >
          {t.chatStart.characters(welcome.trim().length, WELCOME_MAX_LENGTH)}
        </span>
      </div>

      <fieldset className="flex flex-col gap-2">
        <legend className="font-medium">{t.chatStart.questionsTitle}</legend>
        <p className="text-sm text-gray-600">{t.chatStart.questionsHint}</p>
        {questions.map((question, index) => (
          <input
            key={index}
            type="text"
            aria-label={t.chatStart.questionLabel(index + 1)}
            value={question}
            maxLength={QUESTION_MAX_LENGTH}
            onChange={(e) => {
              const value = e.target.value
              setQuestions((current) => current.map((q, i) => (i === index ? value : q)))
              edited()
            }}
            className="rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none"
          />
        ))}
      </fieldset>

      <div className="flex items-center justify-end gap-4">
        {saved && (
          <p role="status" className="text-sm text-emerald-700">
            {t.chatStart.saved}
          </p>
        )}
        {save.isError && (
          <p role="alert" className="text-sm text-red-700">
            {t.chatStart.saveError}
          </p>
        )}
        <button
          type="submit"
          disabled={save.isPending || tooLong}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {save.isPending ? t.chatStart.saving : t.chatStart.save}
        </button>
      </div>
    </form>
  )
}
