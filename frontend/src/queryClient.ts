import { QueryCache, QueryClient, type QueryClientConfig } from '@tanstack/react-query'
import { ApiError } from './api/client'
import { AUTH_SESSION_KEY } from './auth/session'
import { ADMIN_SESSION_KEY } from './pages/admin/session'

/**
 * Cliente de React Query de la aplicación. Si una consulta responde 401 (sesión caducada),
 * se vuelve a comprobar la sesión para que los guards redirijan al inicio de sesión.
 */
export function createQueryClient(config: QueryClientConfig = {}) {
  const client: QueryClient = new QueryClient({
    ...config,
    queryCache: new QueryCache({
      onError: (error, query) => {
        if (!(error instanceof ApiError) || error.status !== 401) return
        const isAdminQuery = query.queryKey[0] === 'admin'
        void client.invalidateQueries({
          queryKey: isAdminQuery ? ADMIN_SESSION_KEY : AUTH_SESSION_KEY,
        })
      },
    }),
  })
  return client
}
