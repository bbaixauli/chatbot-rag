import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { ApiError } from '../../api/client'
import { createCategory, deleteCategory, renameCategory, type Category } from '../../api/documents'
import { t } from '../../i18n/es'

interface Props {
  categories: Category[]
  /** Se llama tras cualquier cambio (las categorías cambian los documentos). */
  onChanged: () => Promise<unknown>
}

function errorMessage(error: unknown): string {
  return error instanceof ApiError && error.detail ? error.detail : t.categories.error
}

/** Alta, renombrado y borrado de las categorías de los documentos (un solo nivel). */
export function CategoryManager({ categories, onChanged }: Props) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [editing, setEditing] = useState<{ id: number; name: string } | null>(null)
  const [error, setError] = useState<string | null>(null)

  const done = async () => {
    setError(null)
    await queryClient.invalidateQueries({ queryKey: ['admin', 'categories'] })
    await onChanged()
  }
  const create = useMutation({
    mutationFn: createCategory,
    onSuccess: async () => {
      setName('')
      await done()
    },
    onError: (e) => setError(errorMessage(e)),
  })
  const rename = useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) => renameCategory(id, name),
    onSuccess: async () => {
      setEditing(null)
      await done()
    },
    onError: (e) => setError(errorMessage(e)),
  })
  const remove = useMutation({
    mutationFn: deleteCategory,
    onSuccess: done,
    onError: (e) => setError(errorMessage(e)),
  })

  function onCreate(event: FormEvent) {
    event.preventDefault()
    if (name.trim()) create.mutate(name.trim())
  }

  function onRename(event: FormEvent) {
    event.preventDefault()
    if (editing && editing.name.trim()) rename.mutate({ ...editing, name: editing.name.trim() })
  }

  return (
    <section aria-labelledby="categories-title" className="flex flex-col gap-3">
      <div>
        <h2 id="categories-title" className="text-lg font-bold">
          {t.categories.title}
        </h2>
        <p className="text-sm text-texto-secundario">{t.categories.description}</p>
      </div>
      <form onSubmit={onCreate} className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-sm">
          {t.categories.newName}
          <input
            value={name}
            maxLength={100}
            onChange={(e) => setName(e.target.value)}
            className="campo"
          />
        </label>
        <button
          type="submit"
          disabled={!name.trim() || create.isPending}
          className="btn btn-secundario"
        >
          {t.categories.add}
        </button>
      </form>
      {error && (
        <p role="alert" className="text-sm text-error">
          {error}
        </p>
      )}
      {categories.length === 0 ? (
        <p className="text-sm text-texto-secundario">{t.categories.empty}</p>
      ) : (
        <ul aria-label={t.categories.title} className="flex flex-col gap-1 text-sm">
          {categories.map((category) =>
            editing?.id === category.id ? (
              <li key={category.id}>
                <form onSubmit={onRename} className="flex flex-wrap items-center gap-2">
                  <input
                    aria-label={t.categories.rename(category.name)}
                    value={editing.name}
                    maxLength={100}
                    autoFocus
                    onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                    onKeyDown={(e) => {
                      if (e.key === 'Escape') setEditing(null)
                    }}
                    className="campo"
                  />
                  <button
                    type="submit"
                    disabled={!editing.name.trim() || rename.isPending}
                    className="btn btn-primario"
                  >
                    {t.categories.save}
                  </button>
                  <button
                    type="button"
                    onClick={() => setEditing(null)}
                    className="btn btn-secundario"
                  >
                    {t.categories.cancel}
                  </button>
                </form>
              </li>
            ) : (
              <li key={category.id} className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{category.name}</span>
                <span className="text-texto-secundario">
                  {t.categories.documents(category.documents)}
                </span>
                <button
                  type="button"
                  aria-label={t.categories.rename(category.name)}
                  onClick={() => {
                    setError(null)
                    setEditing({ id: category.id, name: category.name })
                  }}
                  className="btn btn-terciario"
                >
                  {t.categories.renameShort}
                </button>
                <button
                  type="button"
                  aria-label={t.categories.delete(category.name)}
                  onClick={() => {
                    if (window.confirm(t.categories.confirmDelete(category.name))) {
                      remove.mutate(category.id)
                    }
                  }}
                  className="btn btn-terciario"
                >
                  {t.categories.deleteShort}
                </button>
              </li>
            ),
          )}
        </ul>
      )}
    </section>
  )
}
