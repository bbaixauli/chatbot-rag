import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import {
  getAccessConfig,
  getAdminChatConfig,
  saveAccessConfig,
  saveChatConfig,
} from '../../api/admin'
import { ApiError, type ChatConfig } from '../../api/client'
import { usePageTitle } from '../../hooks/usePageTitle'
import { t } from '../../i18n/es'
import { AUTH_SESSION_KEY } from '../../auth/session'
import { ADMIN_SESSION_KEY } from './session'

const CONFIG_KEY = ['admin', 'chat-config'] as const
const ACCESS_KEY = ['admin', 'access'] as const
// Deben coincidir con los límites del servidor (CHATBOT_CHAT_*).
const MAX_QUESTIONS = 5
const WELCOME_MAX_LENGTH = 2000
const QUESTION_MAX_LENGTH = 300

/**
 * Administración del inicio del chat: mensaje de bienvenida, preguntas sugeridas y acceso
 * (acceso anónimo con LDAP o sesiones privadas sin él).
 */
export function ChatStartPage() {
  usePageTitle(t.chatStart.title)
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
        <h1 className="text-2xl font-bold">{t.chatStart.title}</h1>
        <p className="text-sm text-texto-secundario">{t.chatStart.description}</p>
      </div>
      {config.isPending ? (
        <p className="text-texto-secundario">{t.common.loading}</p>
      ) : config.isError ? (
        <p role="alert" className="text-error">
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
      <AccessSection />
    </div>
  )
}

/**
 * Acceso al chat. Con LDAP: permitir el acceso anónimo. Sin LDAP: sesiones privadas y
 * temporales en lugar del historial compartido.
 */
function AccessSection() {
  const queryClient = useQueryClient()
  const access = useQuery({ queryKey: ACCESS_KEY, queryFn: getAccessConfig })
  const save = useMutation({
    mutationFn: saveAccessConfig,
    onSuccess: (data) => {
      queryClient.setQueryData(ACCESS_KEY, data)
      // El chat (o el login) vuelve a consultar cómo se accede.
      void queryClient.invalidateQueries({ queryKey: AUTH_SESSION_KEY })
    },
  })

  if (access.isPending) return null
  const ldap = access.data?.mode === 'ldap'
  const texts = ldap
    ? {
        label: t.chatStart.anonymousLabel,
        hint: t.chatStart.anonymousHint,
        warning: t.chatStart.anonymousWarning,
      }
    : {
        label: t.chatStart.privateSessionsLabel,
        hint: t.chatStart.privateSessionsHint,
        warning: t.chatStart.privateSessionsWarning,
      }
  return (
    <section className="flex flex-col gap-2 border-t border-borde pt-6">
      <h2 className="text-lg font-bold">{t.chatStart.accessTitle}</h2>
      {access.isError ? (
        <p role="alert" className="text-sm text-error">
          {t.chatStart.accessLoadError}
        </p>
      ) : (
        <>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={ldap ? access.data.anonymous_allowed : access.data.private_sessions}
              disabled={save.isPending}
              onChange={(e) =>
                save.mutate(
                  ldap
                    ? { anonymous_allowed: e.target.checked }
                    : { private_sessions: e.target.checked },
                )
              }
              className="h-5 w-5 accent-marca"
            />
            {texts.label}
          </label>
          <p className="text-sm text-texto-secundario">{texts.hint}</p>
          <p className="text-sm text-aviso">{texts.warning}</p>
          {save.isSuccess && (
            <p role="status" className="text-sm text-exito">
              {t.chatStart.accessSaved}
            </p>
          )}
          {save.isError && (
            <p role="alert" className="text-sm text-error">
              {t.chatStart.accessSaveError}
            </p>
          )}
        </>
      )}
    </section>
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
        <p className="text-sm text-texto-secundario">{t.chatStart.welcomeHint}</p>
        <textarea
          id="welcome-message"
          value={welcome}
          onChange={(e) => {
            setWelcome(e.target.value)
            edited()
          }}
          rows={5}
          className="campo text-sm"
        />
        <span
          className={`text-xs ${welcome.trim().length > WELCOME_MAX_LENGTH ? 'text-error' : 'text-texto-secundario'}`}
        >
          {t.chatStart.characters(welcome.trim().length, WELCOME_MAX_LENGTH)}
        </span>
      </div>

      <fieldset className="flex flex-col gap-2">
        <legend className="font-medium">{t.chatStart.questionsTitle}</legend>
        <p className="text-sm text-texto-secundario">{t.chatStart.questionsHint}</p>
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
            className="campo text-sm"
          />
        ))}
      </fieldset>

      <div className="flex items-center justify-end gap-4">
        {saved && (
          <p role="status" className="text-sm text-exito">
            {t.chatStart.saved}
          </p>
        )}
        {save.isError && (
          <p role="alert" className="text-sm text-error">
            {t.chatStart.saveError}
          </p>
        )}
        <button type="submit" disabled={save.isPending || tooLong} className="btn btn-primario">
          {save.isPending ? t.chatStart.saving : t.chatStart.save}
        </button>
      </div>
    </form>
  )
}
