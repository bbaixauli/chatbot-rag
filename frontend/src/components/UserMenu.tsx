import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router'
import { anonymousLogin, userLogout } from '../api/auth'
import { AUTH_SESSION_KEY, useAuthSession } from '../auth/session'
import { t } from '../i18n/es'

/** Usuario conectado (o sesión privada), enlace a la administración y cierre de sesión. */
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

  // Sin LDAP, «Terminar sesión» empieza otra sesión privada vacía.
  const restart = useMutation({
    mutationFn: anonymousLogin,
    onSuccess: (data) => {
      queryClient.clear()
      queryClient.setQueryData(AUTH_SESSION_KEY, data)
      navigate('/')
    },
  })

  const ldap = session.data?.mode === 'ldap'
  const user = session.data?.user ?? null
  // Sin LDAP, el enlace lleva al acceso con la cuenta local de administración (también con
  // sesión privada). Con LDAP, un acceso anónimo nunca administra.
  const showAdmin = !ldap || !user || user.is_admin

  return (
    <>
      {showAdmin && (
        <Link to="/admin" className="text-sm text-sobre-cabecera">
          {t.nav.admin}
        </Link>
      )}
      {!ldap && user && (
        <div className="flex items-center gap-3 text-sm">
          <span
            className="rounded border border-borde bg-fondo-alt px-2 py-0.5 text-texto"
            title={t.nav.privateSessionHint}
          >
            {t.nav.privateSession}
          </span>
          <button
            type="button"
            disabled={restart.isPending}
            onClick={() => {
              if (window.confirm(t.nav.confirmEndPrivateSession)) restart.mutate()
            }}
            className="btn btn-cabecera"
          >
            {t.nav.endPrivateSession}
          </button>
          {restart.isError && (
            <span role="alert" className="rounded bg-error-suave px-2 py-0.5 text-error">
              {t.nav.endPrivateSessionError}
            </span>
          )}
        </div>
      )}
      {ldap && user && (
        <div className="flex items-center gap-3 text-sm">
          {user.is_anonymous ? (
            // El id de un acceso anónimo no dice nada al usuario: se explica la sesión.
            <span
              className="rounded border border-borde bg-fondo-alt px-2 py-0.5 text-texto"
              title={t.nav.anonymousSession}
            >
              {user.display_name}
            </span>
          ) : (
            <span title={user.id}>{user.display_name}</span>
          )}
          <button type="button" onClick={() => signOut.mutate()} className="btn btn-cabecera">
            {t.nav.logout}
          </button>
        </div>
      )}
    </>
  )
}
