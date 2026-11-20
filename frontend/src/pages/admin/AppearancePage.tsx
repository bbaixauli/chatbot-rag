import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState, type CSSProperties, type ReactNode } from 'react'
import {
  deleteLogo,
  getAdminAppearance,
  reloadStylesheet,
  resetAppearance,
  saveAppearance,
  uploadLogo,
  type AdminAppearance,
  type AppearanceInput,
} from '../../api/appearance'
import { ApiError } from '../../api/client'
import { APPEARANCE_KEY } from '../../hooks/useAppearance'
import { usePageTitle } from '../../hooks/usePageTitle'
import { t } from '../../i18n/es'
import { contrastWarnings, isHexColor } from '../../utils/contrast'
import { ADMIN_SESSION_KEY } from './session'

const ADMIN_APPEARANCE_KEY = ['admin', 'appearance'] as const

// Grupos de colores en el orden del manual de estilos (3.1).
const COLOR_GROUPS: { title: string; names: string[] }[] = [
  {
    title: t.appearance.groups.brand,
    names: ['marca', 'marca-oscuro', 'sobre-marca', 'marca-suave', 'enlace'],
  },
  {
    title: t.appearance.groups.base,
    names: ['texto', 'texto-secundario', 'fondo', 'fondo-alt', 'borde-control', 'borde', 'foco'],
  },
  {
    title: t.appearance.groups.states,
    names: [
      'error',
      'error-suave',
      'exito',
      'exito-suave',
      'aviso',
      'aviso-suave',
      'info',
      'info-suave',
    ],
  },
]

const colorLabel = (name: string) => t.appearance.colors[name] ?? name

/** Administración de la apariencia: identidad, colores, tipos de letra y textos. */
export function AppearancePage() {
  usePageTitle(t.appearance.title)
  const queryClient = useQueryClient()
  const appearance = useQuery({ queryKey: ADMIN_APPEARANCE_KEY, queryFn: getAdminAppearance })
  // Cambia al restablecer o al cambiar el logo: el formulario vuelve a partir de los datos.
  const [formVersion, setFormVersion] = useState(0)

  const unauthorized = appearance.error instanceof ApiError && appearance.error.status === 401
  useEffect(() => {
    if (unauthorized) void queryClient.invalidateQueries({ queryKey: ADMIN_SESSION_KEY })
  }, [unauthorized, queryClient])

  function applied(data: AdminAppearance) {
    queryClient.setQueryData(ADMIN_APPEARANCE_KEY, data)
    void queryClient.invalidateQueries({ queryKey: APPEARANCE_KEY })
    reloadStylesheet()
  }

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold">{t.appearance.title}</h1>
        <p className="text-sm text-texto-secundario">{t.appearance.description}</p>
      </div>
      {appearance.isPending ? (
        <p className="text-texto-secundario">{t.common.loading}</p>
      ) : appearance.isError ? (
        <p role="alert" className="text-error">
          {t.appearance.loadError}
        </p>
      ) : (
        <>
          <LogoSection
            current={appearance.data}
            onChanged={(data) => {
              applied(data)
              setFormVersion((v) => v + 1)
            }}
          />
          <AppearanceForm
            key={formVersion}
            initial={appearance.data}
            onSaved={applied}
            onReset={(data) => {
              applied(data)
              setFormVersion((v) => v + 1)
            }}
          />
        </>
      )}
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3 rounded-lg border border-borde bg-fondo p-4 md:p-6">
      <h2 className="text-lg font-bold">{title}</h2>
      {children}
    </section>
  )
}

function LogoSection(props: { current: AdminAppearance; onChanged: (d: AdminAppearance) => void }) {
  const upload = useMutation({ mutationFn: uploadLogo, onSuccess: props.onChanged })
  const remove = useMutation({ mutationFn: deleteLogo, onSuccess: props.onChanged })
  const error = upload.error instanceof ApiError ? upload.error.message : null
  return (
    <Section title={t.appearance.logo}>
      <p className="text-sm text-texto-secundario">{t.appearance.logoHint}</p>
      {props.current.logo_url ? (
        <div className="flex flex-wrap items-center gap-4">
          <img
            src={props.current.logo_url}
            alt={t.appearance.currentLogo}
            className="h-16 w-auto rounded border border-borde bg-fondo-alt p-2"
          />
          <button
            type="button"
            className="btn btn-secundario"
            disabled={remove.isPending}
            onClick={() => window.confirm(t.appearance.confirmDeleteLogo) && remove.mutate()}
          >
            {t.appearance.deleteLogo}
          </button>
        </div>
      ) : (
        <p className="text-sm text-texto-secundario">{t.appearance.noLogo}</p>
      )}
      <label className="flex flex-col gap-1 text-sm font-medium">
        {t.appearance.uploadLogo}
        <input
          type="file"
          accept=".svg,.png,.webp,image/svg+xml,image/png,image/webp"
          disabled={upload.isPending}
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) upload.mutate(file)
            e.target.value = ''
          }}
          className="campo text-sm"
        />
      </label>
      {upload.isError && (
        <p role="alert" className="text-sm text-error">
          {error && upload.error instanceof ApiError && upload.error.status === 422
            ? error
            : t.appearance.logoError}
        </p>
      )}
      {upload.isSuccess && (
        <p role="status" className="text-sm text-exito">
          {t.appearance.logoSaved}
        </p>
      )}
    </Section>
  )
}

