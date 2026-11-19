import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router'
import { useAuthSession } from './session'

/**
 * Con LDAP, exige haber iniciado sesión. Mientras se comprueba la sesión (o si no se puede
 * comprobar) se muestra la aplicación: sin sesión, la API no devuelve ningún dato.
 */
export function RequireUser({ children }: { children: ReactNode }) {
  const session = useAuthSession()
  const location = useLocation()

  if (session.data?.mode === 'ldap' && !session.data.authenticated) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  return children
}
