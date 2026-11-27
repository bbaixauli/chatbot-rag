import { useEffect, useState } from 'react'
import { getHealth, type HealthResponse } from '../api/client'
import { t } from '../i18n/es'

type State = 'checking' | 'ok' | 'degraded' | 'offline'
type Label = State | 'documentation'

// Siempre con texto: el color no es el único indicador (WCAG 1.4.1).
const styles: Record<State, string> = {
  checking: 'border-borde bg-fondo-alt text-texto-secundario',
  ok: 'border-exito bg-exito-suave text-exito',
  degraded: 'border-aviso bg-aviso-suave text-aviso',
  offline: 'border-error bg-error-suave text-error',
}

/** Si solo fallan el índice o los embeddings, el chat funciona pero sin documentación. */
function label(health: HealthResponse): Label {
  if (health.status === 'ok') return 'ok'
  const documentation = health.vector_store === 'error' || health.embeddings === 'error'
  return documentation && health.database === 'ok' ? 'documentation' : 'degraded'
}

export function HealthBadge() {
  const [state, setState] = useState<Label>('checking')

  useEffect(() => {
    const controller = new AbortController()
    getHealth(controller.signal)
      .then((health) => setState(label(health)))
      .catch(() => {
        if (!controller.signal.aborted) setState('offline')
      })
    return () => controller.abort()
  }, [])

  const style = state === 'documentation' ? 'degraded' : state
  return (
    <span
      role="status"
      data-state={style}
      className={`rounded border px-2 py-1 text-xs font-semibold ${styles[style]}`}
    >
      {t.health[state]}
    </span>
  )
}
