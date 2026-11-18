import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, Navigate, NavLink, Outlet, useLocation, useNavigate } from 'react-router'
import { logout } from '../../api/admin'
import { t } from '../../i18n/es'
import { ADMIN_SESSION_KEY, useAdminSession } from './session'

/** Zona de administración: exige sesión y muestra la navegación de secciones. */
export function AdminLayout() {
  const session = useAdminSession()
  const location = useLocation()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const signOut = useMutation({
    mutationFn: logout,
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: ['admin'] })
      queryClient.setQueryData(ADMIN_SESSION_KEY, {
        authenticated: false,
        username: null,
        enabled: true,
      })
      navigate('/admin/login')
    },
  })

  if (session.isPending) return <p className="p-6 text-gray-600">{t.common.loading}</p>
  if (!session.data?.authenticated) {
    return <Navigate to="/admin/login" replace state={{ from: location.pathname }} />
  }

  return (
    <div className="flex h-full flex-col bg-gray-50 text-gray-900">
      <header className="flex items-center justify-between border-b border-gray-200 bg-white px-6 py-3">
        <div className="flex items-center gap-6">
          <h1 className="text-lg font-semibold">{t.nav.admin}</h1>
          <nav aria-label={t.nav.admin} className="flex gap-2 text-sm">
            {[
              { to: '/admin/prompt', label: t.nav.prompt },
              { to: '/admin/documents', label: t.nav.documents },
              { to: '/admin/chat', label: t.nav.chatStart },
            ].map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `rounded-md px-3 py-1.5 ${isActive ? 'bg-gray-100 font-medium' : 'hover:bg-gray-50'}`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
        </div>
        <div className="flex items-center gap-4 text-sm">
          <span className="text-gray-600">{session.data.username}</span>
          <Link to="/" className="text-blue-700 hover:underline">
            {t.nav.backToChat}
          </Link>
          <button
            type="button"
            onClick={() => signOut.mutate()}
            className="rounded-md border border-gray-300 px-3 py-1.5 hover:bg-gray-50"
          >
            {t.nav.logout}
          </button>
        </div>
      </header>
      <main className="flex-1 overflow-y-auto p-6">
        <Outlet />
      </main>
    </div>
  )
}
