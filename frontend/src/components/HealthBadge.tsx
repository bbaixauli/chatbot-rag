import { useEffect, useState } from 'react'
import { getHealth } from '../api/client'
import { t } from '../i18n/es'

type State = 'checking' | 'ok' | 'degraded' | 'offline'

// Siempre con texto: el color no es el único indicador (WCAG 1.4.1).
const styles: Record<State, string> = {
  checking: 'border-borde bg-fondo-alt text-texto-secundario',
  ok: 'border-exito bg-exito-suave text-exito',
  degraded: 'border-aviso bg-aviso-suave text-aviso',
  offline: 'border-error bg-error-suave text-error',
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
      className={`rounded border px-2 py-1 text-xs font-semibold ${styles[state]}`}
    >
      {t.health[state]}
    </span>
  )
}
