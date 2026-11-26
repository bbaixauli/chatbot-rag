import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, CheckCircle2, CircleSlash, Download, HelpCircle } from 'lucide-react'
import { useEffect, useState, type ReactNode } from 'react'
import Markdown from 'react-markdown'
import { useSearchParams } from 'react-router'
import remarkGfm from 'remark-gfm'
import { ApiError } from '../../api/client'
import {
  exportUrl,
  getDashboardSummary,
  getLoggedQuestions,
  getNoInfoPhrases,
  saveNoInfoPhrases,
  type DashboardSummary,
  type DateRange,
  type LoggedQuestion,
  type QuestionResult,
} from '../../api/dashboard'
import { DailyChart } from '../../components/DailyChart'
import { usePageTitle } from '../../hooks/usePageTitle'
import { t } from '../../i18n/es'
import {
  PRESET_IDS,
  SERIES,
  formatDay,
  formatLocalDateTime,
  formatNumber,
  formatPercent,
  formatSeconds,
  matchingPreset,
  plainText,
  presetRange,
} from '../../utils/dashboard'
import { ADMIN_SESSION_KEY } from './session'

const PAGE_SIZES = [10, 25, 50, 100]
const DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/

/** Rango de la URL (`?from=…&to=…`); por defecto, los últimos 30 días. */
function useRange(): [DateRange, (range: DateRange) => void] {
  const [params, setParams] = useSearchParams()
  const from = params.get('from') ?? ''
  const to = params.get('to') ?? ''
  const range =
    DATE_PATTERN.test(from) && DATE_PATTERN.test(to) && from <= to
      ? { from, to }
      : presetRange('last30')
  return [range, (next) => setParams({ from: next.from, to: next.to }, { replace: true })]
}

/** Cuadro de mando: actividad, tiempos de respuesta y preguntas del rango elegido. */
export function DashboardPage() {
  usePageTitle(t.dashboard.title)
  const queryClient = useQueryClient()
  const [range, setRange] = useRange()
  const summary = useQuery({
    queryKey: ['admin', 'dashboard', 'summary', range],
    queryFn: () => getDashboardSummary(range),
    placeholderData: keepPreviousData,
  })

  const unauthorized = summary.error instanceof ApiError && summary.error.status === 401
  useEffect(() => {
    if (unauthorized) void queryClient.invalidateQueries({ queryKey: ADMIN_SESSION_KEY })
  }, [unauthorized, queryClient])

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold">{t.dashboard.title}</h1>
        <p className="text-sm text-texto-secundario">{t.dashboard.description}</p>
      </div>
      <RangeFilter range={range} onChange={setRange} />
      {summary.isPending ? (
        <p className="text-texto-secundario">{t.common.loading}</p>
      ) : summary.isError ? (
        <p role="alert" className="text-error">
          {t.dashboard.loadError}
        </p>
      ) : (
        // Mientras se recargan, los datos anteriores se atenúan (sin saltos).
        <div
          aria-busy={summary.isPlaceholderData}
          className={`flex flex-col gap-6 ${summary.isPlaceholderData ? 'opacity-60' : ''}`}
        >
          <Figures summary={summary.data} />
          <Activity summary={summary.data} />
          <Questions range={range} />
          <NoInfoSettings />
        </div>
      )}
    </div>
  )
}

