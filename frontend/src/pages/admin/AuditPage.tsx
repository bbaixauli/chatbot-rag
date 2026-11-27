import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Download, Info, ShieldAlert } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useSearchParams } from 'react-router'
import {
  auditLogExportUrl,
  getAuditSummary,
  listAlerts,
  listAuditLog,
  listAuditRuns,
  sendTestEmail,
  startAuditRun,
  updateAlert,
  type AlertFilters,
  type AlertStatus,
  type AuditAlert,
  type AuditEntry,
  type AuditRun,
  type AuditSummary,
  type LogFilters,
  type Severity,
} from '../../api/audit'
import { ApiError } from '../../api/client'
import { usePageTitle } from '../../hooks/usePageTitle'
import { t } from '../../i18n/es'
import {
  formatLocalDateTime,
  formatNumber,
  formatSeconds,
  presetRange,
} from '../../utils/dashboard'

const SUMMARY_KEY = ['admin', 'audit', 'summary'] as const
const PAGE_SIZES = [10, 25, 50, 100]
type View = 'alerts' | 'runs' | 'log'
const VIEWS: View[] = ['alerts', 'runs', 'log']

const dateTime = new Intl.DateTimeFormat('es-ES', {
  timeZone: 'Europe/Madrid',
  day: '2-digit',
  month: '2-digit',
  year: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

/** Fecha y hora (dd/mm/aaaa hh:mm) de un instante en UTC, en hora de Madrid. */
const formatInstant = (iso: string | null) =>
  iso ? dateTime.format(new Date(iso)).replace(',', '') : '—'

/** Auditoría: avisos, ejecuciones del proceso de revisión y registro completo. */
export function AuditPage() {
  usePageTitle(t.audit.title)
  const [params, setParams] = useSearchParams()
  const view = (VIEWS as string[]).includes(params.get('vista') ?? '')
    ? (params.get('vista') as View)
    : 'alerts'
  const summary = useQuery({
    queryKey: SUMMARY_KEY,
    queryFn: getAuditSummary,
    // Mientras hay una revisión en curso se sigue su estado.
    refetchInterval: (query) => (query.state.data?.running ? 3000 : 60_000),
  })
  // Al terminar una revisión se recargan los avisos, las revisiones y el registro.
  const queryClient = useQueryClient()
  const runningId = summary.data?.running?.id ?? null
  const previousRunningId = useRef(runningId)
  useEffect(() => {
    if (previousRunningId.current !== null && runningId === null) {
      void queryClient.invalidateQueries({
        queryKey: ['admin', 'audit'],
        predicate: (query) => query.queryKey[2] !== 'summary',
      })
    }
    previousRunningId.current = runningId
  }, [runningId, queryClient])

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold">{t.audit.title}</h1>
        <p className="text-sm text-texto-secundario">{t.audit.description}</p>
      </div>
      <RunControls
        running={summary.data?.running ?? null}
        lastRun={summary.data?.last_run ?? null}
      />
      <div role="group" aria-label={t.audit.views} className="flex flex-wrap gap-2">
        {VIEWS.map((id) => (
          <button
            key={id}
            type="button"
            aria-pressed={view === id}
            onClick={() => setParams({ vista: id }, { replace: true })}
            className={`btn ${view === id ? 'btn-primario' : 'btn-secundario'}`}
          >
            {t.audit.viewLabels[id]}{' '}
            {id === 'alerts' && summary.data && summary.data.new_alerts > 0 && (
              <span className="font-normal">({formatNumber(summary.data.new_alerts)})</span>
            )}
          </button>
        ))}
      </div>
      {view === 'alerts' && <AlertsView />}
      {view === 'runs' && <RunsView />}
      {view === 'log' && <LogView />}
    </div>
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

function RunControls({ running, lastRun }: { running: AuditRun | null; lastRun: AuditRun | null }) {
  const queryClient = useQueryClient()
  const start = useMutation({
    mutationFn: startAuditRun,
    // Se marca en curso aunque termine enseguida: al verla terminada se recargan los avisos.
    onSuccess: (run) => {
      queryClient.setQueryData<AuditSummary>(SUMMARY_KEY, (old) =>
        old ? { ...old, running: run } : old,
      )
      void queryClient.invalidateQueries({ queryKey: SUMMARY_KEY })
    },
  })
  const testEmail = useMutation({ mutationFn: sendTestEmail })
  const conflict = start.error instanceof ApiError && start.error.status === 409
  const emailNotConfigured = testEmail.error instanceof ApiError && testEmail.error.status === 409

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-borde bg-fondo p-4">
      <div aria-live="polite" className="text-sm">
        {running ? (
          <p className="font-semibold">{t.audit.running(formatInstant(running.started_at))}</p>
        ) : lastRun ? (
          <p>
            {t.audit.lastRun(formatInstant(lastRun.started_at))}{' '}
            <RunStatusLabel status={lastRun.status} /> · {t.audit.runAlerts(lastRun.alerts)}
          </p>
        ) : (
          <p className="text-texto-secundario">{t.audit.neverRun}</p>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button
          type="button"
          className="btn btn-secundario"
          disabled={testEmail.isPending}
          onClick={() => testEmail.mutate()}
        >
          {t.audit.testEmail}
        </button>
        <button
          type="button"
          className="btn btn-primario"
          disabled={Boolean(running) || start.isPending}
          onClick={() => start.mutate()}
        >
          {t.audit.runNow}
        </button>
      </div>
      {(start.isError || testEmail.isError || testEmail.isSuccess) && (
        <div className="w-full text-sm">
          {start.isError && (
            <p role="alert" className="text-error">
              {conflict ? t.audit.alreadyRunning : t.audit.runError}
            </p>
          )}
          {testEmail.isSuccess && (
            <p role="status" className="text-exito">
              {t.audit.testEmailSent}
            </p>
          )}
          {testEmail.isError && (
            <p role="alert" className="text-error">
              {emailNotConfigured ? t.audit.testEmailNotConfigured : t.audit.testEmailError}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function RunStatusLabel({ status }: { status: AuditRun['status'] }) {
  const style = {
    running: 'text-info',
    ok: 'text-exito',
    partial: 'text-aviso',
    failed: 'text-error',
  }[status]
  return <span className={`font-semibold ${style}`}>{t.audit.runStatus[status]}</span>
}

const SEVERITY_STYLES: Record<Severity, string> = {
  low: 'border-info bg-info-suave text-info',
  medium: 'border-aviso bg-aviso-suave text-aviso',
  high: 'border-error bg-error-suave text-error',
}

function SeverityLabel({ severity }: { severity: Severity }) {
  const Icon = severity === 'high' ? ShieldAlert : severity === 'medium' ? AlertTriangle : Info
  // Siempre icono y texto: el color no es el único indicador.
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-xs font-semibold ${SEVERITY_STYLES[severity]}`}
    >
      <Icon aria-hidden size={14} />
      {t.audit.severity[severity]}
    </span>
  )
}

function Pagination(props: {
  total: number
  page: number
  size: number
  onPage: (page: number) => void
  onSize: (size: number) => void
  label: string
}) {
  const pages = Math.max(1, Math.ceil(props.total / props.size))
  const first = props.total === 0 ? 0 : (props.page - 1) * props.size + 1
  const last = Math.min(props.page * props.size, props.total)
  return (
    <nav
      aria-label={props.label}
      className="flex flex-wrap items-center justify-between gap-3 text-sm"
    >
      <p aria-live="polite">
        {t.dashboard.showing(formatNumber(first), formatNumber(last), formatNumber(props.total))}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2">
          {t.dashboard.pageSize}
          <select
            value={props.size}
            onChange={(e) => props.onSize(Number(e.target.value))}
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
          disabled={props.page <= 1}
          onClick={() => props.onPage(props.page - 1)}
        >
          {t.dashboard.previous}
        </button>
        <button
          type="button"
          className="btn btn-secundario"
          disabled={props.page >= pages}
          onClick={() => props.onPage(props.page + 1)}
        >
          {t.dashboard.next}
        </button>
      </div>
    </nav>
  )
}

// --- Avisos ---

function AlertsView() {
  const [filters, setFilters] = useState<AlertFilters>({ status: 'new' })
  const [page, setPage] = useState(1)
  const [size, setSize] = useState(25)
  const alerts = useQuery({
    queryKey: ['admin', 'audit', 'alerts', filters, page, size],
    queryFn: () => listAlerts(filters, page, size),
    placeholderData: keepPreviousData,
  })
  const setFilter = (change: Partial<AlertFilters>) => {
    setFilters((current) => ({ ...current, ...change }))
    setPage(1)
  }
  const select = (
    id: string,
    label: string,
    value: string | undefined,
    options: Record<string, string>,
    onChange: (value: string) => void,
  ) => (
    <label htmlFor={id} className="flex flex-col gap-1 text-sm font-medium">
      {label}
      <select
        id={id}
        value={value ?? ''}
        onChange={(e) => onChange(e.target.value)}
        className="campo"
      >
        <option value="">{t.audit.all}</option>
        {Object.entries(options).map(([key, text]) => (
          <option key={key} value={key}>
            {text}
          </option>
        ))}
      </select>
    </label>
  )

  return (
    <Card title={t.audit.alertsTitle}>
      <div className="flex flex-wrap gap-3">
        {select('filtro-estado', t.audit.filters.status, filters.status, t.audit.alertStatus, (v) =>
          setFilter({ status: (v || undefined) as AlertStatus | undefined }),
        )}
        {select(
          'filtro-categoria',
          t.audit.filters.category,
          filters.category,
          t.audit.category,
          (v) => setFilter({ category: (v || undefined) as AlertFilters['category'] }),
        )}
        {select(
          'filtro-gravedad',
          t.audit.filters.severity,
          filters.severity,
          t.audit.severity,
          (v) => setFilter({ severity: (v || undefined) as Severity | undefined }),
        )}
      </div>
      {alerts.isPending ? (
        <p className="text-texto-secundario">{t.common.loading}</p>
      ) : alerts.isError ? (
        <p role="alert" className="text-error">
          {t.audit.loadError}
        </p>
      ) : alerts.data.total === 0 ? (
        <p className="text-texto-secundario">{t.audit.noAlerts}</p>
      ) : (
        <>
          <ul
            aria-label={t.audit.alertsTitle}
            aria-busy={alerts.isPlaceholderData}
            className={`flex flex-col gap-4 ${alerts.isPlaceholderData ? 'opacity-60' : ''}`}
          >
            {alerts.data.items.map((alert) => (
              <AlertItem key={alert.id} alert={alert} />
            ))}
          </ul>
          <Pagination
            label={t.audit.alertsPagination}
            total={alerts.data.total}
            page={page}
            size={size}
            onPage={setPage}
            onSize={(n) => {
              setSize(n)
              setPage(1)
            }}
          />
        </>
      )}
    </Card>
  )
}

function Who({ entry }: { entry: AuditEntry }) {
  return (
    <p className="text-sm text-texto-secundario">
      {formatLocalDateTime(entry.asked_at)} · {entry.user_display_name ?? t.audit.unknownUser}
      {entry.user_id && entry.user_id !== entry.user_display_name && ` (${entry.user_id})`} ·{' '}
      {t.audit.ip(entry.client_ip ?? '—')}
    </p>
  )
}

function AlertItem({ alert }: { alert: AuditAlert }) {
  const queryClient = useQueryClient()
  const [note, setNote] = useState(alert.note ?? '')
  const update = useMutation({
    mutationFn: (status: AlertStatus) => updateAlert(alert.id, status, note || null),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['admin', 'audit'] }),
  })
  const noteId = `nota-${alert.id}`
  return (
    <li className="flex flex-col gap-3 rounded-lg border border-borde p-4">
      <div className="flex flex-wrap items-center gap-2">
        <SeverityLabel severity={alert.severity} />
        <h3 className="font-semibold">{t.audit.category[alert.category]}</h3>
        <span className="text-xs text-texto-secundario">
          · {t.audit.source[alert.source]} ·{' '}
          {t.audit.alertDate(formatLocalDateTime(alert.created_at))}
        </span>
      </div>
      <p>{alert.reason}</p>
      {alert.excerpt && (
        <p className="rounded bg-fondo-alt px-3 py-2 font-mono text-sm break-words">
          {alert.excerpt}
        </p>
      )}
      <Who entry={alert.entry} />
      <details>
        <summary className="cursor-pointer text-sm font-medium">{t.audit.showConversation}</summary>
        <dl className="mt-2 grid gap-2 text-sm">
          <dt className="font-semibold">{t.audit.question}</dt>
          <dd className="whitespace-pre-wrap break-words">{alert.entry.question}</dd>
          <dt className="font-semibold">{t.audit.answer}</dt>
          <dd className="whitespace-pre-wrap break-words">{alert.entry.answer ?? '—'}</dd>
        </dl>
      </details>
      <div className="flex flex-col gap-2 border-t border-borde pt-3">
        <p className="text-sm">
          {t.audit.statusLine(t.audit.alertStatus[alert.status])}
          {alert.reviewed_by &&
            ` · ${t.audit.reviewedBy(alert.reviewed_by, formatInstant(alert.reviewed_at))}`}
        </p>
        <label htmlFor={noteId} className="text-sm font-medium">
          {t.audit.note}
        </label>
        <textarea
          id={noteId}
          rows={2}
          maxLength={2000}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          className="campo text-sm"
        />
        <div className="flex flex-wrap gap-2">
          {alert.status !== 'reviewed' && (
            <button
              type="button"
              className="btn btn-primario"
              onClick={() => update.mutate('reviewed')}
            >
              {t.audit.markReviewed}
            </button>
          )}
          {alert.status !== 'dismissed' && (
            <button
              type="button"
              className="btn btn-secundario"
              onClick={() => update.mutate('dismissed')}
            >
              {t.audit.dismiss}
            </button>
          )}
          {alert.status !== 'new' && (
            <button
              type="button"
              className="btn btn-terciario"
              onClick={() => update.mutate('new')}
            >
              {t.audit.reopen}
            </button>
          )}
        </div>
        {update.isError && (
          <p role="alert" className="text-sm text-error">
            {t.audit.updateError}
          </p>
        )}
      </div>
    </li>
  )
}

// --- Ejecuciones ---

function RunsView() {
  const runs = useQuery({ queryKey: ['admin', 'audit', 'runs'], queryFn: listAuditRuns })
  return (
    <Card title={t.audit.runsTitle}>
      {runs.isPending ? (
        <p className="text-texto-secundario">{t.common.loading}</p>
      ) : runs.isError ? (
        <p role="alert" className="text-error">
          {t.audit.loadError}
        </p>
      ) : runs.data.length === 0 ? (
        <p className="text-texto-secundario">{t.audit.neverRun}</p>
      ) : (
        <div
          role="region"
          aria-label={t.audit.runsTitle}
          tabIndex={0}
          className="overflow-x-auto rounded border border-borde"
        >
          <table className="w-full text-left text-sm">
            <caption className="sr-only">{t.audit.runsTitle}</caption>
            <thead className="bg-fondo-alt">
              <tr>
                {Object.values(t.audit.runColumns).map((label) => (
                  <th key={label} scope="col" className="px-3 py-2 font-semibold">
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {runs.data.map((run) => (
                <tr key={run.id} className="border-t border-borde align-top">
                  <td className="px-3 py-2 whitespace-nowrap tabular-nums">
                    {formatInstant(run.started_at)}
                  </td>
                  <td className="px-3 py-2">
                    {t.audit.trigger[run.trigger]}
                    {run.triggered_by && ` (${run.triggered_by})`}
                  </td>
                  <td className="px-3 py-2">
                    <RunStatusLabel status={run.status} />
                    {run.error && <p className="text-xs text-error">{run.error}</p>}
                    {run.notification_error && (
                      <p className="text-xs text-aviso">
                        {t.audit.emailFailed(run.notification_error)}
                      </p>
                    )}
                  </td>
                  <td className="px-3 py-2 tabular-nums">
                    {formatNumber(run.reviewed)}
                    {run.used_model && (
                      <span className="block text-xs text-texto-secundario">
                        {t.audit.byModel(formatNumber(run.reviewed_by_model), run.used_model)}
                        {run.model_errors > 0 && ` · ${t.audit.modelErrors(run.model_errors)}`}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 tabular-nums">{formatNumber(run.alerts)}</td>
                  <td className="px-3 py-2 tabular-nums">{formatNumber(run.anonymized)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}

// --- Registro completo ---

function LogView() {
  const initial: LogFilters = presetRange('last7')
  const [filters, setFilters] = useState<LogFilters>(initial)
  const [draft, setDraft] = useState<LogFilters>(initial)
  const [page, setPage] = useState(1)
  const [size, setSize] = useState(25)
  const log = useQuery({
    queryKey: ['admin', 'audit', 'log', filters, page, size],
    queryFn: () => listAuditLog(filters, page, size),
    placeholderData: keepPreviousData,
  })
  const invalid = !draft.from || !draft.to || draft.from > draft.to
  const field = (key: keyof LogFilters, label: string, type: 'date' | 'text') => (
    <label className="flex flex-col gap-1 text-sm font-medium">
      {label}
      <input
        type={type}
        value={draft[key] ?? ''}
        onChange={(e) => setDraft((d) => ({ ...d, [key]: e.target.value }))}
        className="campo"
      />
    </label>
  )
  return (
    <Card title={t.audit.logTitle}>
      <p className="text-sm text-texto-secundario">{t.audit.logHint}</p>
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          if (invalid) return
          setFilters(draft)
          setPage(1)
        }}
      >
        {field('from', t.dashboard.from, 'date')}
        {field('to', t.dashboard.to, 'date')}
        {field('user', t.audit.filters.user, 'text')}
        {field('ip', t.audit.filters.ip, 'text')}
        <button type="submit" className="btn btn-secundario">
          {t.audit.search}
        </button>
        <a href={auditLogExportUrl(filters, 'csv')} download className="btn btn-secundario">
          <Download aria-hidden size={16} />
          {t.dashboard.exportCsv}
        </a>
        <a href={auditLogExportUrl(filters, 'xlsx')} download className="btn btn-secundario">
          <Download aria-hidden size={16} />
          {t.dashboard.exportXlsx}
        </a>
        {invalid && (
          <p role="alert" className="w-full text-sm text-error">
            {t.dashboard.invalidRange}
          </p>
        )}
      </form>
      {log.isPending ? (
        <p className="text-texto-secundario">{t.common.loading}</p>
      ) : log.isError ? (
        <p role="alert" className="text-error">
          {t.audit.loadError}
        </p>
      ) : log.data.total === 0 ? (
        <p className="text-texto-secundario">{t.audit.noEntries}</p>
      ) : (
        <>
          <div
            role="region"
            aria-label={t.audit.logTitle}
            tabIndex={0}
            aria-busy={log.isPlaceholderData}
            className={`overflow-x-auto rounded border border-borde ${log.isPlaceholderData ? 'opacity-60' : ''}`}
          >
            <table className="w-full text-left text-sm">
              <caption className="sr-only">{t.audit.logTitle}</caption>
              <thead className="bg-fondo-alt">
                <tr>
                  {Object.values(t.audit.logColumns).map((label) => (
                    <th key={label} scope="col" className="px-3 py-2 font-semibold">
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {log.data.items.map((entry) => (
                  <LogRow key={entry.id} entry={entry} />
                ))}
              </tbody>
            </table>
          </div>
          <Pagination
            label={t.audit.logPagination}
            total={log.data.total}
            page={page}
            size={size}
            onPage={setPage}
            onSize={(n) => {
              setSize(n)
              setPage(1)
            }}
          />
        </>
      )}
    </Card>
  )
}

function LogRow({ entry }: { entry: AuditEntry }) {
  const documents = (entry.cited_documents ?? []).map((d) => d.title || d.filename).join(', ')
  return (
    <tr className="border-t border-borde align-top">
      <td className="px-3 py-2 whitespace-nowrap tabular-nums">
        {formatLocalDateTime(entry.asked_at)}
      </td>
      <td className="px-3 py-2">
        {entry.user_display_name ?? t.audit.unknownUser}
        {entry.user_id && (
          <span className="block text-xs text-texto-secundario">{entry.user_id}</span>
        )}
      </td>
      <td className="px-3 py-2 whitespace-nowrap">{entry.client_ip ?? '—'}</td>
      <td className="min-w-48 px-3 py-2 break-words">{entry.question}</td>
      <td className="min-w-64 px-3 py-2">
        <details>
          <summary className="cursor-pointer">{t.audit.details}</summary>
          <dl className="mt-2 grid gap-1 text-xs">
            <dt className="font-semibold">{t.audit.answer}</dt>
            <dd className="whitespace-pre-wrap break-words">{entry.answer ?? '—'}</dd>
            <dt className="font-semibold">{t.audit.meta.model}</dt>
            <dd>{entry.model ?? '—'}</dd>
            <dt className="font-semibold">{t.audit.meta.promptVersion}</dt>
            <dd>{entry.prompt_version_id ?? t.audit.meta.defaultPrompt}</dd>
            <dt className="font-semibold">{t.audit.meta.searchQuery}</dt>
            <dd className="break-words">{entry.search_query ?? '—'}</dd>
            <dt className="font-semibold">{t.audit.meta.searchedIn}</dt>
            <dd className="break-words">
              {entry.searched_in ? t.audit.searchedIn(entry.searched_in) : t.audit.everywhere}
            </dd>
            <dt className="font-semibold">{t.audit.meta.documents}</dt>
            <dd className="break-words">{documents || '—'}</dd>
            <dt className="font-semibold">{t.audit.meta.userAgent}</dt>
            <dd className="break-words">{entry.user_agent ?? '—'}</dd>
            <dt className="font-semibold">{t.audit.meta.promptSize}</dt>
            <dd>
              {entry.prompt_tokens_estimated == null
                ? '—'
                : t.audit.promptTokens(entry.prompt_tokens_estimated, entry.prompt_tokens)}
            </dd>
            {entry.prompt_trim && (
              <>
                <dt className="font-semibold">{t.audit.meta.promptTrim}</dt>
                <dd>{t.audit.trim(entry.prompt_trim)}</dd>
              </>
            )}
            {entry.error_detail && (
              <>
                <dt className="font-semibold">{t.audit.meta.error}</dt>
                <dd className="break-words">{entry.error_detail}</dd>
              </>
            )}
          </dl>
        </details>
      </td>
      <td className="px-3 py-2 whitespace-nowrap">
        {t.audit.entryStatus[entry.status] ?? entry.status}
      </td>
      <td className="px-3 py-2 text-right whitespace-nowrap tabular-nums">
        {formatSeconds(entry.response_ms)}
      </td>
    </tr>
  )
}
