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
  pending: 'bg-gray-100 text-gray-700',
  processing: 'bg-blue-100 text-blue-800',
  ready: 'bg-emerald-100 text-emerald-800',
  error: 'bg-red-100 text-red-800',
}

const isAccepted = (file: File) =>
  ACCEPTED_EXTENSIONS.some((ext) => file.name.toLowerCase().endsWith(ext))

const isBusy = (documents: DocumentItem[] | undefined) =>
  documents?.some((d) => d.status === 'pending' || d.status === 'processing') ?? false

export function DocumentsPage() {
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
          <h2 className="text-xl font-semibold">{t.documents.title}</h2>
          <p className="text-sm text-gray-600">{t.documents.description}</p>
          {stats.data && (
            <p className="mt-1 text-sm text-gray-700" data-testid="index-stats">
              {t.documents.stats(stats.data.documents, stats.data.chunks)} ·{' '}
              {t.documents.model(stats.data.embedding_model)}
            </p>
          )}
        </div>
        <button
          type="button"
          disabled={!documents.data?.length || reindexEverything.isPending}
          onClick={() => {
            if (window.confirm(t.documents.confirmReindexAll)) reindexEverything.mutate()
          }}
          className="rounded-md border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50 disabled:opacity-50"
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
        className={`flex flex-col items-center gap-2 rounded-xl border-2 border-dashed p-8 text-center ${
          dragging ? 'border-blue-500 bg-blue-50' : 'border-gray-300 bg-white'
        }`}
      >
        <p className="text-gray-700">{t.documents.dropzone}</p>
        <p className="text-xs text-gray-500">{t.documents.or}</p>
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white"
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
          <h3 id="uploads-title" className="mb-2 text-sm font-semibold">
            {t.documents.uploads}
          </h3>
          <ul className="flex flex-col gap-1 text-sm">
            {uploads.map((u) => (
              <li key={u.key} data-state={u.state} className="flex gap-2">
                <span className="font-medium">{u.name}</span>
                <span className={u.state === 'error' ? 'text-red-700' : 'text-gray-600'}>
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
        <p role="alert" className="text-sm text-red-700">
          {t.documents.actionError}
        </p>
      )}

      <section>
        {documents.isPending ? (
          <p className="text-gray-600">{t.common.loading}</p>
        ) : documents.isError ? (
          <p role="alert" className="text-red-700">
            {t.documents.loadError}
          </p>
        ) : documents.data.length === 0 ? (
          <p className="text-gray-600">{t.documents.empty}</p>
        ) : (
          <div className="overflow-x-auto rounded-lg border border-gray-200 bg-white">
            <table className="w-full text-left text-sm">
              <thead className="bg-gray-50 text-gray-600">
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
                  <tr key={d.id} className="border-t border-gray-100 align-top">
                    <td className="px-3 py-2">
                      <a
                        href={documentFileUrl(d.id)}
                        download={d.filename}
                        aria-label={t.documents.download(d.filename)}
                        className="text-blue-700 hover:underline"
                      >
                        {d.filename}
                      </a>
                    </td>
                    <td className="px-3 py-2">{t.documents.kinds[d.kind]}</td>
                    <td className="px-3 py-2 whitespace-nowrap">{formatBytes(d.size_bytes)}</td>
                    <td className="px-3 py-2">
                      <span
                        className={`rounded-full px-2 py-0.5 text-xs ${statusStyles[d.status]}`}
                      >
                        {t.documents.status[d.status]}
                      </span>
                      {d.error && <p className="mt-1 text-xs text-red-700">{d.error}</p>}
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
                          className="rounded border border-gray-300 px-2 py-1 text-xs hover:bg-gray-50 disabled:opacity-50"
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
                          className="rounded border border-red-200 px-2 py-1 text-xs text-red-700 hover:bg-red-50"
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
