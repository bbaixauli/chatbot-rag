import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useState, type ReactNode } from 'react'
import { ApiError } from '../../api/client'
import {
  APP_CONFIG_KEY,
  checkConnection,
  getAppConfig,
  saveConfigGroup,
  type AppConfig,
  type ConfigChanges,
  type ConfigGroup,
  type ConfigGroupName,
  type ConfigSaveResult,
  type SecretState,
} from '../../api/config'
import { reindexAll } from '../../api/documents'
import { usePageTitle } from '../../hooks/usePageTitle'
import { t } from '../../i18n/es'
import { AUTH_SESSION_KEY } from '../../auth/session'
import { ADMIN_SESSION_KEY } from './session'

type FieldKind =
  'text' | 'url' | 'number' | 'boolean' | 'provider' | 'select' | 'secret' | 'textarea'

interface FieldSpec {
  name: string
  kind: FieldKind
  min?: number
  max?: number
  step?: number
  integer?: boolean
  /** Texto que puede quedar vacío (se envía `null`). */
  optional?: boolean
  /** Solo se muestra con ciertos valores del formulario (p. ej. según el proveedor). */
  visible?: (state: FormState) => boolean
  /** Opciones de un campo `select` (los textos están en `t.config.options`). */
  options?: string[]
}

interface GroupSpec {
  name: ConfigGroupName
  fields: FieldSpec[]
  testable?: boolean
  /** La prueba de conexión admite además comprobar un usuario (directorio LDAP). */
  testUser?: boolean
}

const remoteProvider = (state: FormState) => state.values.provider === 'openai'
const ldapMode = (state: FormState) => state.values.auth_mode === 'ldap'
const dailyAudit = (state: FormState) => state.values.schedule === 'daily'
const auditWithModel = (state: FormState) => state.values.use_llm === true
const auditEmail = (state: FormState) => state.values.notify_enabled === true
const stripRepeatedLines = (state: FormState) => state.values.strip_repeated_lines === true

