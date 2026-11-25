import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useId, useRef, useState, type DragEvent, type FormEvent } from 'react'
import { ApiError } from '../../api/client'
import {
  ACCEPTED_EXTENSIONS,
  deleteDocument,
  documentFileUrl,
  getIndexStats,
  listCategories,
  listDocuments,
  reindexAll,
  reindexDocument,
  reindexOutdated,
  updateDocument,
  uploadDocument,
  type Category,
  type DocumentItem,
  type DocumentStatus,
  type UploadResult,
} from '../../api/documents'
import { usePageTitle } from '../../hooks/usePageTitle'
import { t } from '../../i18n/es'
import { formatBytes } from '../../utils/format'
import { CategoryManager } from './CategoryManager'

const DOCUMENTS_KEY = ['admin', 'documents'] as const
const STATS_KEY = ['admin', 'documents-stats'] as const
const CATEGORIES_KEY = ['admin', 'categories'] as const
const POLL_MS = 1500

/** Valor de los selectores de categoría para «sin categoría». */
const NONE = 'none'
/** Valor del filtro para «todas las categorías». */
const ALL = 'all'

interface UploadEntry {
  key: number
  name: string
  /** `archive`: ZIP ya procesado (sus ficheros van en entradas propias). */
  state: 'uploading' | 'uploaded' | 'duplicate' | 'error' | 'archive'
  message?: string
}

const resultState = (result: UploadResult): UploadEntry['state'] =>
  result.error ? 'error' : result.duplicate ? 'duplicate' : 'uploaded'

const statusStyles: Record<DocumentStatus, string> = {
  pending: 'border-borde-control bg-fondo-alt text-texto',
  processing: 'border-info bg-info-suave text-info',
  ready: 'border-exito bg-exito-suave text-exito',
  error: 'border-error bg-error-suave text-error',
}

const isAccepted = (file: File) =>
  ACCEPTED_EXTENSIONS.some((ext) => file.name.toLowerCase().endsWith(ext))

const isBusy = (documents: DocumentItem[] | undefined) =>
  documents?.some((d) => d.status === 'pending' || d.status === 'processing') ?? false

const toCategoryId = (value: string) => (value === NONE ? null : Number(value))

function CategoryOptions({ categories }: { categories: Category[] }) {
  return (
    <>
      <option value={NONE}>{t.documents.noCategory}</option>
      {categories.map((c) => (
        <option key={c.id} value={c.id}>
          {c.name}
        </option>
      ))}
    </>
  )
}

