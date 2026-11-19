import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Menu, X } from 'lucide-react'
import { useState } from 'react'
import { Link, Navigate, NavLink, Outlet, useLocation, useNavigate } from 'react-router'
import { logout } from '../../api/admin'
import { AppFooter, MAIN_ID, SkipLink } from '../../components/PageChrome'
import { t } from '../../i18n/es'
import { ADMIN_SESSION_KEY, useAdminSession } from './session'

const ADMIN_NAV_ID = 'secciones-administracion'

/** Zona de administración: exige sesión y muestra la navegación de secciones. */
export function AdminLayout() {
  const session = useAdminSession()
  const location = useLocation()
  // El menú plegado (pantallas estrechas) se abre para la sección actual: al cambiar de
  // sección se cierra solo.
  const [menuOpenAt, setMenuOpenAt] = useState<string | null>(null)
  const menuOpen = menuOpenAt === location.pathname
  const setMenuOpen = (open: boolean) => setMenuOpenAt(open ? location.pathname : null)
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

  if (session.isPending) return <p className="p-6 text-texto-secundario">{t.common.loading}</p>
  if (!session.data?.authenticated) {
    return <Navigate to="/admin/login" replace state={{ from: location.pathname }} />
  }

  return (
    <div className="flex h-full flex-col bg-fondo-alt text-texto">
      <SkipLink />
      <header className="cabecera flex min-h-14 flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-borde bg-cabecera px-4 py-2 text-sobre-cabecera md:min-h-16 md:px-6">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          <button
            type="button"
            className="btn btn-cabecera md:hidden"
            aria-expanded={menuOpen}
            aria-controls={ADMIN_NAV_ID}
            onClick={() => setMenuOpen(!menuOpen)}
          >
            {menuOpen ? <X aria-hidden size={20} /> : <Menu aria-hidden size={20} />}
            {t.nav.menu}
          </button>
          <span className="text-lg font-semibold">
            {t.appName} · {t.nav.admin}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
          <span>{session.data.username}</span>
          <Link to="/" className="text-sobre-cabecera">
            {t.nav.backToChat}
          </Link>
          <button type="button" onClick={() => signOut.mutate()} className="btn btn-cabecera">
            {t.nav.logout}
          </button>
        </div>
      </header>
      <nav
        id={ADMIN_NAV_ID}
        aria-label={t.nav.admin}
        className={`${menuOpen ? 'flex' : 'hidden'} flex-col gap-1 border-b border-borde bg-fondo px-4 py-2 md:flex md:flex-row md:px-6`}
      >
        {[
          { to: '/admin/prompt', label: t.nav.prompt },
          { to: '/admin/documents', label: t.nav.documents },
          { to: '/admin/chat', label: t.nav.chatStart },
        ].map((item) => (
          <NavLink key={item.to} to={item.to} className="nav-item text-sm">
            {item.label}
          </NavLink>
        ))}
      </nav>
      <main id={MAIN_ID} tabIndex={-1} className="flex-1 overflow-y-auto p-4 md:p-6">
        <Outlet />
      </main>
      <AppFooter />
    </div>
  )
}
