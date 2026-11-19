import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router'
import { userLogout } from '../api/auth'
import { AUTH_SESSION_KEY, useAuthSession } from '../auth/session'
import { t } from '../i18n/es'

/** Usuario conectado, enlace a la administración y cierre de sesión. */
export function UserMenu() {
  const session = useAuthSession()
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  const signOut = useMutation({
    mutationFn: userLogout,
    onSuccess: () => {
      const anonymousAllowed = session.data?.anonymous_allowed
      queryClient.clear()
      // El login sigue ofreciendo (o no) el acceso anónimo, y se confirma con el servidor.
      queryClient.setQueryData(AUTH_SESSION_KEY, {
        mode: 'ldap',
        authenticated: false,
        user: null,
        anonymous_allowed: anonymousAllowed,
      })
      void queryClient.invalidateQueries({ queryKey: AUTH_SESSION_KEY })
      navigate('/login')
    },
  })

  const user = session.data?.mode === 'ldap' ? session.data.user : null
  // Sin LDAP, el enlace lleva al acceso con la cuenta local de administración. Un acceso
  // anónimo nunca administra.
  const showAdmin = !user || user.is_admin

  return (
    <>
      {showAdmin && (
        <Link to="/admin" className="text-sm text-blue-700 hover:underline">
          {t.nav.admin}
        </Link>
      )}
      {user && (
        <div className="flex items-center gap-3 text-sm">
          {user.is_anonymous ? (
            // El id de un acceso anónimo no dice nada al usuario: se explica la sesión.
            <span
              className="rounded-full bg-gray-100 px-2 py-0.5 text-gray-700"
              title={t.nav.anonymousSession}
            >
              {user.display_name}
            </span>
          ) : (
            <span className="text-gray-700" title={user.id}>
              {user.display_name}
            </span>
          )}
          <button
            type="button"
            onClick={() => signOut.mutate()}
            className="rounded-md border border-gray-300 px-3 py-1.5 hover:bg-gray-50"
          >
            {t.nav.logout}
          </button>
        </div>
      )}
    </>
  )
}