// Rangos: los mismos que valida el servidor (backend/app/core/runtime_config.py).
const GROUPS: GroupSpec[] = [
  {
    name: 'llm',
    testable: true,
    fields: [
      { name: 'provider', kind: 'provider' },
      { name: 'base_url', kind: 'url', visible: remoteProvider },
      { name: 'api_key', kind: 'secret', visible: remoteProvider },
      { name: 'model', kind: 'text' },
      { name: 'temperature', kind: 'number', min: 0, max: 2, step: 0.1 },
      { name: 'timeout_seconds', kind: 'number', min: 1, step: 1 },
      { name: 'max_retries', kind: 'number', min: 0, integer: true },
    ],
  },
  {
    name: 'embeddings',
    testable: true,
    fields: [
      { name: 'provider', kind: 'provider' },
      { name: 'base_url', kind: 'url', optional: true, visible: remoteProvider },
      { name: 'api_key', kind: 'secret', visible: remoteProvider },
      { name: 'model', kind: 'text' },
      { name: 'batch_size', kind: 'number', min: 1, integer: true },
    ],
  },
  {
    name: 'indexing',
    fields: [
      { name: 'upload_max_mb', kind: 'number', min: 1, integer: true },
      { name: 'chunk_size_chars', kind: 'number', min: 200, integer: true },
      { name: 'chunk_overlap_chars', kind: 'number', min: 0, integer: true },
      { name: 'strip_repeated_lines', kind: 'boolean' },
      {
        name: 'repeated_line_min_ratio',
        kind: 'number',
        min: 0.05,
        max: 1,
        step: 0.05,
        visible: stripRepeatedLines,
      },
      { name: 'clean_toc_leaders', kind: 'boolean' },
      { name: 'context_prefix', kind: 'boolean' },
    ],
  },
  {
    name: 'rag',
    fields: [
      { name: 'enabled', kind: 'boolean' },
      { name: 'top_k', kind: 'number', min: 1, max: 50, integer: true },
      { name: 'min_score', kind: 'number', min: 0, max: 1, step: 0.01 },
      { name: 'max_context_chars', kind: 'number', min: 500, integer: true },
      { name: 'condense_question', kind: 'boolean' },
      { name: 'condense_history_messages', kind: 'number', min: 1, integer: true },
      { name: 'toc_score_factor', kind: 'number', min: 0, max: 1, step: 0.05 },
      { name: 'scoped_min_score', kind: 'number', min: 0, max: 1, step: 0.01 },
    ],
  },
  {
    name: 'chat',
    fields: [
      { name: 'history_max_messages', kind: 'number', min: 0, integer: true },
      { name: 'max_message_length', kind: 'number', min: 1, integer: true },
      { name: 'welcome_max_length', kind: 'number', min: 1, integer: true },
      { name: 'suggested_question_max_length', kind: 'number', min: 1, integer: true },
      { name: 'system_prompt_max_length', kind: 'number', min: 1, integer: true },
    ],
  },
  {
    name: 'limits',
    fields: [
      { name: 'chat_per_minute', kind: 'number', min: 0, integer: true },
      { name: 'login_per_minute', kind: 'number', min: 0, integer: true },
    ],
  },
  {
    name: 'users',
    testable: true,
    testUser: true,
    fields: [
      { name: 'auth_mode', kind: 'select', options: ['none', 'ldap'] },
      { name: 'ldap_url', kind: 'url', visible: ldapMode },
      { name: 'ldap_start_tls', kind: 'boolean', visible: ldapMode },
      { name: 'ldap_ca_cert', kind: 'textarea', optional: true, visible: ldapMode },
      { name: 'ldap_timeout_seconds', kind: 'number', min: 1, step: 1, visible: ldapMode },
      { name: 'ldap_bind_dn', kind: 'text', optional: true, visible: ldapMode },
      { name: 'ldap_bind_password', kind: 'secret', visible: ldapMode },
      { name: 'ldap_user_base_dn', kind: 'text', visible: ldapMode },
      { name: 'ldap_user_filter', kind: 'text', visible: ldapMode },
      { name: 'ldap_user_id_attribute', kind: 'text', visible: ldapMode },
      { name: 'ldap_display_name_attribute', kind: 'text', visible: ldapMode },
      { name: 'ldap_admin_group_dn', kind: 'text', optional: true, visible: ldapMode },
      { name: 'ldap_auditor_group_dn', kind: 'text', optional: true, visible: ldapMode },
      { name: 'ldap_nested_groups', kind: 'boolean', visible: ldapMode },
    ],
  },
  {
    name: 'audit',
    fields: [
      { name: 'enabled', kind: 'boolean' },
      { name: 'schedule', kind: 'select', options: ['hourly', 'daily'] },
      { name: 'daily_time', kind: 'text', visible: dailyAudit },
      { name: 'max_per_run', kind: 'number', min: 1, max: 10000, integer: true },
      { name: 'use_llm', kind: 'boolean' },
      { name: 'llm_model', kind: 'text', optional: true, visible: auditWithModel },
      { name: 'llm_history_days', kind: 'number', min: 0, integer: true, visible: auditWithModel },
      { name: 'retention_days', kind: 'number', min: 0, integer: true },
      { name: 'notify_enabled', kind: 'boolean' },
      {
        name: 'notify_min_severity',
        kind: 'select',
        options: ['low', 'medium', 'high'],
        visible: auditEmail,
      },
      { name: 'notify_to', kind: 'text', optional: true, visible: auditEmail },
      { name: 'smtp_host', kind: 'text', optional: true, visible: auditEmail },
      { name: 'smtp_port', kind: 'number', min: 1, max: 65535, integer: true, visible: auditEmail },
      {
        name: 'smtp_security',
        kind: 'select',
        options: ['starttls', 'ssl', 'plain'],
        visible: auditEmail,
      },
      { name: 'smtp_username', kind: 'text', optional: true, visible: auditEmail },
      { name: 'smtp_password', kind: 'secret', visible: auditEmail },
      { name: 'smtp_from', kind: 'text', optional: true, visible: auditEmail },
    ],
  },
]

interface FormState {
  /** Texto de los campos (números incluidos) o casillas; los secretos empiezan vacíos. */
  values: Record<string, string | boolean>
  /** Secretos marcados para quitarlos. */
  clear: Record<string, boolean>
}

const isSecret = (value: unknown): value is SecretState =>
  typeof value === 'object' && value !== null && 'configured' in value

