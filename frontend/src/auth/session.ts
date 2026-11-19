import { useQuery } from '@tanstack/react-query'
import { getAuthSession } from '../api/auth'

export const AUTH_SESSION_KEY = ['auth-session'] as const

export function useAuthSession() {
  return useQuery({ queryKey: AUTH_SESSION_KEY, queryFn: getAuthSession, staleTime: 60_000 })
}
