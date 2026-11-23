import type { DateRange } from '../api/dashboard'

/** Fecha AAAA-MM-DD en la hora local del navegador. */
export function isoDate(date: Date) {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

export function addDays(date: Date, days: number) {
  const copy = new Date(date)
  copy.setDate(copy.getDate() + days)
  return copy
}

export type PresetId = 'today' | 'last7' | 'last30' | 'month'

export const PRESET_IDS: PresetId[] = ['today', 'last7', 'last30', 'month']

export function presetRange(preset: PresetId, today = new Date()): DateRange {
  const to = isoDate(today)
  switch (preset) {
    case 'today':
      return { from: to, to }
    case 'last7':
      return { from: isoDate(addDays(today, -6)), to }
    case 'last30':
      return { from: isoDate(addDays(today, -29)), to }
    case 'month':
      return { from: isoDate(new Date(today.getFullYear(), today.getMonth(), 1)), to }
  }
}

export function matchingPreset(range: DateRange, today = new Date()): PresetId | null {
  return (
    PRESET_IDS.find((id) => {
      const preset = presetRange(id, today)
      return preset.from === range.from && preset.to === range.to
    }) ?? null
  )
}

const integer = new Intl.NumberFormat('es-ES')
const percent = new Intl.NumberFormat('es-ES', { style: 'percent', maximumFractionDigits: 1 })
const seconds = new Intl.NumberFormat('es-ES', {
  maximumFractionDigits: 1,
  minimumFractionDigits: 1,
})

/** Números con el formato del manual (punto de millares a partir de 10.000). */
export const formatNumber = (value: number) => integer.format(value)

export const formatPercent = (ratio: number | null) =>
  ratio === null ? '—' : percent.format(ratio)

export const formatSeconds = (ms: number | null) =>
  ms === null ? '—' : `${seconds.format(ms / 1000)} s`

/** dd/mm/aaaa hh:mm a partir de una fecha ISO que ya está en hora local (con su desfase). */
export function formatLocalDateTime(iso: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso)
  if (!match) return iso
  const [, year, month, day, hour, minute] = match
  return `${day}/${month}/${year} ${hour}:${minute}`
}

/** Texto plano de una respuesta en Markdown, para el resumen de una fila. */
export function plainText(markdown: string) {
  return markdown
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[*_`~#>]+/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

/** dd/mm/aaaa a partir de AAAA-MM-DD. */
export function formatDay(day: string) {
  const [year, month, date] = day.split('-')
  return `${date}/${month}/${year}`
}

/** Marcas del eje Y: números redondos desde 0 que cubren `max`. */
export function niceTicks(max: number, count = 4): number[] {
  if (max <= 0) return [0, 1]
  const rough = max / count
  const power = 10 ** Math.floor(Math.log10(rough))
  const step = [1, 2, 5, 10].map((m) => m * power).find((s) => s >= rough) ?? 10 * power
  const stepInt = Math.max(1, Math.round(step))
  const ticks = []
  for (let value = 0; value < max + stepInt; value += stepInt) ticks.push(value)
  return ticks
}

export type SeriesKey = 'questions' | 'sessions' | 'users'

// Orden fijo de la paleta: el color sigue a la serie, nunca a su posición.
export const SERIES: { key: SeriesKey; color: string }[] = [
  { key: 'questions', color: 'var(--serie-1)' },
  { key: 'sessions', color: 'var(--serie-2)' },
  { key: 'users', color: 'var(--serie-3)' },
]