function initialState(group: ConfigGroup): FormState {
  const values: Record<string, string | boolean> = {}
  for (const [name, value] of Object.entries(group.values)) {
    if (isSecret(value)) values[name] = ''
    else if (typeof value === 'boolean') values[name] = value
    else values[name] = value === null ? '' : String(value)
  }
  return { values, clear: {} }
}

/** Errores de validación por campo (solo de los visibles). */
function validate(spec: GroupSpec, state: FormState): Record<string, string> {
  const errors: Record<string, string> = {}
  for (const field of spec.fields) {
    if (field.visible && !field.visible(state)) continue
    const raw = state.values[field.name]
    if (typeof raw !== 'string' || field.kind === 'secret') continue
    const text = raw.trim()
    if (!text) {
      if (!field.optional) errors[field.name] = t.config.required
      continue
    }
    if (field.kind !== 'number') continue
    const number = Number(text)
    if (Number.isNaN(number)) errors[field.name] = t.config.required
    else if (field.integer && !Number.isInteger(number)) errors[field.name] = t.config.integer
    else if (
      (field.min !== undefined && number < field.min) ||
      (field.max !== undefined && number > field.max)
    ) {
      errors[field.name] = t.config.range(field.min, field.max)
    }
  }
  return errors
}

/** Cambios para la API: los campos visibles y los secretos escritos o quitados. */
function toChanges(spec: GroupSpec, state: FormState): ConfigChanges {
  const changes: ConfigChanges = {}
  const clear: string[] = []
  for (const field of spec.fields) {
    if (field.visible && !field.visible(state)) continue
    const raw = state.values[field.name]
    if (field.kind === 'secret') {
      if (state.clear[field.name]) clear.push(field.name)
      else if (typeof raw === 'string' && raw) changes[field.name] = raw
    } else if (typeof raw === 'boolean') {
      changes[field.name] = raw
    } else if (field.kind === 'number') {
      changes[field.name] = Number(raw.trim())
    } else {
      changes[field.name] = raw.trim() || (field.optional ? null : '')
    }
  }
  if (clear.length) changes.clear_secrets = clear
  return changes
}

function isDirty(spec: GroupSpec, group: ConfigGroup, state: FormState): boolean {
  const changes = toChanges(spec, state)
  return Object.entries(changes).some(([name, value]) => {
    if (name === 'clear_secrets') return true
    const current = group.values[name]
    if (isSecret(current)) return true // se ha escrito un secreto nuevo
    return value !== current
  })
}

const errorText = (error: unknown, fallback: string) =>
  error instanceof ApiError && error.detail ? error.detail : fallback

/** Configuración de la aplicación: un formulario por grupo (#42). */
export function ConfigPage() {
  usePageTitle(t.config.title)
  const queryClient = useQueryClient()
  const config = useQuery({ queryKey: APP_CONFIG_KEY, queryFn: getAppConfig })

  // Si la sesión ha caducado, se vuelve a comprobar y el guard redirige al login.
  const unauthorized = config.error instanceof ApiError && config.error.status === 401
  useEffect(() => {
    if (unauthorized) void queryClient.invalidateQueries({ queryKey: ADMIN_SESSION_KEY })
  }, [unauthorized, queryClient])

  function onSaved(result: ConfigSaveResult) {
    queryClient.setQueryData<AppConfig>(APP_CONFIG_KEY, (old) =>
      old ? { ...old, groups: { ...old.groups, [result.group]: result.config } } : old,
    )
    // Documentos desactualizados respecto al nuevo modelo o troceado.
    void queryClient.invalidateQueries({ queryKey: ['admin', 'documents-stats'] })
    if (result.group === 'users') {
      // Cambia cómo se accede al chat y qué ajustes de acceso se ofrecen.
      void queryClient.invalidateQueries({ queryKey: AUTH_SESSION_KEY })
      void queryClient.invalidateQueries({ queryKey: ['admin', 'access'] })
    }
  }

  return (
    <div className="mx-auto flex max-w-4xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold">{t.config.title}</h1>
        <p className="text-sm text-texto-secundario">{t.config.description}</p>
      </div>
      {config.isPending ? (
        <p className="text-texto-secundario">{t.common.loading}</p>
      ) : config.isError ? (
        <p role="alert" className="text-error">
          {t.config.loadError}
        </p>
      ) : (
        GROUPS.map((spec) => (
          <GroupForm
            key={spec.name}
            spec={spec}
            group={config.data.groups[spec.name]}
            providers={
              spec.name === 'llm' || spec.name === 'embeddings'
                ? config.data.providers[spec.name]
                : []
            }
            onSaved={onSaved}
          />
        ))
      )}
    </div>
  )
}

