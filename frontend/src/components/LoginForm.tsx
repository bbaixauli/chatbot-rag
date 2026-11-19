import { useState, type FormEvent, type ReactNode } from 'react'
import { t } from '../i18n/es'

interface Props {
  title: string
  hint?: string
  /** Si se indica, se muestra en lugar del formulario (p. ej. acceso deshabilitado). */
  unavailable?: string | null
  error?: string | null
  pending: boolean
  onSubmit: (username: string, password: string) => void
  footer?: ReactNode
}

export function LoginForm({ title, hint, unavailable, error, pending, onSubmit, footer }: Props) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  function submit(event: FormEvent) {
    event.preventDefault()
    onSubmit(username, password)
  }

  return (
    <div className="flex h-full items-center justify-center bg-gray-50 p-6">
      <form
        onSubmit={submit}
        className="flex w-full max-w-sm flex-col gap-4 rounded-xl border border-gray-200 bg-white p-6 shadow-sm"
      >
        <h1 className="text-xl font-semibold">{title}</h1>
        {unavailable ? (
          <p role="alert" className="text-sm text-amber-800">
            {unavailable}
          </p>
        ) : (
          <>
            {hint && <p className="text-sm text-gray-600">{hint}</p>}
            <label className="flex flex-col gap-1 text-sm">
              {t.login.username}
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
                className="rounded-md border border-gray-300 px-3 py-2"
              />
            </label>
            <label className="flex flex-col gap-1 text-sm">
              {t.login.password}
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                required
                className="rounded-md border border-gray-300 px-3 py-2"
              />
            </label>
            {error && (
              <p role="alert" className="text-sm text-red-700">
                {error}
              </p>
            )}
            <button
              type="submit"
              disabled={pending}
              className="rounded-md bg-blue-600 px-4 py-2 font-medium text-white disabled:opacity-50"
            >
              {t.login.submit}
            </button>
          </>
        )}
        {footer}
      </form>
    </div>
  )
}
