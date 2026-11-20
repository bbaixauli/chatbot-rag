import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef, useState, type DragEvent } from 'react'
import {
  ACCEPTED_EXTENSIONS,
  deleteDocument,
  documentFileUrl,
  getIndexStats,
  listDocuments,
  reindexAll,
  reindexDocument,
  uploadDocument,
  type DocumentItem,
  type DocumentStatus,
} from '../../api/documents'
import { usePageTitle } from '../../hooks/usePageTitle'
import { t } from '../../i18n/es'
import { formatBytes } from '../../utils/format'

const DOCUMENTS_KEY = ['admin', 'documents'] as const
const STATS_KEY = ['admin', 'documents-stats'] as const
const POLL_MS = 1500

interface UploadEntry {
  key: number
  name: string
  state: 'uploading' | 'uploaded' | 'duplicate' | 'error'
  message?: string
}

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

export function DocumentsPage() {
  usePageTitle(t.documents.title)
  const queryClient = useQueryClient()
  const inputRef = useRef<HTMLInputElement>(null)
  const nextKey = useRef(0)
  const [uploads, setUploads] = useState<UploadEntry[]>([])
  const [dragging, setDragging] = useState(false)

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

  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY }),
      queryClient.invalidateQueries({ queryKey: STATS_KEY }),
    ])

  const remove = useMutation({ mutationFn: deleteDocument, onSettled: refresh })
  const reindex = useMutation({ mutationFn: reindexDocument, onSettled: refresh })
  const reindexEverything = useMutation({ mutationFn: reindexAll, onSettled: refresh })
  const actionFailed = remove.isError || reindex.isError || reindexEverything.isError

  function update(key: number, patch: Partial<UploadEntry>) {
    setUploads((list) => list.map((u) => (u.key === key ? { ...u, ...patch } : u)))
  }

  async function uploadFiles(files: File[]) {
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
        const result = await uploadDocument(file)
        if (result.error) update(key, { state: 'error', message: result.error })
        else update(key, { state: result.duplicate ? 'duplicate' : 'uploaded' })
      } catch {
        update(key, { state: 'error', message: t.documents.uploadFailed })
      }
      await refresh()
    }
  }

  function onDrop(event: DragEvent) {
    event.preventDefault()
    setDragging(false)
    void uploadFiles(Array.from(event.dataTransfer.files))
  }

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
          {!busy && (stats.data?.outdated ?? 0) > 0 && (
            <p role="status" className="mt-1 text-sm text-aviso">
              {t.documents.outdated(stats.data!.outdated)}
            </p>
          )}
        </div>
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
      </section>

      <section
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

      <section>
        {documents.isPending ? (
          <p className="text-texto-secundario">{t.common.loading}</p>
        ) : documents.isError ? (
          <p role="alert" className="text-error">
            {t.documents.loadError}
          </p>
        ) : documents.data.length === 0 ? (
          <p className="text-texto-secundario">{t.documents.empty}</p>
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
                {documents.data.map((d) => (
                  <tr key={d.id} className="border-t border-borde align-top">
                    <td className="px-3 py-2">
                      <a
                        href={documentFileUrl(d.id)}
                        download={d.filename}
                        aria-label={t.documents.download(d.filename)}
                      >
                        {d.filename}
                      </a>
                    </td>
                    <td className="px-3 py-2">{t.documents.kinds[d.kind]}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{formatBytes(d.size_bytes)}</td>
                    <td className="px-3 py-2">
                      <span
                        className={`rounded border px-2 py-0.5 text-xs ${statusStyles[d.status]}`}
                      >
                        {t.documents.status[d.status]}
                      </span>
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
                          aria-label={`${t.documents.reindex}: ${d.filename}`}
                          disabled={d.status === 'pending' || d.status === 'processing'}
                          onClick={() => reindex.mutate(d.id)}
                          className="btn btn-secundario"
                        >
                          {t.documents.reindex}
                        </button>
                        <button
                          type="button"
                          aria-label={`${t.documents.delete}: ${d.filename}`}
                          onClick={() => {
                            if (window.confirm(t.documents.confirmDelete(d.filename))) {
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
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}
