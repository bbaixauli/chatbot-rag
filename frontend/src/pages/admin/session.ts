import { useQuery } from '@tanstack/react-query'
import { getAdminSession } from '../../api/admin'

export const ADMIN_SESSION_KEY = ['admin-session'] as const

export function useAdminSession() {
  return useQuery({ queryKey: ADMIN_SESSION_KEY, queryFn: getAdminSession, staleTime: 0 })
}