function GroupForm(props: {
  spec: GroupSpec
  group: ConfigGroup
  providers: string[]
  onSaved: (result: ConfigSaveResult) => void
}) {
  const { spec, group } = props
  const texts = t.config.groups[spec.name]
  const headingId = useId()
  const [state, setState] = useState(() => initialState(group))
  const [showErrors, setShowErrors] = useState(false)
  const [result, setResult] = useState<ConfigSaveResult | null>(null)
  const [testUser, setTestUser] = useState({ username: '', password: '' })
  const queryClient = useQueryClient()

  const save = useMutation({
    mutationFn: (changes: ConfigChanges) => saveConfigGroup(spec.name, changes),
    onSuccess: (data) => {
      // El formulario vuelve a partir de lo guardado (los secretos, vacíos).
      setState(initialState(data.config))
      setShowErrors(false)
      setResult(data)
      props.onSaved(data)
    },
  })
  const test = useMutation({
    mutationFn: (changes: ConfigChanges) =>
      checkConnection(spec.name as 'llm' | 'embeddings' | 'users', changes),
  })
  const reindex = useMutation({
    mutationFn: reindexAll,
    onSettled: () => queryClient.invalidateQueries({ queryKey: ['admin'] }),
  })

  const errors = validate(spec, state)
  const valid = Object.keys(errors).length === 0
  const dirty = isDirty(spec, group, state)

  function edit(change: (state: FormState) => FormState) {
    setState(change)
    setResult(null)
    save.reset()
    test.reset()
  }

  function submitIfValid(action: (changes: ConfigChanges) => void) {
    setShowErrors(true)
    if (valid) action(toChanges(spec, state))
  }

  return (
    <section
      aria-labelledby={headingId}
      className="flex flex-col gap-4 rounded-lg border border-borde bg-fondo p-4 md:p-6"
    >
      <div>
        <h2 id={headingId} className="text-lg font-bold">
          {texts.title}
        </h2>
        <p className="text-sm text-texto-secundario">{texts.description}</p>
        <p className="text-xs text-texto-secundario">
          {group.updated_by === 'env' || !group.updated_at
            ? t.config.fromEnv
            : t.config.updated(group.updated_by, group.updated_at)}
        </p>
      </div>
      <form
        noValidate
        aria-labelledby={headingId}
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault()
          if (!save.isPending && dirty) submitIfValid((changes) => save.mutate(changes))
        }}
      >
        <div className="grid gap-4 md:grid-cols-2">
          {spec.fields
            .filter((field) => !field.visible || field.visible(state))
            .map((field) => (
              <Field
                key={field.name}
                group={spec.name}
                field={field}
                state={state}
                secret={group.values[field.name]}
                providers={props.providers}
                reindex={group.reindex_fields.includes(field.name)}
                error={showErrors ? errors[field.name] : undefined}
                onChange={edit}
              />
            ))}
        </div>

        {spec.testUser && state.values.auth_mode === 'ldap' && (
          <fieldset className="flex flex-col gap-2 rounded border border-borde p-3">
            <legend className="px-1 text-sm font-medium">{t.config.testUser.legend}</legend>
            <p className="text-xs text-texto-secundario">{t.config.testUser.hint}</p>
            <div className="grid gap-3 md:grid-cols-2">
              <label className="flex flex-col gap-1 text-sm">
                {t.config.testUser.username}
                <input
                  type="text"
                  autoComplete="off"
                  value={testUser.username}
                  onChange={(e) => setTestUser((u) => ({ ...u, username: e.target.value }))}
                  className="campo text-sm"
                />
              </label>
              <label className="flex flex-col gap-1 text-sm">
                {t.config.testUser.password}
                <input
                  type="password"
                  autoComplete="off"
                  value={testUser.password}
                  onChange={(e) => setTestUser((u) => ({ ...u, password: e.target.value }))}
                  className="campo text-sm"
                />
              </label>
            </div>
          </fieldset>
        )}

        {showErrors && !valid && (
          <p role="alert" className="text-sm text-error">
            {t.config.invalid}
          </p>
        )}
        {save.isError && (
          <p role="alert" className="text-sm text-error">
            {errorText(save.error, t.config.saveError)}
          </p>
        )}
        {result && (
          <div role="status" className="flex flex-col gap-2 text-sm">
            <p className="text-exito">{t.config.saved}</p>
            {result.warnings.map((warning) => (
              <p key={warning} className="text-aviso">
                {warning}
              </p>
            ))}
          </div>
        )}
        {result?.requires_reindex && result.warnings.length > 0 && (
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              className="btn btn-secundario"
              disabled={reindex.isPending || reindex.isSuccess}
              onClick={() => reindex.mutate()}
            >
              {reindex.isPending ? t.config.reindexing : t.config.reindexNow}
            </button>
            {reindex.isSuccess && (
              <p role="status" className="text-sm text-exito">
                {t.config.reindexStarted(reindex.data.scheduled)}
              </p>
            )}
            {reindex.isError && (
              <p role="alert" className="text-sm text-error">
                {t.config.reindexError}
              </p>
            )}
          </div>
        )}
        {test.isSuccess && (
          <p role="status" className={`text-sm ${test.data.ok ? 'text-exito' : 'text-error'}`}>
            {test.data.detail}
          </p>
        )}
        {test.isError && (
          <p role="alert" className="text-sm text-error">
            {errorText(test.error, t.config.testError)}
          </p>
        )}

        <div className="flex flex-wrap items-center justify-end gap-3">
          {spec.testable && (
            <button
              type="button"
              className="btn btn-secundario"
              disabled={test.isPending}
              onClick={() =>
                submitIfValid((changes) =>
                  test.mutate(
                    spec.testUser && testUser.username.trim()
                      ? {
                          ...changes,
                          test_username: testUser.username.trim(),
                          test_password: testUser.password,
                        }
                      : changes,
                  ),
                )
              }
            >
              {test.isPending ? t.config.testing : t.config.test}
            </button>
          )}
          <button type="submit" className="btn btn-primario" disabled={save.isPending || !dirty}>
            {save.isPending ? t.config.saving : t.config.save}
          </button>
        </div>
      </form>
    </section>
  )
}