export function DocumentsPage() {
  usePageTitle(t.documents.title)
  const queryClient = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)
  const nextKey = useRef(0)
  const optionsId = useId()
  const [uploads, setUploads] = useState<UploadEntry[]>([])
  const [dragging, setDragging] = useState(false)
  const [uploadCategory, setUploadCategory] = useState(NONE)
  const [useFilename, setUseFilename] = useState(false)
  const [filter, setFilter] = useState(ALL)
  const [editing, setEditing] = useState<string | null>(null)

  const documents = useQuery({
    queryKey: DOCUMENTS_KEY,
    queryFn: listDocuments,
    // Mientras haya documentos en proceso, se consulta periódicamente su estado.
    refetchInterval: (query) => (isBusy(query.state.data) ? POLL_MS : false),
  })
  const busy = isBusy(documents.data)
  const stats = useQuery({
    queryKey: STATS_KEY,
    queryFn: getIndexStats,
    refetchInterval: busy ? POLL_MS : false,
  })
  const categories = useQuery({ queryKey: CATEGORIES_KEY, queryFn: listCategories })
  const categoryList = categories.data ?? []
  const categoryName = new Map(categoryList.map((c) => [c.id, c.name]))

  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY }),
      queryClient.invalidateQueries({ queryKey: STATS_KEY }),
      queryClient.invalidateQueries({ queryKey: CATEGORIES_KEY }),
    ])

  const remove = useMutation({ mutationFn: deleteDocument, onSettled: refresh })
  const reindex = useMutation({ mutationFn: reindexDocument, onSettled: refresh })
  const reindexEverything = useMutation({ mutationFn: reindexAll, onSettled: refresh })
  const reindexStale = useMutation({ mutationFn: reindexOutdated, onSettled: refresh })
  const actionFailed =
    remove.isError || reindex.isError || reindexEverything.isError || reindexStale.isError

  function update(key: number, patch: Partial<UploadEntry>) {
    setUploads((list) => list.map((u) => (u.key === key ? { ...u, ...patch } : u)))
  }

  async function uploadFiles(files: File[]) {
    const options = { useFilename, categoryId: toCategoryId(uploadCategory) }
    const entries = files.map((file) => ({ key: nextKey.current++, file }))
    setUploads((list) => [
      ...entries.map(({ key, file }) => ({
        key,
        name: file.name,
        state: 'uploading' as const,
      })),
      ...list,
    ])
    for (const { key, file } of entries) {
      if (!isAccepted(file)) {
        update(key, { state: 'error', message: t.documents.unsupported })
        continue
      }
      try {
        const results = await uploadDocument(file, options)
        const [first] = results
        if (results.length === 1 && !first.archive) {
          update(key, { state: resultState(first), message: first.error ?? undefined })
        } else {
          // ZIP: un resumen y, debajo, cada fichero.
          const uploaded = results.filter((r) => !r.error).length
          const children = results.map((result) => ({
            key: nextKey.current++,
            name: `${file.name} › ${result.filename}`,
            state: resultState(result),
            message: result.error ?? undefined,
          }))
          setUploads((list) =>
            list.flatMap((u) =>
              u.key === key
                ? [
                    {
                      ...u,
                      state: 'archive' as const,
                      message: t.documents.archiveSummary(uploaded, results.length - uploaded),
                    },
                    ...children,
                  ]
                : [u],
            ),
          )
        }
      } catch (error) {
        const message =
          error instanceof ApiError && error.detail ? error.detail : t.documents.uploadFailed
        update(key, { state: 'error', message })
      }
      await refresh()
    }
  }

  function onDrop(event: DragEvent) {
    event.preventDefault()
    setDragging(false)
    void uploadFiles(Array.from(event.dataTransfer.files))
  }

  const outdated = stats.data?.outdated ?? 0
  const visible = (documents.data ?? []).filter(
    (d) =>
      filter === ALL ||
      (filter === NONE ? d.category_id === null : d.category_id === Number(filter)),
  )

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6">
      <section className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{t.documents.title}</h1>
          <p className="text-sm text-texto-secundario">{t.documents.description}</p>
          {stats.data && (
            <p className="mt-1 text-sm text-texto" data-testid="index-stats">
              {t.documents.stats(stats.data.documents, stats.data.chunks)} ·{' '}
              {t.documents.model(stats.data.embedding_model)}
            </p>
          )}
          {!busy && outdated > 0 && (
            <p role="status" className="mt-1 text-sm text-aviso">
              {t.documents.outdated(outdated)}
            </p>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy || outdated === 0 || reindexStale.isPending}
            onClick={() => reindexStale.mutate()}
            className="btn btn-secundario"
          >
            {t.documents.reindexOutdated}
          </button>
          <button
            type="button"
            disabled={!documents.data?.length || reindexEverything.isPending}
            onClick={() => {
              if (window.confirm(t.documents.confirmReindexAll)) reindexEverything.mutate()
            }}
            className="btn btn-secundario"
          >
            {t.documents.reindexAll}
          </button>
        </div>
      </section>

      <section className="flex flex-col gap-3">
        <fieldset
          aria-describedby={`${optionsId}-hint`}
          className="flex flex-wrap items-end gap-x-6 gap-y-2"
        >
          <legend className="sr-only">{t.documents.uploadOptions}</legend>
          <label className="flex flex-col gap-1 text-sm">
            {t.documents.uploadCategory}
            <select
              value={uploadCategory}
              onChange={(e) => setUploadCategory(e.target.value)}
              className="campo text-sm"
            >
              <CategoryOptions categories={categoryList} />
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={useFilename}
              onChange={(e) => setUseFilename(e.target.checked)}
            />
            {t.documents.useFilename}
          </label>
          <p id={`${optionsId}-hint`} className="w-full text-xs text-texto-secundario">
            {t.documents.titleHint}
          </p>
        </fieldset>
        <div
          role="region"
          aria-label={t.documents.dropzone}
          onDragOver={(e) => {
            e.preventDefault()
            setDragging(true)
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={onDrop}
          className={`flex flex-col items-center gap-2 rounded-lg border-2 border-dashed p-6 md:p-8 text-center ${
            dragging ? 'border-marca bg-marca-suave' : 'border-borde-control bg-fondo'
          }`}
        >
          <p className="text-texto">{t.documents.dropzone}</p>
          <p className="text-xs text-texto-secundario">{t.documents.or}</p>
          <button
            type="button"
            onClick={() => inputRef.current?.click()}
            className="btn btn-primario"
          >
            {t.documents.select}
          </button>
          <input
            ref={inputRef}
            type="file"
            multiple
            hidden
            data-testid="file-input"
            accept={ACCEPTED_EXTENSIONS.join(',')}
            onChange={(e) => {
              const files = Array.from(e.target.files ?? [])
              e.target.value = ''
              if (files.length) void uploadFiles(files)
            }}
          />
        </div>
      </section>

      {uploads.length > 0 && (
        <section aria-labelledby="uploads-title">
          <h2 id="uploads-title" className="mb-2 text-sm font-bold">
            {t.documents.uploads}
          </h2>
          <ul className="flex flex-col gap-1 text-sm">
            {uploads.map((u) => (
              <li key={u.key} data-state={u.state} className="flex gap-2">
                <span className="font-medium">{u.name}</span>
                <span className={u.state === 'error' ? 'text-error' : 'text-texto-secundario'}>
                  {u.state === 'uploading'
                    ? t.documents.uploading
                    : u.state === 'uploaded'
                      ? t.documents.uploaded
                      : u.state === 'duplicate'
                        ? t.documents.duplicate
                        : u.message}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {actionFailed && (
        <p role="alert" className="text-sm text-error">
          {t.documents.actionError}
        </p>
      )}

      <section className="flex flex-col gap-3">
        {categoryList.length > 0 && (
          <label className="flex items-center gap-2 self-start text-sm">
            {t.documents.filter}
            <select
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              className="campo text-sm"
            >
              <option value={ALL}>{t.documents.allCategories}</option>
              <CategoryOptions categories={categoryList} />
            </select>
          </label>
        )}
        {documents.isPending ? (
          <p className="text-texto-secundario">{t.common.loading}</p>
        ) : documents.isError ? (
          <p role="alert" className="text-error">
            {t.documents.loadError}
          </p>
        ) : documents.data.length === 0 ? (
          <p className="text-texto-secundario">{t.documents.empty}</p>
        ) : visible.length === 0 ? (
          <p className="text-texto-secundario">{t.documents.emptyFilter}</p>
        ) : (
          <div
            role="region"
            aria-label={t.documents.tableCaption}
            tabIndex={0}
            className="overflow-x-auto rounded-lg border border-borde bg-fondo"
          >
            <table className="w-full text-left text-sm">
              <caption className="sr-only">{t.documents.tableCaption}</caption>
              <thead className="bg-fondo-alt text-texto-secundario">
                <tr>
                  {Object.values(t.documents.columns).map((label) => (
                    <th key={label} scope="col" className="px-3 py-2 font-medium">
                      {label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visible.map((d) =>
                  editing === d.id ? (
                    <EditRow
                      key={d.id}
                      document={d}
                      categories={categoryList}
                      onClose={() => setEditing(null)}
                      onSaved={async () => {
                        setEditing(null)
                        await refresh()
                      }}
                    />
                  ) : (
                    <tr key={d.id} className="border-t border-borde align-top">
                      <td className="px-3 py-2">
                        <a
                          href={documentFileUrl(d.id)}
                          download={d.filename}
                          aria-label={t.documents.download(d.title)}
                        >
                          {d.title}
                        </a>
                        {d.title !== d.filename && (
                          <p className="text-xs break-all text-texto-secundario">{d.filename}</p>
                        )}
                      </td>
                      <td className="px-3 py-2">
                        {d.category_id === null ? '—' : categoryName.get(d.category_id)}
                      </td>
                      <td className="px-3 py-2">{t.documents.kinds[d.kind]}</td>
                      <td className="px-3 py-2 whitespace-nowrap">{formatBytes(d.size_bytes)}</td>
                      <td className="px-3 py-2">
                        <span
                          className={`rounded border px-2 py-0.5 text-xs ${statusStyles[d.status]}`}
                        >
                          {t.documents.status[d.status]}
                        </span>
                        {d.outdated && (
                          <p className="mt-1 text-xs text-aviso">{t.documents.outdatedBadge}</p>
                        )}
                        {d.error && <p className="mt-1 text-xs text-error">{d.error}</p>}
                      </td>
                      <td className="px-3 py-2">{d.status === 'ready' ? d.chunk_count : '—'}</td>
                      <td className="px-3 py-2 whitespace-nowrap">
                        {new Date(d.created_at).toLocaleString('es-ES', {
                          dateStyle: 'short',
                          timeStyle: 'short',
                        })}
                      </td>
                      <td className="px-3 py-2">
                        <div className="flex gap-2">
                          <button
                            type="button"
                            aria-label={`${t.documents.edit}: ${d.title}`}
                            onClick={() => setEditing(d.id)}
                            className="btn btn-secundario"
                          >
                            {t.documents.edit}
                          </button>
                          <button
                            type="button"
                            aria-label={`${t.documents.reindex}: ${d.title}`}
                            disabled={d.status === 'pending' || d.status === 'processing'}
                            onClick={() => reindex.mutate(d.id)}
                            className="btn btn-secundario"
                          >
                            {t.documents.reindex}
                          </button>
                          <button
                            type="button"
                            aria-label={`${t.documents.delete}: ${d.title}`}
                            onClick={() => {
                              if (window.confirm(t.documents.confirmDelete(d.title))) {
                                remove.mutate(d.id)
                              }
                            }}
                            className="btn btn-peligro"
                          >
                            {t.documents.delete}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <CategoryManager categories={categoryList} onChanged={refresh} />
    </div>
  )
}

interface EditRowProps {
  document: DocumentItem
  categories: Category[]
  onClose: () => void
  onSaved: () => Promise<unknown>
}

/** Fila de la tabla en edición: título y categoría. */
function EditRow({ document, categories, onClose, onSaved }: EditRowProps) {
  const id = useId()
  const [title, setTitle] = useState(document.title)
  const [category, setCategory] = useState(
    document.category_id === null ? NONE : String(document.category_id),
  )
  const save = useMutation({
    mutationFn: () =>
      updateDocument(document.id, { title: title.trim(), category_id: toCategoryId(category) }),
    onSuccess: onSaved,
  })

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    if (title.trim()) save.mutate()
  }

  return (
    <tr className="border-t border-borde bg-fondo-alt align-top">
      <td colSpan={Object.keys(t.documents.columns).length} className="px-3 py-3">
        <form
          onSubmit={onSubmit}
          onKeyDown={(e) => {
            if (e.key === 'Escape') onClose()
          }}
          aria-label={t.documents.editing(document.title)}
          className="flex flex-wrap items-end gap-3"
        >
          <div className="flex min-w-64 grow flex-col gap-1">
            <label htmlFor={`${id}-title`}>{t.documents.titleLabel}</label>
            <input
              id={`${id}-title`}
              value={title}
              maxLength={255}
              autoFocus
              aria-describedby={`${id}-file`}
              onChange={(e) => setTitle(e.target.value)}
              className="campo"
            />
            <span id={`${id}-file`} className="text-xs break-all text-texto-secundario">
              {t.documents.fileLabel(document.filename)}
            </span>
          </div>
          <button
            type="button"
            disabled={title === document.filename}
            onClick={() => setTitle(document.filename)}
            className="btn btn-terciario"
          >
            {t.documents.titleFromFilename}
          </button>
          <label className="flex flex-col gap-1">
            {t.documents.categoryLabel}
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              className="campo"
            >
              <CategoryOptions categories={categories} />
            </select>
          </label>
          <div className="flex gap-2">
            <button
              type="submit"
              disabled={!title.trim() || save.isPending}
              className="btn btn-primario"
            >
              {t.documents.save}
            </button>
            <button type="button" onClick={onClose} className="btn btn-secundario">
              {t.documents.cancel}
            </button>
          </div>
          <p className="w-full text-xs text-texto-secundario">{t.documents.editHint}</p>
          {save.isError && (
            <p role="alert" className="w-full text-sm text-error">
              {save.error instanceof ApiError && save.error.detail
                ? save.error.detail
                : t.documents.actionError}
            </p>
          )}
        </form>
      </td>
    </tr>
  )
}
