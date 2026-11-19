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
    <main className="flex h-full items-center justify-center bg-fondo-alt p-4 md:p-6">
      <form
        onSubmit={submit}
        className="flex w-full max-w-sm flex-col gap-4 rounded-lg border border-borde bg-fondo p-6 shadow-sm"
      >
        <h1 className="text-xl font-semibold">{title}</h1>
        {unavailable ? (
          <p role="alert" className="text-sm text-aviso">
            {unavailable}
          </p>
        ) : (
          <>
            {hint && <p className="text-sm text-texto-secundario">{hint}</p>}
            <label className="flex flex-col gap-1 text-sm">
              {t.login.username}
              <input
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                required
                className="campo"
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
                className="campo"
              />
            </label>
            {error && (
              <p role="alert" className="text-sm text-error">
                {error}
              </p>
            )}
            <button type="submit" disabled={pending} className="btn btn-primario">
              {t.login.submit}
            </button>
          </>
        )}
        {footer}
      </form>
    </main>
  )
}
