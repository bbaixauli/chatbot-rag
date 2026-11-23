import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react'
import type { DayStats } from '../api/dashboard'
import { t } from '../i18n/es'
import { SERIES, formatDay, formatNumber, niceTicks, type SeriesKey } from '../utils/dashboard'

const HEIGHT = 240
const MARGIN = { top: 16, right: 112, bottom: 28, left: 44 }
const LABEL_GAP = 14 // separación mínima entre etiquetas finales

/**
 * Sesiones, usuarios y preguntas por día: líneas de 2 px con un solo eje (todo son
 * recuentos), leyenda, etiquetas al final cuando no se solapan y línea vertical que sigue
 * al puntero o a las flechas del teclado. Los mismos datos están en la tabla equivalente.
 */
export function DailyChart({ days }: { days: DayStats[] }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(720)
  const [active, setActive] = useState<number | null>(null)

  useEffect(() => {
    const element = containerRef.current
    if (!element || typeof ResizeObserver === 'undefined') return
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.max(280, Math.round(entry.contentRect.width)))
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  // En pantallas estrechas no caben las etiquetas finales: quedan la leyenda y el recuadro.
  const showEndLabels = width >= 480
  const margin = { ...MARGIN, right: showEndLabels ? MARGIN.right : 16 }
  const plotWidth = width - margin.left - margin.right
  const plotHeight = HEIGHT - margin.top - margin.bottom
  const max = Math.max(1, ...days.flatMap((d) => SERIES.map((s) => d[s.key])))
  const ticks = niceTicks(max)
  const top = ticks[ticks.length - 1] ?? 1
  const x = (i: number) =>
    margin.left + (days.length <= 1 ? plotWidth / 2 : (i * plotWidth) / (days.length - 1))
  const y = (value: number) => margin.top + plotHeight - (value / top) * plotHeight

  // Unas 6 fechas en el eje X, repartidas, incluidas la primera y la última.
  const labelEvery = Math.max(1, Math.ceil(days.length / (width < 480 ? 3 : 6)))
  const xLabels = days
    .map((d, i) => ({ d, i }))
    .filter(({ i }) => i % labelEvery === 0 || i === days.length - 1)
    .filter(({ i }, n, all) => i === days.length - 1 || all[n + 1]!.i - i >= labelEvery / 2)

  // Etiquetas finales solo para las series que no chocan con ninguna otra: si dos líneas
  // acaban juntas no se apilan etiquetas (las identifican la leyenda y el recuadro).
  const last = days[days.length - 1]
  const ends = last
    ? SERIES.map((s) => ({ key: s.key, y: y(last[s.key]), value: last[s.key] }))
    : []
  const endLabels: { key: SeriesKey; y: number; value: number }[] = showEndLabels
    ? ends.filter((e) => ends.every((o) => o.key === e.key || Math.abs(o.y - e.y) >= LABEL_GAP))
    : []

  function onPointerMove(event: PointerEvent<SVGSVGElement>) {
    const box = event.currentTarget.getBoundingClientRect()
    const scale = width / box.width
    const px = (event.clientX - box.left) * scale
    const index = Math.round(((px - margin.left) / plotWidth) * (days.length - 1))
    setActive(Math.min(days.length - 1, Math.max(0, days.length <= 1 ? 0 : index)))
  }

  function onKeyDown(event: KeyboardEvent<SVGSVGElement>) {
    const step = { ArrowLeft: -1, ArrowRight: 1, Home: -days.length, End: days.length }[event.key]
    if (step === undefined) return
    event.preventDefault()
    const current = active ?? (step > 0 ? -1 : days.length)
    setActive(Math.min(days.length - 1, Math.max(0, current + step)))
  }

  const activeDay = active !== null ? days[active] : undefined

  return (
    <div ref={containerRef} className="relative">
      <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-sm" aria-label={t.dashboard.legend}>
        {SERIES.map((s) => (
          <li key={s.key} className="flex items-center gap-2">
            <svg width="20" height="8" aria-hidden>
              <line
                x1="0"
                y1="4"
                x2="20"
                y2="4"
                stroke={s.color}
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
            {t.dashboard.series[s.key]}
          </li>
        ))}
      </ul>
      <svg
        width="100%"
        viewBox={`0 0 ${width} ${HEIGHT}`}
        role="img"
        tabIndex={0}
        aria-label={t.dashboard.chartLabel}
        aria-describedby="grafica-ayuda"
        onPointerMove={onPointerMove}
        onPointerLeave={() => setActive(null)}
        onKeyDown={onKeyDown}
        onBlur={() => setActive(null)}
        className="block touch-pan-y"
      >
        {ticks.map((tick) => (
          <g key={tick}>
            <line
              x1={margin.left}
              x2={margin.left + plotWidth}
              y1={y(tick)}
              y2={y(tick)}
              stroke="var(--color-borde)"
              strokeWidth="1"
            />
            <text
              x={margin.left - 8}
              y={y(tick)}
              textAnchor="end"
              dominantBaseline="middle"
              className="fill-texto-secundario text-xs"
            >
              {formatNumber(tick)}
            </text>
          </g>
        ))}
        {xLabels.map(({ d, i }) => (
          <text
            key={d.day}
            x={x(i)}
            y={HEIGHT - 8}
            textAnchor={
              days.length <= 1
                ? 'middle'
                : i === 0
                  ? 'start'
                  : i === days.length - 1
                    ? 'end'
                    : 'middle'
            }
            className="fill-texto-secundario text-xs"
          >
            {formatDay(d.day).slice(0, 5)}
          </text>
        ))}
        {activeDay && active !== null && (
          <line
            x1={x(active)}
            x2={x(active)}
            y1={margin.top}
            y2={margin.top + plotHeight}
            stroke="var(--color-texto-secundario)"
            strokeWidth="1"
          />
        )}
        {SERIES.map((s) => (
          <g key={s.key}>
            {days.length > 1 && (
              <polyline
                fill="none"
                stroke={s.color}
                strokeWidth="2"
                strokeLinejoin="round"
                strokeLinecap="round"
                points={days.map((d, i) => `${x(i)},${y(d[s.key])}`).join(' ')}
              />
            )}
            {last && (
              <circle
                cx={x(days.length - 1)}
                cy={y(last[s.key])}
                r="4"
                fill={s.color}
                stroke="var(--color-fondo)"
                strokeWidth="2"
              />
            )}
            {activeDay && active !== null && (
              <circle
                cx={x(active)}
                cy={y(activeDay[s.key])}
                r="4"
                fill={s.color}
                stroke="var(--color-fondo)"
                strokeWidth="2"
              />
            )}
          </g>
        ))}
        {endLabels.map((label) => (
          <text
            key={label.key}
            x={margin.left + plotWidth + 10}
            y={label.y}
            dominantBaseline="middle"
            className="fill-texto text-xs"
          >
            <tspan className="font-semibold">{formatNumber(label.value)}</tspan>{' '}
            {t.dashboard.unit(label.key, label.value)}
          </text>
        ))}
      </svg>
      <p id="grafica-ayuda" className="sr-only">
        {t.dashboard.chartHelp}
      </p>
      {activeDay && active !== null && (
        <div
          role="status"
          className="pointer-events-none absolute top-8 rounded border border-borde bg-fondo px-3 py-2 text-sm shadow"
          style={
            x(active) > width / 2
              ? { right: `${((width - x(active)) / width) * 100 + 2}%` }
              : { left: `${(x(active) / width) * 100 + 2}%` }
          }
        >
          <p className="mb-1 text-xs text-texto-secundario">{formatDay(activeDay.day)}</p>
          <ul>
            {SERIES.map((s) => (
              <li key={s.key} className="flex items-center gap-2">
                <svg width="12" height="8" aria-hidden>
                  <line
                    x1="0"
                    y1="4"
                    x2="12"
                    y2="4"
                    stroke={s.color}
                    strokeWidth="2"
                    strokeLinecap="round"
                  />
                </svg>
                <span className="font-semibold text-texto">{formatNumber(activeDay[s.key])}</span>
                <span className="text-texto-secundario">
                  {t.dashboard.unit(s.key, activeDay[s.key])}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
