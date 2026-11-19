import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, type ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'
import { anonymousLogin } from '../api/auth'
import { t } from '../i18n/es'
import { AUTH_SESSION_KEY, useAuthSession } from './session'

/**
 * Con LDAP, exige haber iniciado sesión. Sin LDAP y con sesiones privadas, crea la sesión
 * privada del visitante al entrar. Mientras se comprueba la sesión (o si no se puede
 * comprobar) se muestra la aplicación: sin sesión, la API no devuelve ningún dato.
 */
export function RequireUser({ children }: { children: ReactNode }) {
  const session = useAuthSession()
  const location = useLocation()

  if (session.data?.mode === 'ldap' && !session.data.authenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  if (
    session.data?.mode === 'none' &&
    session.data.private_sessions &&
    !session.data.authenticated
  ) {
    return <StartPrivateSession />
  }
  return children
}

/** Crea la sesión privada sin intervención del usuario (una sola vez por montaje). */
function StartPrivateSession() {
  const queryClient = useQueryClient()
  const start = useMutation({
    mutationFn: anonymousLogin,
    onSuccess: async (data) => {
      // Mientras se creaba, los 401 de la aplicación pudieron volver a consultar la sesión:
      // esas respuestas («sin sesión») no deben pisar la recién creada.
      await queryClient.cancelQueries({ queryKey: AUTH_SESSION_KEY })
      // Lo cargado antes (p. ej. el historial compartido) no pertenece a esta sesión.
      queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== AUTH_SESSION_KEY[0] })
      queryClient.setQueryData(AUTH_SESSION_KEY, data)
    },
  })
  // StrictMode ejecuta los efectos dos veces en desarrollo: se evita crear dos sesiones.
  const started = useRef(false)
  const { mutate } = start
  useEffect(() => {
    if (started.current) return
    started.current = true
    mutate()
  }, [mutate])

  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 p-6 text-texto-secundario">
      {start.isError ? (
        <>
          <p role="alert" className="text-error">
            {t.privateSession.error}
          </p>
          <button type="button" onClick={() => mutate()} className="btn btn-secundario">
            {t.privateSession.retry}
          </button>
        </>
      ) : (
        <p>{t.privateSession.starting}</p>
      )}
    </div>
  )
}