function Field(props: {
  group: ConfigGroupName
  field: FieldSpec
  state: FormState
  secret: unknown
  providers: string[]
  reindex: boolean
  error?: string
  onChange: (change: (state: FormState) => FormState) => void
}) {
  const { field, state, error } = props
  const texts = t.config.groups[props.group]
  const label = (texts.fields as Record<string, string>)[field.name] ?? field.name
  const hint = (texts.hints as Record<string, string | undefined>)[field.name]
  const id = `config-${props.group}-${field.name}`
  const hintId = `${id}-ayuda`
  const errorId = `${id}-error`
  const value = state.values[field.name]
  const setValue = (next: string | boolean) =>
    props.onChange((s) => ({ ...s, values: { ...s.values, [field.name]: next } }))

  const notes: ReactNode[] = []
  if (hint) notes.push(hint)
  if (props.reindex) notes.push(t.config.reindexNote)
  const describedBy = [notes.length ? hintId : '', error ? errorId : ''].filter(Boolean).join(' ')
  const description = (
    <>
      {notes.length > 0 && (
        <span id={hintId} className="text-xs text-texto-secundario">
          {notes.map((note, i) => (
            <span key={i}>
              {i > 0 ? ' ' : ''}
              {note}
            </span>
          ))}
        </span>
      )}
      {error && (
        <span id={errorId} className="text-xs text-error">
          {error}
        </span>
      )}
    </>
  )

  if (field.kind === 'boolean') {
    return (
      <div className="flex flex-col gap-1 md:col-span-2">
        <label className="flex items-center gap-2 text-sm font-medium">
          <input
            id={id}
            type="checkbox"
            checked={value === true}
            aria-describedby={describedBy || undefined}
            onChange={(e) => setValue(e.target.checked)}
            className="h-5 w-5 accent-marca"
          />
          {label}
        </label>
        {description}
      </div>
    )
  }

  if (field.kind === 'select') {
    const current = typeof value === 'string' ? value : ''
    return (
      <div className="flex flex-col gap-1 md:col-span-2">
        <label htmlFor={id} className="text-sm font-medium">
          {label}
        </label>
        <select
          id={id}
          value={current}
          aria-describedby={describedBy || undefined}
          onChange={(e) => setValue(e.target.value)}
          className="campo text-sm"
        >
          {(field.options ?? []).map((option) => (
            <option key={option} value={option}>
              {t.config.options[option] ?? option}
            </option>
          ))}
        </select>
        {description}
      </div>
    )
  }

  if (field.kind === 'textarea') {
    return (
      <div className="flex flex-col gap-1 md:col-span-2">
        <label htmlFor={id} className="text-sm font-medium">
          {label}
        </label>
        <textarea
          id={id}
          rows={4}
          spellCheck={false}
          value={typeof value === 'string' ? value : ''}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy || undefined}
          onChange={(e) => setValue(e.target.value)}
          className="campo font-mono text-xs"
        />
        {description}
      </div>
    )
  }

  if (field.kind === 'provider') {
    const current = typeof value === 'string' ? value : ''
    // El valor guardado se conserva aunque ya no se ofrezca (p. ej. el simulado en producción).
    const options = props.providers.includes(current)
      ? props.providers
      : [current, ...props.providers]
    return (
      <div className="flex flex-col gap-1 md:col-span-2">
        <label htmlFor={id} className="text-sm font-medium">
          {label}
        </label>
        <select
          id={id}
          value={current}
          aria-describedby={describedBy || undefined}
          onChange={(e) => setValue(e.target.value)}
          className="campo text-sm"
        >
          {options.map((option) => (
            <option key={option} value={option}>
              {t.config.providers[option] ?? option}
            </option>
          ))}
        </select>
        {description}
      </div>
    )
  }

  if (field.kind === 'secret') {
    const secret = isSecret(props.secret) ? props.secret : { configured: false, unreadable: false }
    const status = secret.unreadable
      ? t.config.secret.unreadable
      : secret.configured
        ? t.config.secret.configured
        : t.config.secret.empty
    const statusId = `${id}-estado`
    const cleared = state.clear[field.name] === true
    return (
      <div className="flex flex-col gap-1 md:col-span-2">
        <label htmlFor={id} className="text-sm font-medium">
          {label}
        </label>
        <input
          id={id}
          type="password"
          autoComplete="off"
          value={typeof value === 'string' ? value : ''}
          disabled={cleared}
          aria-describedby={[statusId, describedBy].filter(Boolean).join(' ')}
          onChange={(e) => setValue(e.target.value)}
          className="campo text-sm"
        />
        <span
          id={statusId}
          className={`text-xs ${secret.unreadable ? 'text-aviso' : 'text-texto-secundario'}`}
        >
          {status}
        </span>
        {description}
        {secret.configured && (
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={cleared}
              onChange={(e) => {
                const checked = e.target.checked
                props.onChange((s) => ({
                  values: { ...s.values, [field.name]: '' },
                  clear: { ...s.clear, [field.name]: checked },
                }))
              }}
              className="h-5 w-5 accent-marca"
            />
            {t.config.secret.clear}
          </label>
        )}
      </div>
    )
  }

  const wide = field.kind === 'url' || field.kind === 'text'
  return (
    <div className={`flex flex-col gap-1 ${wide ? 'md:col-span-2' : ''}`}>
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      <input
        id={id}
        type={field.kind === 'number' ? 'number' : field.kind === 'url' ? 'url' : 'text'}
        inputMode={field.kind === 'number' ? (field.integer ? 'numeric' : 'decimal') : undefined}
        min={field.min}
        max={field.max}
        step={field.integer ? 1 : (field.step ?? 'any')}
        value={typeof value === 'string' ? value : ''}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy || undefined}
        onChange={(e) => setValue(e.target.value)}
        className="campo text-sm"
      />
      {description}
    </div>
  )
}
