import { useQuery } from '@tanstack/react-query'
import { Files, X } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import { listIndexedDocuments, MAX_SCOPE_DOCUMENTS } from '../api/client'
import { t } from '../i18n/es'

interface Props {
  /** Ids de los documentos elegidos (vacío: se busca en todos). */
  selected: string[]
  onChange: (documentIds: string[]) => void
  disabled?: boolean
  /** Error al guardar la selección. */
  error?: string | null
}

const collator = new Intl.Collator('es', { sensitivity: 'base' })

/** Elige en qué documentos buscar: todos (por defecto) o hasta `MAX_SCOPE_DOCUMENTS`. */
export function ScopePicker({ selected, onChange, disabled = false, error }: Props) {
  const [open, setOpen] = useState(false)
  const [filter, setFilter] = useState('')
  const rootRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const panelId = useId()

  // La lista solo hace falta al elegir o para mostrar los nombres de los elegidos.
  const documents = useQuery({
    queryKey: ['indexed-documents'],
    queryFn: listIndexedDocuments,
    enabled: open || selected.length > 0,
    staleTime: 60_000,
  })
  const names = new Map((documents.data ?? []).map((d) => [d.id, d.filename]))

  function close(focusButton = true) {
    setOpen(false)
    setFilter('')
    if (focusButton) buttonRef.current?.focus()
  }

  // Se cierra al pulsar fuera del panel.
  useEffect(() => {
    if (!open) return
    function onPointerDown(event: PointerEvent) {
      if (!rootRef.current?.contains(event.target as Node)) close(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  function toggle(id: string) {
    onChange(selected.includes(id) ? selected.filter((d) => d !== id) : [...selected, id])
  }

  const needle = filter.trim().toLocaleLowerCase('es')
  const visible = (documents.data ?? [])
    .filter((d) => !needle || d.filename.toLocaleLowerCase('es').includes(needle))
    .sort((a, b) => collator.compare(a.filename, b.filename))
  const full = selected.length >= MAX_SCOPE_DOCUMENTS

  return (
    <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
      <div ref={rootRef} className="relative">
        <button
          ref={buttonRef}
          type="button"
          disabled={disabled}
          aria-expanded={open}
          aria-controls={panelId}
          aria-haspopup="dialog"
          onClick={() => (open ? close() : setOpen(true))}
          className="btn btn-secundario font-normal"
        >
          <Files aria-hidden size={16} />
          {t.chat.scope.label}:{' '}
          {selected.length ? t.chat.scope.some(selected.length) : t.chat.scope.all}
        </button>
        {open && (
          <div
            id={panelId}
            role="dialog"
            aria-label={t.chat.scope.choose}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                event.stopPropagation()
                close()
              }
            }}
            className="absolute bottom-full left-0 z-20 mb-2 flex w-96 max-w-[calc(100vw-2rem)] flex-col gap-2 rounded-lg border border-borde bg-fondo p-3 shadow-lg"
          >
            <input
              type="search"
              autoFocus
              aria-label={t.chat.scope.filter}
              placeholder={t.chat.scope.filter}
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              className="campo"
            />
            <p className="text-xs text-texto-secundario">{t.chat.scope.max(MAX_SCOPE_DOCUMENTS)}</p>
            {documents.isLoading ? (
              <p role="status">{t.chat.scope.loading}</p>
            ) : documents.isError ? (
              <p role="alert" className="text-error">
                {t.chat.scope.loadError}
              </p>
            ) : visible.length === 0 ? (
              <p>{documents.data?.length ? t.chat.scope.empty : t.chat.scope.none}</p>
            ) : (
              <fieldset className="max-h-60 overflow-y-auto">
                <legend className="sr-only">{t.chat.scope.choose}</legend>
                {visible.map((document) => {
                  const checked = selected.includes(document.id)
                  return (
                    <label
                      key={document.id}
                      className="flex cursor-pointer items-start gap-2 rounded px-1 py-1 break-all hover:bg-fondo-alt"
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={!checked && full}
                        onChange={() => toggle(document.id)}
                        className="mt-1"
                      />
                      {document.filename}
                    </label>
                  )
                })}
              </fieldset>
            )}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                disabled={selected.length === 0}
                onClick={() => onChange([])}
                className="btn btn-secundario"
              >
                {t.chat.scope.clear}
              </button>
              <button type="button" onClick={() => close()} className="btn btn-primario">
                {t.chat.scope.close}
              </button>
            </div>
          </div>
        )}
      </div>
      {selected.length > 0 && (
        <ul aria-label={t.chat.scope.selected} className="flex flex-wrap gap-2">
          {selected.map((id) => {
            const name = names.get(id) ?? '…'
            return (
              <li
                key={id}
                className="flex max-w-full items-center gap-1 rounded border border-borde-control bg-marca-suave px-2 py-0.5 text-xs break-all"
              >
                {name}
                <button
                  type="button"
                  disabled={disabled}
                  aria-label={t.chat.scope.remove(name)}
                  onClick={() => onChange(selected.filter((d) => d !== id))}
                  className="rounded p-0.5 hover:bg-fondo-alt"
                >
                  <X aria-hidden size={12} />
                </button>
              </li>
            )
          })}
        </ul>
      )}
      {error && (
        <p role="alert" className="w-full text-xs text-error">
          {error}
        </p>
      )}
    </div>
  )
}
