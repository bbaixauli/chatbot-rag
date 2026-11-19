import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router'
import { login } from '../../api/admin'
import { ApiError } from '../../api/client'
import { t } from '../../i18n/es'
import { ADMIN_SESSION_KEY, useAdminSession } from './session'

export function LoginPage() {
  const session = useAdminSession()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from ?? '/admin/prompt'
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  const signIn = useMutation({
    mutationFn: () => login(username, password),
    onSuccess: (data) => {
      queryClient.setQueryData(ADMIN_SESSION_KEY, data)
      navigate(from, { replace: true })
    },
  })

  if (session.data?.authenticated) return <Navigate to={from} replace />

  function submit(event: FormEvent) {
    event.preventDefault()
    signIn.mutate()
  }

  const status = signIn.error instanceof ApiError ? signIn.error.status : null
  const errorMessage =
    status === 401
      ? t.login.invalid
      : status === 429
        ? t.login.tooManyAttempts
        : signIn.error
          ? t.login.error
          : null

  return (
    <div className="flex h-full items-center justify-center bg-gray-50 p-6">
      <form
        onSubmit={submit}
        className="flex w-full max-w-sm flex-col gap-4 rounded-xl border border-gray-200 bg-white p-6 shadow-sm"
      >
        <h1 className="text-xl font-semibold">{t.login.title}</h1>
        {session.data && !session.data.enabled ? (
          <p role="alert" className="text-sm text-amber-800">
            {t.login.disabled}
          </p>
        ) : (
          <>
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
            {errorMessage && (
              <p role="alert" className="text-sm text-red-700">
                {errorMessage}
              </p>
            )}
            <button
              type="submit"
              disabled={signIn.isPending}
              className="rounded-md bg-blue-600 px-4 py-2 font-medium text-white disabled:opacity-50"
            >
              {t.login.submit}
            </button>
          </>
        )}
        <Link to="/" className="text-center text-sm text-blue-700 hover:underline">
          {t.nav.backToChat}
        </Link>
      </form>
    </div>
  )
}