function RangeFilter({ range, onChange }: { range: DateRange; onChange: (r: DateRange) => void }) {
  const active = matchingPreset(range)
  const [from, setFrom] = useState(range.from)
  const [to, setTo] = useState(range.to)
  // Si el rango cambia desde fuera (atajos), los campos lo reflejan.
  const [shown, setShown] = useState(range)
  if (shown.from !== range.from || shown.to !== range.to) {
    setShown(range)
    setFrom(range.from)
    setTo(range.to)
  }
  const invalid = !from || !to || from > to
  return (
    <form
      className="flex flex-wrap items-end gap-x-4 gap-y-3 rounded-lg border border-borde bg-fondo p-4"
      onSubmit={(e) => {
        e.preventDefault()
        if (!invalid) onChange({ from, to })
      }}
    >
      <fieldset className="flex flex-col gap-1">
        <legend className="mb-1 text-sm font-medium">{t.dashboard.range}</legend>
        <div className="flex flex-wrap gap-2">
          {PRESET_IDS.map((id) => (
            <button
              key={id}
              type="button"
              aria-pressed={active === id}
              onClick={() => onChange(presetRange(id))}
              className={`btn ${active === id ? 'btn-primario' : 'btn-secundario'}`}
            >
              {t.dashboard.presets[id]}
            </button>
          ))}
        </div>
      </fieldset>
      <div className="flex flex-wrap items-end gap-3">
        <label className="flex flex-col gap-1 text-sm font-medium">
          {t.dashboard.from}
          <input
            type="date"
            value={from}
            max={to || undefined}
            onChange={(e) => setFrom(e.target.value)}
            className="campo"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm font-medium">
          {t.dashboard.to}
          <input
            type="date"
            value={to}
            min={from || undefined}
            onChange={(e) => setTo(e.target.value)}
            className="campo"
          />
        </label>
        <button type="submit" className="btn btn-secundario">
          {t.dashboard.apply}
        </button>
      </div>
      {invalid && (
        <p role="alert" className="w-full text-sm text-error">
          {t.dashboard.invalidRange}
        </p>
      )}
    </form>
  )
}

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3 rounded-lg border border-borde bg-fondo p-4 md:p-6">
      <h2 className="text-lg font-bold">{title}</h2>
      {children}
    </section>
  )
}

function Figure(props: { label: string; value: string; detail?: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-lg border border-borde bg-fondo p-4">
      <dt className="text-sm text-texto-secundario">{props.label}</dt>
      <dd className="text-3xl font-bold">{props.value}</dd>
      {props.detail && <dd className="text-xs text-texto-secundario">{props.detail}</dd>}
    </div>
  )
}

function Figures({ summary }: { summary: DashboardSummary }) {
  return (
    <section aria-labelledby="cifras">
      <h2 id="cifras" className="sr-only">
        {t.dashboard.figures(formatDay(summary.start), formatDay(summary.end))}
      </h2>
      <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
        <Figure label={t.dashboard.sessions} value={formatNumber(summary.sessions)} />
        <Figure
          label={t.dashboard.users}
          value={formatNumber(summary.users)}
          detail={t.dashboard.usersHint}
        />
        <Figure label={t.dashboard.questions} value={formatNumber(summary.questions)} />
        <Figure
          label={t.dashboard.avgResponse}
          value={formatSeconds(summary.avg_response_ms)}
          detail={t.dashboard.responseDetail(
            formatSeconds(summary.median_response_ms),
            formatSeconds(summary.avg_first_token_ms),
          )}
        />
        <Figure
          label={t.dashboard.answered}
          value={formatPercent(summary.answered_ratio)}
          detail={t.dashboard.answeredDetail(
            formatNumber(summary.no_info),
            formatNumber(summary.errors),
            formatNumber(summary.without_sources),
            formatNumber(summary.trimmed),
          )}
        />
      </dl>
    </section>
  )
}

