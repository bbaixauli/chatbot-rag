import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Navigate, useLocation, useNavigate } from 'react-router'
import { anonymousLogin, userLogin, type AuthSession } from '../api/auth'
import { ApiError } from '../api/client'
import { AUTH_SESSION_KEY, useAuthSession } from '../auth/session'
import { LoginForm } from '../components/LoginForm'
import { loginErrorMessage } from '../components/loginError'
import { usePageTitle } from '../hooks/usePageTitle'
import { t } from '../i18n/es'

/** Inicio de sesión de los usuarios del chat (modo LDAP), con acceso anónimo opcional. */
export function UserLoginPage() {
  usePageTitle(t.userLogin.title)
  const session = useAuthSession()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from ?? '/'

  function enter(data: AuthSession) {
    // Cambia el usuario: se descarta todo lo cargado con la sesión anterior.
    queryClient.clear()
    queryClient.setQueryData(AUTH_SESSION_KEY, data)
    navigate(from, { replace: true })
  }

  const signIn = useMutation({
    mutationFn: ({ username, password }: { username: string; password: string }) =>
      userLogin(username, password),
    onSuccess: enter,
  })
  const anonymous = useMutation({ mutationFn: anonymousLogin, onSuccess: enter })

  if (session.isPending) return <p className="p-6 text-texto-secundario">{t.common.loading}</p>
  if (session.data?.mode !== 'ldap' || session.data.authenticated) {
    return <Navigate to={from} replace />
  }

  const status = signIn.error instanceof ApiError ? signIn.error.status : null
  return (
    <LoginForm
      title={t.userLogin.title}
      hint={t.userLogin.hint}
      error={loginErrorMessage(status, signIn.isError)}
      pending={signIn.isPending}
      onSubmit={(username, password) => signIn.mutate({ username, password })}
      footer={
        session.data.anonymous_allowed && (
          <div className="flex flex-col gap-1 border-t border-borde pt-4 text-center">
            <button
              type="button"
              onClick={() => anonymous.mutate()}
              disabled={anonymous.isPending}
              className="btn btn-terciario"
            >
              {t.userLogin.anonymous}
            </button>
            <p className="text-xs text-texto-secundario">{t.userLogin.anonymousHint}</p>
            {anonymous.isError && (
              <p role="alert" className="text-sm text-error">
                {t.userLogin.anonymousError}
              </p>
            )}
          </div>
        )
      }
    />
  )
}
