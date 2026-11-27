import { useQuery } from '@tanstack/react-query'
import { Files, X } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'
import {
  ALL_DOCUMENTS,
  isLimited,
  listIndexedDocuments,
  MAX_SCOPE_DOCUMENTS,
  type SearchScope,
} from '../api/client'
import { t } from '../i18n/es'

interface Props {
  /** Documentos y categorías elegidos (ambos vacíos: se busca en todos). */
  selected: SearchScope
  onChange: (scope: SearchScope) => void
  disabled?: boolean
  /** Error al guardar la selección. */
  error?: string | null
}

const collator = new Intl.Collator('es', { sensitivity: 'base' })

interface CategoryOption {
  id: number
  name: string
  documents: number
}

/**
 * Elige en qué buscar: en todos los documentos (por defecto), en hasta
 * `MAX_SCOPE_DOCUMENTS` documentos sueltos y en categorías enteras (sin límite: se busca en
 * sus documentos de cada momento).
 */
export function ScopePicker({ selected, onChange, disabled = false, error }: Props) {
  const [open, setOpen] = useState(false)
  const [filter, setFilter] = useState('')
  const rootRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const panelId = useId()
  const { documentIds, categoryIds } = selected

  // La lista solo hace falta al elegir o para mostrar los nombres de los elegidos.
  const documents = useQuery({
    queryKey: ['indexed-documents'],
    queryFn: listIndexedDocuments,
    enabled: open || isLimited(selected),
    staleTime: 60_000,
  })
  const names = new Map((documents.data ?? []).map((d) => [d.id, d.title]))
  // Categorías con documentos indexados (las vacías no aportan nada a la búsqueda).
  const categories = new Map<number, CategoryOption>()
  for (const document of documents.data ?? []) {
    if (document.category_id == null || !document.category) continue
    const option = categories.get(document.category_id)
    if (option) option.documents += 1
    else
      categories.set(document.category_id, {
        id: document.category_id,
        name: document.category,
        documents: 1,
      })
  }
  const categoryName = (id: number) => categories.get(id)?.name ?? '…'

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

  const toggleDocument = (id: string) =>
    onChange({
      ...selected,
      documentIds: documentIds.includes(id)
        ? documentIds.filter((d) => d !== id)
        : [...documentIds, id],
    })
  const toggleCategory = (id: number) =>
    onChange({
      ...selected,
      categoryIds: categoryIds.includes(id)
        ? categoryIds.filter((c) => c !== id)
        : [...categoryIds, id],
    })

  // Se busca en el título, el nombre del fichero y la categoría.
  const needle = filter.trim().toLocaleLowerCase('es')
  const matches = (text: string) => text.toLocaleLowerCase('es').includes(needle)
  const visible = (documents.data ?? [])
    .filter((d) => !needle || [d.title, d.filename, d.category ?? ''].some(matches))
    .sort((a, b) => collator.compare(a.title, b.title))
  const visibleCategories = [...categories.values()]
    .filter((c) => !needle || matches(c.name))
    .sort((a, b) => collator.compare(a.name, b.name))
  const full = documentIds.length >= MAX_SCOPE_DOCUMENTS
  const empty = visible.length === 0 && visibleCategories.length === 0

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
          {isLimited(selected)
            ? t.chat.scope.some(categoryIds.length, documentIds.length)
            : t.chat.scope.all}
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
            <p className="text-xs text-texto-secundario">
              {t.chat.scope.max(MAX_SCOPE_DOCUMENTS)}
              {categories.size > 0 && ` ${t.chat.scope.categoriesHint}`}
            </p>
            {documents.isLoading ? (
              <p role="status">{t.chat.scope.loading}</p>
            ) : documents.isError ? (
              <p role="alert" className="text-error">
                {t.chat.scope.loadError}
              </p>
            ) : empty ? (
              <p>{documents.data?.length ? t.chat.scope.empty : t.chat.scope.none}</p>
            ) : (
              <div className="flex max-h-72 flex-col gap-2 overflow-y-auto">
                {visibleCategories.length > 0 && (
                  <fieldset>
                    <legend className="text-xs font-semibold text-texto-secundario">
                      {t.chat.scope.categories}
                    </legend>
                    {visibleCategories.map((category) => (
                      <label
                        key={category.id}
                        className="flex cursor-pointer items-start gap-2 rounded px-1 py-1 break-all hover:bg-fondo-alt"
                      >
                        <input
                          type="checkbox"
                          checked={categoryIds.includes(category.id)}
                          onChange={() => toggleCategory(category.id)}
                          className="mt-1"
                        />
                        <span className="flex flex-col">
                          {category.name}
                          <span className="text-xs text-texto-secundario">
                            {t.chat.scope.documentCount(category.documents)}
                          </span>
                        </span>
                      </label>
                    ))}
                  </fieldset>
                )}
                {visible.length > 0 && (
                  <fieldset>
                    <legend
                      className={
                        categories.size > 0
                          ? 'text-xs font-semibold text-texto-secundario'
                          : 'sr-only'
                      }
                    >
                      {categories.size > 0 ? t.chat.scope.documents : t.chat.scope.choose}
                    </legend>
                    {visible.map((document) => {
                      // Incluido por su categoría: se busca en él sin elegirlo suelto.
                      const viaCategory =
                        document.category_id != null && categoryIds.includes(document.category_id)
                      const checked = viaCategory || documentIds.includes(document.id)
                      return (
                        <label
                          key={document.id}
                          className="flex cursor-pointer items-start gap-2 rounded px-1 py-1 break-all hover:bg-fondo-alt"
                        >
                          <input
                            type="checkbox"
                            checked={checked}
                            disabled={viaCategory || (!checked && full)}
                            onChange={() => toggleDocument(document.id)}
                            className="mt-1"
                          />
                          <span className="flex flex-col">
                            {document.title}
                            {(document.category || document.title !== document.filename) && (
                              <span className="text-xs text-texto-secundario">
                                {[document.category, document.filename]
                                  .filter((part) => part && part !== document.title)
                                  .join(' · ')}
                              </span>
                            )}
                          </span>
                        </label>
                      )
                    })}
                  </fieldset>
                )}
              </div>
            )}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                disabled={!isLimited(selected)}
                onClick={() => onChange(ALL_DOCUMENTS)}
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
      {isLimited(selected) && (
        <ul aria-label={t.chat.scope.selected} className="flex flex-wrap gap-2">
          {categoryIds.map((id) => (
            <Chip
              key={`c${id}`}
              name={categoryName(id)}
              disabled={disabled}
              onRemove={() =>
                onChange({ ...selected, categoryIds: categoryIds.filter((c) => c !== id) })
              }
            />
          ))}
          {documentIds.map((id) => (
            <Chip
              key={id}
              name={names.get(id) ?? '…'}
              disabled={disabled}
              onRemove={() =>
                onChange({ ...selected, documentIds: documentIds.filter((d) => d !== id) })
              }
            />
          ))}
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

function Chip({
  name,
  disabled,
  onRemove,
}: {
  name: string
  disabled: boolean
  onRemove: () => void
}) {
  return (
    <li className="flex max-w-full items-center gap-1 rounded border border-borde-control bg-marca-suave px-2 py-0.5 text-xs break-all">
      {name}
      <button
        type="button"
        disabled={disabled}
        aria-label={t.chat.scope.remove(name)}
        onClick={onRemove}
        className="rounded p-0.5 hover:bg-fondo-alt"
      >
        <X aria-hidden size={12} />
      </button>
    </li>
  )
}
