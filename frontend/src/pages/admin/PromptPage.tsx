import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import {
  getPrompt,
  listPromptVersions,
  restorePromptVersion,
  savePrompt,
  type SystemPrompt,
} from '../../api/admin'
import { ApiError } from '../../api/client'
import { t } from '../../i18n/es'
import { ADMIN_SESSION_KEY } from './session'

const PROMPT_KEY = ['admin', 'prompt'] as const
const VERSIONS_KEY = ['admin', 'prompt-versions'] as const

const formatDate = (iso: string) =>
  new Date(iso).toLocaleString('es-ES', { dateStyle: 'short', timeStyle: 'short' })

export function PromptPage() {
  const queryClient = useQueryClient()
  const prompt = useQuery({ queryKey: PROMPT_KEY, queryFn: getPrompt })
  const versions = useQuery({ queryKey: VERSIONS_KEY, queryFn: listPromptVersions })
  const [notice, setNotice] = useState<string | null>(null)

  // Si la sesión ha caducado, se vuelve a comprobar y el guard redirige al login.
  const unauthorized = [prompt.error, versions.error].some(
    (e) => e instanceof ApiError && e.status === 401,
  )
  useEffect(() => {
    if (unauthorized) void queryClient.invalidateQueries({ queryKey: ADMIN_SESSION_KEY })
  }, [unauthorized, queryClient])

  function onUpdated(data: SystemPrompt, message: string) {
    queryClient.setQueryData(PROMPT_KEY, data)
    void queryClient.invalidateQueries({ queryKey: VERSIONS_KEY })
    setNotice(message)
  }

  const restore = useMutation({
    mutationFn: restorePromptVersion,
    onSuccess: (data) => onUpdated(data, t.prompt.restored),
  })

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <section className="flex flex-col gap-3">
        <div>
          <h2 className="text-xl font-semibold">{t.prompt.title}</h2>
          <p className="text-sm text-gray-600">{t.prompt.description}</p>
        </div>
        {prompt.isPending ? (
          <p className="text-gray-600">{t.common.loading}</p>
        ) : prompt.isError ? (
          <p role="alert" className="text-red-700">
            {t.prompt.loadError}
          </p>
        ) : (
          <PromptEditor
            key={prompt.data.version_id ?? 'default'}
            prompt={prompt.data}
            onSaved={(data) => onUpdated(data, t.prompt.saved)}
            onChange={() => setNotice(null)}
          />
        )}
        {notice && (
          <p role="status" className="text-sm text-emerald-700">
            {notice}
          </p>
        )}
      </section>

      <section aria-labelledby="versions-title" className="flex flex-col gap-3">
        <h3 id="versions-title" className="text-lg font-semibold">
          {t.prompt.history}
        </h3>
        {versions.data?.length === 0 && (
          <p className="text-sm text-gray-600">{t.prompt.noHistory}</p>
        )}
        <ol className="flex flex-col gap-2">
          {versions.data?.map((version) => {
            const isCurrent = version.id === prompt.data?.version_id
            return (
              <li
                key={version.id}
                aria-label={`Versión ${version.id}`}
                className="rounded-lg border border-gray-200 bg-white p-3"
              >
                <div className="flex items-center justify-between gap-2 text-sm">
                  <span className="font-medium">
                    #{version.id}{' '}
                    <span className="font-normal text-gray-600">
                      {t.prompt.by(version.created_by, formatDate(version.created_at))}
                    </span>
                  </span>
                  {isCurrent ? (
                    <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs text-emerald-800">
                      {t.prompt.current}
                    </span>
                  ) : (
                    <button
                      type="button"
                      disabled={restore.isPending}
                      onClick={() => {
                        if (window.confirm(t.prompt.confirmRestore(version.id))) {
                          restore.mutate(version.id)
                        }
                      }}
                      className="rounded-md border border-gray-300 px-2 py-1 text-xs hover:bg-gray-50"
                    >
                      {t.prompt.restore}
                    </button>
                  )}
                </div>
                <p className="mt-2 line-clamp-3 text-sm whitespace-pre-wrap text-gray-700">
                  {version.content}
                </p>
              </li>
            )
          })}
        </ol>
      </section>
    </div>
  )
}

function PromptEditor(props: {
  prompt: SystemPrompt
  onSaved: (data: SystemPrompt) => void
  onChange: () => void
}) {
  const [content, setContent] = useState(props.prompt.content)
  const save = useMutation({ mutationFn: savePrompt, onSuccess: props.onSaved })
  const dirty = content.trim() !== props.prompt.content
  const canSave = dirty && content.trim().length > 0 && !save.isPending

  return (
    <form
      className="flex flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault()
        if (canSave) save.mutate(content.trim())
      }}
    >
      {props.prompt.is_default && (
        <p className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {t.prompt.usingDefault}
        </p>
      )}
      <textarea
        aria-label={t.prompt.label}
        value={content}
        onChange={(e) => {
          setContent(e.target.value)
          props.onChange()
        }}
        rows={14}
        className="rounded-lg border border-gray-300 p-3 font-mono text-sm focus:border-blue-500 focus:outline-none"
      />
      <div className="flex items-center justify-between">
        <span className="text-xs text-gray-500">{t.prompt.characters(content.length)}</span>
        <button
          type="submit"
          disabled={!canSave}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {save.isPending ? t.prompt.saving : t.prompt.save}
        </button>
      </div>
      {save.isError && (
        <p role="alert" className="text-sm text-red-700">
          {t.prompt.saveError}
        </p>
      )}
    </form>
  )
}
