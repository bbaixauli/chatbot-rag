import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, Navigate, useLocation, useNavigate } from 'react-router'
import { login } from '../../api/admin'
import { ApiError } from '../../api/client'
import { AUTH_SESSION_KEY, useAuthSession } from '../../auth/session'
import { LoginForm } from '../../components/LoginForm'
import { loginErrorMessage } from '../../components/loginError'
import { usePageTitle } from '../../hooks/usePageTitle'
import { t } from '../../i18n/es'
import { ADMIN_SESSION_KEY, useAdminSession } from './session'

export function LoginPage() {
  usePageTitle(t.login.title)
  const session = useAdminSession()
  const auth = useAuthSession()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from ?? '/admin/dashboard'

  const signIn = useMutation({
    mutationFn: ({ username, password }: { username: string; password: string }) =>
      login(username, password),
    onSuccess: (data) => {
      queryClient.setQueryData(ADMIN_SESSION_KEY, data)
      // Con LDAP, el inicio de sesión de administración también inicia la del chat.
      void queryClient.invalidateQueries({ queryKey: AUTH_SESSION_KEY })
      navigate(from, { replace: true })
    },
    // Un usuario del directorio sin permisos sí ha iniciado sesión en el chat.
    onError: () => void queryClient.invalidateQueries({ queryKey: AUTH_SESSION_KEY }),
  })

  if (session.data?.authenticated) return <Navigate to={from} replace />

  const status = signIn.error instanceof ApiError ? signIn.error.status : null
  return (
    <LoginForm
      title={t.login.title}
      hint={auth.data?.mode === 'ldap' ? t.login.adminLdapHint : undefined}
      unavailable={session.data && !session.data.enabled ? t.login.disabled : null}
      error={loginErrorMessage(status, signIn.isError)}
      pending={signIn.isPending}
      onSubmit={(username, password) => signIn.mutate({ username, password })}
      footer={
        <Link to="/" className="text-center text-sm">
          {t.nav.backToChat}
        </Link>
      }
    />
  )
}