function Activity({ summary }: { summary: DashboardSummary }) {
  const [asTable, setAsTable] = useState(false)
  return (
    <Card title={t.dashboard.activity}>
      <div className="flex justify-end">
        <button
          type="button"
          aria-pressed={asTable}
          onClick={() => setAsTable((v) => !v)}
          className="btn btn-secundario"
        >
          {asTable ? t.dashboard.showChart : t.dashboard.showTable}
        </button>
      </div>
      {summary.questions === 0 ? (
        <p className="text-texto-secundario">{t.dashboard.emptyActivity}</p>
      ) : asTable ? (
        <div
          role="region"
          aria-label={t.dashboard.activityTable}
          tabIndex={0}
          className="max-h-96 overflow-auto rounded border border-borde"
        >
          <table className="w-full text-sm">
            <caption className="sr-only">{t.dashboard.activityTable}</caption>
            <thead className="sticky top-0 bg-fondo-alt">
              <tr>
                <th scope="col" className="px-3 py-2 text-left font-semibold">
                  {t.dashboard.day}
                </th>
                {SERIES.map((s) => (
                  <th key={s.key} scope="col" className="px-3 py-2 text-right font-semibold">
                    {t.dashboard.series[s.key]}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {summary.days.map((d) => (
                <tr key={d.day} className="border-t border-borde">
                  <th scope="row" className="px-3 py-1.5 text-left font-normal">
                    {formatDay(d.day)}
                  </th>
                  {SERIES.map((s) => (
                    <td key={s.key} className="px-3 py-1.5 text-right tabular-nums">
                      {formatNumber(d[s.key])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <DailyChart days={summary.days} />
      )}
    </Card>
  )
}

const RESULT_STYLES: Record<QuestionResult, { icon: typeof CheckCircle2; className: string }> = {
  answered: { icon: CheckCircle2, className: 'text-exito' },
  no_info: { icon: HelpCircle, className: 'text-aviso' },
  error: { icon: AlertTriangle, className: 'text-error' },
  cancelled: { icon: CircleSlash, className: 'text-texto-secundario' },
}

function ResultLabel({ result }: { result: QuestionResult }) {
  const { icon: Icon, className } = RESULT_STYLES[result]
  // Siempre icono y texto: el color no es el único indicador.
  return (
    <span className={`inline-flex items-center gap-1 whitespace-nowrap ${className}`}>
      <Icon aria-hidden size={16} />
      {t.dashboard.results[result]}
    </span>
  )
}

function Questions({ range }: { range: DateRange }) {
  const [page, setPage] = useState(1)
  const [size, setSize] = useState(25)
  // Al cambiar el rango se vuelve a la primera página.
  const [shownRange, setShownRange] = useState(range)
  if (shownRange.from !== range.from || shownRange.to !== range.to) {
    setShownRange(range)
    setPage(1)
  }
  const questions = useQuery({
    queryKey: ['admin', 'dashboard', 'questions', range, page, size],
    queryFn: () => getLoggedQuestions(range, page, size),
    placeholderData: keepPreviousData,
  })
  const data = questions.data
  const total = data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / size))
  const first = total === 0 ? 0 : (page - 1) * size + 1
  const last = Math.min(page * size, total)

  return (
    <Card title={t.dashboard.questionsTitle}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-texto-secundario">{t.dashboard.questionsHint}</p>
        <div className="flex flex-wrap gap-2">
          <a href={exportUrl(range, 'csv')} download className="btn btn-secundario">
            <Download aria-hidden size={16} />
            {t.dashboard.exportCsv}
          </a>
          <a href={exportUrl(range, 'xlsx')} download className="btn btn-secundario">
            <Download aria-hidden size={16} />
            {t.dashboard.exportXlsx}
          </a>
        </div>
      </div>
      {questions.isPending ? (
        <p className="text-texto-secundario">{t.common.loading}</p>
      ) : questions.isError ? (
        <p role="alert" className="text-error">
          {t.dashboard.questionsError}
        </p>
      ) : total === 0 ? (
        <p className="text-texto-secundario">{t.dashboard.emptyQuestions}</p>
      ) : (
        <>
          <div
            role="region"
            aria-label={t.dashboard.questionsTitle}
            tabIndex={0}
            aria-busy={questions.isPlaceholderData}
            className={`overflow-x-auto rounded border border-borde ${questions.isPlaceholderData ? 'opacity-60' : ''}`}
          >
            <table className="w-full text-left text-sm">
              <caption className="sr-only">{t.dashboard.questionsTitle}</caption>
              <thead className="bg-fondo-alt">
                <tr>
                  {[
                    t.dashboard.columns.askedAt,
                    t.dashboard.columns.question,
                    t.dashboard.columns.answer,
                    t.dashboard.columns.time,
                    t.dashboard.columns.result,
                    t.dashboard.columns.sources,
                  ].map((label, i) => (
                    <th
                      key={label}
                      scope="col"
                      className={`px-3 py-2 font-semibold ${i >= 3 && i !== 4 ? 'text-right' : ''}`}
                    >
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data!.items.map((q) => (
                  <QuestionRow key={q.id} question={q} />
                ))}
              </tbody>
            </table>
          </div>
          <nav
            aria-label={t.dashboard.pagination}
            className="flex flex-wrap items-center justify-between gap-3 text-sm"
          >
            <p aria-live="polite">
              {t.dashboard.showing(formatNumber(first), formatNumber(last), formatNumber(total))}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <label className="flex items-center gap-2">
                {t.dashboard.pageSize}
                <select
                  value={size}
                  onChange={(e) => {
                    setSize(Number(e.target.value))
                    setPage(1)
                  }}
                  className="campo"
                >
                  {PAGE_SIZES.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
              <button
                type="button"
                className="btn btn-secundario"
                disabled={page <= 1}
                onClick={() => setPage((p) => p - 1)}
              >
                {t.dashboard.previous}
              </button>
              <button
                type="button"
                className="btn btn-secundario"
                disabled={page >= pages}
                onClick={() => setPage((p) => p + 1)}
              >
                {t.dashboard.next}
              </button>
            </div>
          </nav>
        </>
      )}
    </Card>
  )
}

function QuestionRow({ question }: { question: LoggedQuestion }) {
  return (
    <tr className="border-t border-borde align-top">
      <td className="px-3 py-2 whitespace-nowrap tabular-nums">
        {formatLocalDateTime(question.asked_at)}
      </td>
      <td className="min-w-48 px-3 py-2 break-words">{question.question}</td>
      <td className="min-w-64 px-3 py-2">
        {question.answer ? (
          <details>
            <summary className="cursor-pointer">
              <span className="line-clamp-2 inline">{plainText(question.answer)}</span>
            </summary>
            <div className="mt-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:mb-2 [&_pre]:overflow-x-auto [&_ul]:list-disc [&_ul]:pl-5">
              <Markdown remarkPlugins={[remarkGfm]}>{question.answer}</Markdown>
            </div>
          </details>
        ) : (
          <span className="text-texto-secundario">—</span>
        )}
      </td>
      <td className="px-3 py-2 text-right whitespace-nowrap tabular-nums">
        {formatSeconds(question.response_ms)}
      </td>
      <td className="px-3 py-2">
        <ResultLabel result={question.result} />
      </td>
      <td className="px-3 py-2 text-right tabular-nums">{question.sources_cited}</td>
    </tr>
  )
}

function NoInfoSettings() {
  const queryClient = useQueryClient()
  const phrases = useQuery({
    queryKey: ['admin', 'dashboard', 'phrases'],
    queryFn: getNoInfoPhrases,
  })
  const [text, setText] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: (list: string[]) => saveNoInfoPhrases(list),
    onSuccess: (data) => {
      queryClient.setQueryData(['admin', 'dashboard', 'phrases'], data)
      setText(null)
      // Las respuestas se han reclasificado: se recalculan las cifras y la tabla.
      void queryClient.invalidateQueries({ queryKey: ['admin', 'dashboard', 'summary'] })
      void queryClient.invalidateQueries({ queryKey: ['admin', 'dashboard', 'questions'] })
    },
  })
  const current = text ?? phrases.data?.phrases.join('\n') ?? ''
  return (
    <details className="rounded-lg border border-borde bg-fondo p-4 md:p-6">
      <summary className="cursor-pointer text-lg font-bold">{t.dashboard.noInfoTitle}</summary>
      <div className="mt-3 flex flex-col gap-3">
        <p className="text-sm text-texto-secundario">{t.dashboard.noInfoHint}</p>
        {phrases.isError ? (
          <p role="alert" className="text-sm text-error">
            {t.dashboard.noInfoLoadError}
          </p>
        ) : (
          <form
            className="flex flex-col gap-3"
            onSubmit={(e) => {
              e.preventDefault()
              save.mutate(current.split('\n'))
            }}
          >
            <label htmlFor="frases" className="text-sm font-medium">
              {t.dashboard.noInfoLabel}
            </label>
            <textarea
              id="frases"
              rows={8}
              value={current}
              onChange={(e) => {
                setText(e.target.value)
                save.reset()
              }}
              className="campo text-sm"
            />
            <div className="flex flex-wrap items-center justify-end gap-3">
              {save.isSuccess && (
                <p role="status" className="text-sm text-exito">
                  {t.dashboard.noInfoSaved}
                </p>
              )}
              {save.isError && (
                <p role="alert" className="text-sm text-error">
                  {save.error instanceof ApiError && save.error.status === 422
                    ? t.dashboard.noInfoInvalid
                    : t.dashboard.noInfoSaveError}
                </p>
              )}
              <button
                type="button"
                className="btn btn-secundario"
                disabled={!phrases.data}
                onClick={() => setText(phrases.data!.defaults.join('\n'))}
              >
                {t.dashboard.noInfoDefaults}
              </button>
              <button type="submit" className="btn btn-primario" disabled={save.isPending}>
                {t.dashboard.noInfoSave}
              </button>
            </div>
          </form>
        )}
      </div>
    </details>
  )
}
