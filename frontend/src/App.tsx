import { QueryClientProvider } from '@tanstack/react-query'
import { Menu, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation } from 'react-router'
import { RequireUser } from './auth/RequireUser'
import { HealthBadge } from './components/HealthBadge'
import { AppFooter, Brand, MAIN_ID, SkipLink } from './components/PageChrome'
import { SIDEBAR_ID, Sidebar } from './components/Sidebar'
import { UserMenu } from './components/UserMenu'
import { t } from './i18n/es'
import { ChatPage } from './pages/ChatPage'
import { UserLoginPage } from './pages/UserLoginPage'
import { createQueryClient } from './queryClient'
import { AdminLayout } from './pages/admin/AdminLayout'
import { AppearancePage } from './pages/admin/AppearancePage'
import { ChatStartPage } from './pages/admin/ChatStartPage'
import { DashboardPage } from './pages/admin/DashboardPage'
import { ConfigPage } from './pages/admin/ConfigPage'
import { DocumentsPage } from './pages/admin/DocumentsPage'
import { LoginPage } from './pages/admin/LoginPage'
import { PromptPage } from './pages/admin/PromptPage'

export function Layout() {
  const location = useLocation()
  // El menú plegado (pantallas estrechas) se abre para la página actual: al navegar se
  // cierra solo. También se cierra con Esc.
  const [menuOpenAt, setMenuOpenAt] = useState<string | null>(null)
  const menuOpen = menuOpenAt === location.pathname
  const setMenuOpen = (open: boolean) => setMenuOpenAt(open ? location.pathname : null)
  useEffect(() => {
    if (!menuOpen) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setMenuOpenAt(null)
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [menuOpen])

  return (
    <div className="flex h-full flex-col bg-fondo-alt text-texto">
      <SkipLink />
      <header className="cabecera flex min-h-14 flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-borde bg-cabecera px-4 py-2 text-sobre-cabecera md:min-h-16 md:px-6">
        <div className="flex items-center gap-3">
          <button
            type="button"
            className="btn btn-cabecera md:hidden"
            aria-expanded={menuOpen}
            aria-controls={SIDEBAR_ID}
            onClick={() => setMenuOpen(!menuOpen)}
          >
            {menuOpen ? <X aria-hidden size={20} /> : <Menu aria-hidden size={20} />}
            {t.nav.menu}
          </button>
          <Brand />
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <HealthBadge />
          <UserMenu />
        </div>
      </header>
      <div className="relative flex min-h-0 flex-1">
        <Sidebar open={menuOpen} onNavigate={() => setMenuOpenAt(null)} />
        <main id={MAIN_ID} tabIndex={-1} className="flex min-h-0 min-w-0 flex-1 flex-col">
          <Outlet />
        </main>
      </div>
      <AppFooter />
    </div>
  )
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="login" element={<UserLoginPage />} />
      <Route
        element={
          <RequireUser>
            <Layout />
          </RequireUser>
        }
      >
        <Route index element={<ChatPage />} />
        <Route path="c/:conversationId" element={<ChatPage />} />
      </Route>
      <Route path="admin/login" element={<LoginPage />} />
      <Route path="admin" element={<AdminLayout />}>
        <Route index element={<Navigate to="dashboard" replace />} />
        <Route path="dashboard" element={<DashboardPage />} />
        <Route path="prompt" element={<PromptPage />} />
        <Route path="documents" element={<DocumentsPage />} />
        <Route path="chat" element={<ChatStartPage />} />
        <Route path="appearance" element={<AppearancePage />} />
        <Route path="config" element={<ConfigPage />} />
      </Route>
    </Routes>
  )
}

export default function App() {
  const [queryClient] = useState(() =>
    createQueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 5_000 } } }),
  )
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </QueryClientProvider>
  )
}
