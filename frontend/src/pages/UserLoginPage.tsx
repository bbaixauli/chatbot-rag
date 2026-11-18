import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Navigate, useLocation, useNavigate } from 'react-router'
import { userLogin } from '../api/auth'
import { ApiError } from '../api/client'
import { AUTH_SESSION_KEY, useAuthSession } from '../auth/session'
import { LoginForm } from '../components/LoginForm'
import { loginErrorMessage } from '../components/loginError'
import { t } from '../i18n/es'

/** Inicio de sesión de los usuarios del chat (modo LDAP). */
export function UserLoginPage() {
  const session = useAuthSession()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from ?? '/'

  const signIn = useMutation({
    mutationFn: ({ username, password }: { username: string; password: string }) =>
      userLogin(username, password),
    onSuccess: (data) => {
      // Cambia el usuario: se descarta todo lo cargado con la sesión anterior.
      queryClient.clear()
      queryClient.setQueryData(AUTH_SESSION_KEY, data)
      navigate(from, { replace: true })
    },
  })

  if (session.isPending) return <p className="p-6 text-gray-600">{t.common.loading}</p>
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
    />
  )
}
