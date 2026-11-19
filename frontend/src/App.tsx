import { QueryClientProvider } from '@tanstack/react-query'
import { useState } from 'react'
import { BrowserRouter, Navigate, Outlet, Route, Routes } from 'react-router'
import { RequireUser } from './auth/RequireUser'
import { HealthBadge } from './components/HealthBadge'
import { Sidebar } from './components/Sidebar'
import { UserMenu } from './components/UserMenu'
import { t } from './i18n/es'
import { ChatPage } from './pages/ChatPage'
import { UserLoginPage } from './pages/UserLoginPage'
import { createQueryClient } from './queryClient'
import { AdminLayout } from './pages/admin/AdminLayout'
import { ChatStartPage } from './pages/admin/ChatStartPage'
import { DocumentsPage } from './pages/admin/DocumentsPage'
import { LoginPage } from './pages/admin/LoginPage'
import { PromptPage } from './pages/admin/PromptPage'

export function Layout() {
  return (
    <div className="flex h-full flex-col bg-gray-50 text-gray-900">
      <header className="flex items-center justify-between border-b border-gray-200 bg-white px-6 py-3">
        <h1 className="text-lg font-semibold">{t.appName}</h1>
        <div className="flex items-center gap-4">
          <HealthBadge />
          <UserMenu />
        </div>
      </header>
      <div className="flex min-h-0 flex-1">
        <Sidebar />
        <main className="flex min-h-0 flex-1 flex-col">
          <Outlet />
        </main>
      </div>
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
        <Route index element={<Navigate to="prompt" replace />} />
        <Route path="prompt" element={<PromptPage />} />
        <Route path="documents" element={<DocumentsPage />} />
        <Route path="chat" element={<ChatStartPage />} />
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