function toInput(data: AdminAppearance): AppearanceInput {
  const { app_name, organization, header_style, colors, font_sans, font_mono, logo_alt } = data
  const { footer, login_notice, ai_notice } = data
  return {
    app_name,
    organization,
    header_style,
    colors: { ...colors },
    font_sans,
    font_mono,
    logo_alt,
    footer: { ...footer },
    login_notice,
    ai_notice,
  }
}

function AppearanceForm(props: {
  initial: AdminAppearance
  onSaved: (data: AdminAppearance) => void
  onReset: (data: AdminAppearance) => void
}) {
  const [form, setForm] = useState<AppearanceInput>(() => toInput(props.initial))
  const [invalid, setInvalid] = useState(false)
  const save = useMutation({ mutationFn: saveAppearance, onSuccess: props.onSaved })
  const reset = useMutation({ mutationFn: resetAppearance, onSuccess: props.onReset })

  const badColors = Object.entries(form.colors).filter(([, value]) => !isHexColor(value))
  const warnings = contrastWarnings(form.colors, form.header_style)
  const update = (change: Partial<AppearanceInput>) => {
    setForm((current) => ({ ...current, ...change }))
    setInvalid(false)
    save.reset()
  }
  const setColor = (name: string, value: string) =>
    update({ colors: { ...form.colors, [name]: value } })
  const setFooter = (change: Partial<AppearanceInput['footer']>) =>
    update({ footer: { ...form.footer, ...change } })

  return (
    <form
      className="flex flex-col gap-6"
      onSubmit={(e) => {
        e.preventDefault()
        if (badColors.length > 0) return setInvalid(true)
        save.mutate(form)
      }}
    >
      <Section title={t.appearance.identity}>
        <TextField
          id="app-name"
          label={t.appearance.appName}
          value={form.app_name}
          maxLength={80}
          onChange={(app_name) => update({ app_name })}
        />
        <TextField
          id="organization"
          label={t.appearance.organization}
          hint={t.appearance.organizationHint}
          value={form.organization}
          maxLength={120}
          onChange={(organization) => update({ organization })}
        />
        <TextField
          id="logo-alt"
          label={t.appearance.logoAlt}
          hint={t.appearance.logoAltHint}
          value={form.logo_alt}
          maxLength={200}
          onChange={(logo_alt) => update({ logo_alt })}
        />
        <fieldset className="flex flex-col gap-2">
          <legend className="text-sm font-medium">{t.appearance.headerStyle}</legend>
          {(['clara', 'marca'] as const).map((style) => (
            <label key={style} className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="header-style"
                checked={form.header_style === style}
                onChange={() => update({ header_style: style })}
                className="h-5 w-5 accent-marca"
              />
              {t.appearance.headerStyles[style]}
            </label>
          ))}
        </fieldset>
      </Section>

      <Section title={t.appearance.colorsTitle}>
        <p className="text-sm text-texto-secundario">{t.appearance.colorsHint}</p>
        {COLOR_GROUPS.map((group) => (
          <fieldset key={group.title} className="flex flex-col gap-3">
            <legend className="mb-2 font-semibold">{group.title}</legend>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {group.names.map((name) => (
                <ColorField
                  key={name}
                  name={name}
                  value={form.colors[name] ?? ''}
                  onChange={(value) => setColor(name, value)}
                />
              ))}
            </div>
          </fieldset>
        ))}
        <ContrastReport warnings={warnings} />
      </Section>

      <Section title={t.appearance.fonts}>
        <SelectField
          id="font-sans"
          label={t.appearance.fontSans}
          value={form.font_sans}
          options={props.initial.sans_fonts}
          labels={t.appearance.sansFonts}
          onChange={(font_sans) => update({ font_sans })}
        />
        <SelectField
          id="font-mono"
          label={t.appearance.fontMono}
          value={form.font_mono}
          options={props.initial.mono_fonts}
          labels={t.appearance.monoFonts}
          onChange={(font_mono) => update({ font_mono })}
        />
      </Section>

      <Section title={t.appearance.texts}>
        <TextField
          id="footer-unit"
          label={t.appearance.footerUnit}
          value={form.footer.unit}
          maxLength={200}
          onChange={(unit) => setFooter({ unit })}
        />
        <TextField
          id="footer-accessibility"
          label={t.appearance.footerAccessibility}
          hint={t.appearance.urlHint}
          type="url"
          value={form.footer.accessibility_url}
          maxLength={500}
          onChange={(accessibility_url) => setFooter({ accessibility_url })}
        />
        <TextField
          id="footer-support-text"
          label={t.appearance.footerSupportText}
          value={form.footer.support_text}
          maxLength={200}
          onChange={(support_text) => setFooter({ support_text })}
        />
        <TextField
          id="footer-support-url"
          label={t.appearance.footerSupportUrl}
          hint={t.appearance.urlHint}
          type="url"
          value={form.footer.support_url}
          maxLength={500}
          onChange={(support_url) => setFooter({ support_url })}
        />
        <TextField
          id="login-notice"
          label={t.appearance.loginNotice}
          hint={t.appearance.loginNoticeHint}
          value={form.login_notice}
          maxLength={500}
          multiline
          onChange={(login_notice) => update({ login_notice })}
        />
        <TextField
          id="ai-notice"
          label={t.appearance.aiNotice}
          hint={t.appearance.aiNoticeHint}
          value={form.ai_notice}
          placeholder={t.chat.aiNotice}
          maxLength={500}
          multiline
          onChange={(ai_notice) => update({ ai_notice })}
        />
      </Section>

      <Section title={t.appearance.preview}>
        <Preview
          form={form}
          logoUrl={props.initial.logo_url}
          font={props.initial.sans_fonts[form.font_sans]}
        />
      </Section>

      <div className="flex flex-wrap items-center justify-end gap-4">
        {invalid && (
          <p role="alert" className="text-sm text-error">
            {t.appearance.invalidColors(badColors.map(([name]) => colorLabel(name)).join(', '))}
          </p>
        )}
        {save.isError && (
          <p role="alert" className="text-sm text-error">
            {t.appearance.saveError}
          </p>
        )}
        {save.isSuccess && (
          <p role="status" className="text-sm text-exito">
            {save.data.warnings.length > 0
              ? t.appearance.savedWithWarnings(save.data.warnings.length)
              : t.appearance.saved}
          </p>
        )}
        <button
          type="button"
          className="btn btn-secundario"
          disabled={reset.isPending}
          onClick={() => window.confirm(t.appearance.confirmReset) && reset.mutate()}
        >
          {t.appearance.reset}
        </button>
        <button type="submit" className="btn btn-primario" disabled={save.isPending}>
          {save.isPending ? t.appearance.saving : t.appearance.save}
        </button>
      </div>
    </form>
  )
}

