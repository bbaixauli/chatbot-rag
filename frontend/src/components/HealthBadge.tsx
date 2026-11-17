import { useEffect, useState } from 'react'
import { getHealth } from '../api/client'
import { t } from '../i18n/es'

type State = 'checking' | 'ok' | 'degraded' | 'offline'

const styles: Record<State, string> = {
  checking: 'bg-gray-100 text-gray-600',
  ok: 'bg-emerald-100 text-emerald-800',
  degraded: 'bg-amber-100 text-amber-800',
  offline: 'bg-red-100 text-red-800',
}

export function HealthBadge() {
  const [state, setState] = useState<State>('checking')

  useEffect(() => {
    const controller = new AbortController()
    getHealth(controller.signal)
      .then((health) => setState(health.status))
      .catch(() => {
        if (!controller.signal.aborted) setState('offline')
      })
    return () => controller.abort()
  }, [])

  return (
    <span
      role="status"
      data-state={state}
      className={`rounded-full px-3 py-1 text-xs font-medium ${styles[state]}`}
    >
      {t.health[state]}
    </span>
  )
}