function ColorField(props: { name: string; value: string; onChange: (value: string) => void }) {
  const id = `color-${props.name}`
  const valid = isHexColor(props.value)
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium">
        {colorLabel(props.name)}
      </label>
      <div className="flex items-center gap-2">
        <input
          type="color"
          aria-label={t.appearance.colorPicker(colorLabel(props.name))}
          value={valid ? props.value.toLowerCase() : '#000000'}
          onChange={(e) => props.onChange(e.target.value)}
          className="h-10 w-12 shrink-0 cursor-pointer rounded border border-borde-control bg-fondo p-1"
        />
        <input
          id={id}
          value={props.value}
          maxLength={7}
          spellCheck={false}
          aria-invalid={!valid}
          aria-describedby={valid ? undefined : `${id}-error`}
          onChange={(e) => props.onChange(e.target.value.trim())}
          className="campo w-full min-w-0 font-mono text-sm"
        />
      </div>
      {!valid && (
        <p id={`${id}-error`} className="text-xs text-error">
          {t.appearance.colorFormat}
        </p>
      )}
    </div>
  )
}

function ContrastReport({ warnings }: { warnings: ReturnType<typeof contrastWarnings> }) {
  if (warnings.length === 0) {
    return (
      <p
        aria-live="polite"
        className="rounded border border-exito bg-exito-suave p-3 text-sm text-exito"
      >
        {t.appearance.contrastOk}
      </p>
    )
  }
  return (
    <div
      aria-live="polite"
      className="rounded border border-aviso bg-aviso-suave p-3 text-sm text-aviso"
    >
      <p className="font-semibold">{t.appearance.contrastWarningsTitle(warnings.length)}</p>
      <ul className="mt-1 list-disc pl-5">
        {warnings.map((w) => (
          <li key={`${w.foreground}-${w.background}`}>
            {t.appearance.contrastWarning(
              colorLabel(w.foreground),
              colorLabel(w.background),
              w.ratio,
              w.required,
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

function TextField(props: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  hint?: string
  type?: 'text' | 'url'
  placeholder?: string
  maxLength: number
  multiline?: boolean
}) {
  const common = {
    id: props.id,
    value: props.value,
    maxLength: props.maxLength,
    placeholder: props.placeholder,
    'aria-describedby': props.hint ? `${props.id}-hint` : undefined,
    className: 'campo text-sm',
  }
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={props.id} className="text-sm font-medium">
        {props.label}
      </label>
      {props.hint && (
        <p id={`${props.id}-hint`} className="text-xs text-texto-secundario">
          {props.hint}
        </p>
      )}
      {props.multiline ? (
        <textarea {...common} rows={2} onChange={(e) => props.onChange(e.target.value)} />
      ) : (
        <input
          {...common}
          type={props.type ?? 'text'}
          onChange={(e) => props.onChange(e.target.value)}
        />
      )}
    </div>
  )
}

function SelectField(props: {
  id: string
  label: string
  value: string
  options: Record<string, string>
  labels: Record<string, string>
  onChange: (value: string) => void
}) {
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={props.id} className="text-sm font-medium">
        {props.label}
      </label>
      <select
        id={props.id}
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        className="campo text-sm"
      >
        {Object.keys(props.options).map((key) => (
          <option key={key} value={key}>
            {props.labels[key] ?? key}
          </option>
        ))}
      </select>
      <p className="text-sm" style={{ fontFamily: props.options[props.value] }}>
        {t.appearance.fontSample}
      </p>
    </div>
  )
}

/** Vista previa con los valores del formulario (sin guardar). */
const STATE_STYLES = {
  error: 'border-error bg-error-suave text-error',
  exito: 'border-exito bg-exito-suave text-exito',
  aviso: 'border-aviso bg-aviso-suave text-aviso',
  info: 'border-info bg-info-suave text-info',
} as const

function Preview(props: { form: AppearanceInput; logoUrl: string | null; font?: string }) {
  const { form, logoUrl } = props
  const vars: Record<string, string> = {}
  for (const [name, value] of Object.entries(form.colors)) {
    if (isHexColor(value)) vars[`--color-${name}`] = value
  }
  const header = form.header_style === 'marca' ? ['marca', 'sobre-marca'] : ['fondo', 'texto']
  vars['--color-cabecera'] = `var(--color-${header[0]})`
  vars['--color-sobre-cabecera'] = `var(--color-${header[1]})`
  const style = { ...vars, fontFamily: props.font } as CSSProperties
  return (
    // Solo visual: sus botones y enlaces de muestra no se enfocan ni se anuncian.
    <div
      inert
      style={style}
      className="overflow-hidden rounded-lg border border-borde bg-fondo-alt text-texto"
    >
      <div className="cabecera flex min-h-14 items-center gap-3 border-b border-borde bg-cabecera px-4 py-2 text-sobre-cabecera">
        {logoUrl && <img src={logoUrl} alt="" className="h-10 w-auto" />}
        {logoUrl && <span aria-hidden className="h-8 w-px bg-current opacity-60" />}
        <span className="text-lg font-semibold">{form.app_name || t.appName}</span>
      </div>
      <div className="flex flex-col gap-3 p-4">
        <p>
          {t.appearance.previewText}{' '}
          <a href="#vista-previa" onClick={(e) => e.preventDefault()}>
            {t.appearance.previewLink}
          </a>
        </p>
        <p className="text-sm text-texto-secundario">{t.appearance.previewSecondary}</p>
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn btn-primario">
            {t.appearance.previewPrimary}
          </button>
          <button type="button" className="btn btn-secundario">
            {t.appearance.previewSecondaryButton}
          </button>
          <button type="button" className="btn btn-peligro">
            {t.appearance.previewDanger}
          </button>
        </div>
        <span className="nav-item w-fit text-sm" aria-current="page">
          {t.appearance.previewActive}
        </span>
        <div className="grid gap-2 sm:grid-cols-2">
          {(['error', 'exito', 'aviso', 'info'] as const).map((state) => (
            <p key={state} className={`rounded border px-3 py-2 text-sm ${STATE_STYLES[state]}`}>
              {t.appearance.previewStates[state]}
            </p>
          ))}
        </div>
      </div>
    </div>
  )
}
