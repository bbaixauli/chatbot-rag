import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { Category, DocumentItem, IndexStats, UploadResult } from '../../api/documents'
import { health, jsonResponse, mockApi, renderApp } from '../../test/utils'
import { formatBytes } from '../../utils/format'

const doc = (id: string, filename: string, patch: Partial<DocumentItem> = {}): DocumentItem => ({
  id,
  filename,
  title: filename,
  category_id: null,
  kind: 'markdown',
  size_bytes: 2048,
  status: 'ready',
  chunk_count: 3,
  error: null,
  embedding_model: 'fake',
  chunk_size_chars: 3000,
  chunk_overlap_chars: 400,
  uploaded_by: 'admin',
  created_at: '2026-11-17T10:00:00Z',
  updated_at: '2026-11-17T10:00:00Z',
  outdated: false,
  ...patch,
})

const statsFor = (docs: DocumentItem[]): IndexStats => ({
  documents: docs.length,
  chunks: docs.filter((d) => d.status === 'ready').reduce((n, d) => n + d.chunk_count, 0),
  by_status: {},
  embedding_model: 'bge-m3',
  outdated: docs.filter((d) => d.outdated).length,
})

function documentsApi(
  initial: DocumentItem[],
  overrides: Parameters<typeof mockApi>[0] = {},
  categories: Category[] = [],
) {
  const state = { docs: initial, categories }
  const api = mockApi({
    'GET /health': health,
    'GET /admin/session': () =>
      jsonResponse({ authenticated: true, username: 'admin', enabled: true }),
    'GET /admin/documents': () => jsonResponse(state.docs),
    'GET /admin/documents/stats': () => jsonResponse(statsFor(state.docs)),
    'GET /admin/categories': () => jsonResponse(state.categories),
    ...overrides,
  })
  return { api, state }
}

const rrhh: Category = { id: 1, name: 'Recursos humanos', documents: 1 }
const calidad: Category = { id: 2, name: 'Calidad', documents: 0 }

const file = (name: string, content = '# hola') => new File([content], name)

describe('formatBytes', () => {
  it.each([
    [512, '512 B'],
    [2048, '2 KB'],
    [1536 * 1024, '1,5 MB'],
    [5 * 1024 ** 4, '5120 GB'], // en español, 4 cifras van sin separador de miles
  ])('%d → %s', (bytes, text) => expect(formatBytes(bytes)).toBe(text))
})

describe('DocumentsPage', () => {
  it('muestra estadísticas, tabla y estado vacío', async () => {
    documentsApi([
      doc('1', 'guia.md'),
      doc('2', 'roto.pdf', { kind: 'pdf', status: 'error', error: 'Sin texto (OCR)' }),
    ])
    renderApp('/admin/documents')

    expect(await screen.findByTestId('index-stats')).toHaveTextContent(
      '2 documentos · 3 fragmentos indexados · Modelo de embeddings: bge-m3',
    )
    const row = screen.getByRole('row', { name: /roto\.pdf/ })
    expect(within(row).getByText('Error')).toBeVisible()
    expect(within(row).getByText('Sin texto (OCR)')).toBeVisible()
    expect(within(row).getAllByText('—')).toHaveLength(2) // sin categoría ni fragmentos
    const link = screen.getByRole('link', { name: 'Descargar guia.md' })
    expect(link).toHaveAttribute('href', '/api/v1/admin/documents/1/file')
    expect(screen.getByRole('row', { name: /guia\.md/ })).toHaveTextContent('2 KB')
  })

  it('avisa de los documentos desactualizados y los reindexa', async () => {
    const { state, api } = documentsApi([doc('1', 'a.md'), doc('2', 'b.md', { outdated: true })], {
      'POST /admin/documents/reindex-outdated': () => {
        state.docs = state.docs.map((d) => ({ ...d, outdated: false }))
        return jsonResponse({ scheduled: 1 })
      },
    })
    renderApp('/admin/documents')

    expect(await screen.findByText(/1 documento está desactualizado/)).toBeInTheDocument()
    expect(screen.getByRole('row', { name: /b\.md/ })).toHaveTextContent('Desactualizado')
    expect(screen.getByRole('row', { name: /a\.md/ })).not.toHaveTextContent('Desactualizado')

    await userEvent.click(screen.getByRole('button', { name: 'Reindexar desactualizados' }))

    await waitFor(() => expect(screen.queryByText(/está desactualizado/)).toBeNull())
    expect(api).toHaveBeenCalledWith(
      '/api/v1/admin/documents/reindex-outdated',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(screen.getByRole('button', { name: 'Reindexar desactualizados' })).toBeDisabled()
  })

  it('muestra el título, el fichero y la categoría', async () => {
    documentsApi(
      [doc('1', 'nor13.pdf', { title: 'Normativa de acceso', kind: 'pdf', category_id: 1 })],
      {},
      [rrhh],
    )
    renderApp('/admin/documents')

    const row = await screen.findByRole('row', { name: /Normativa de acceso/ })
    expect(row).toHaveTextContent('nor13.pdf')
    expect(row).toHaveTextContent('Recursos humanos')
    const link = within(row).getByRole('link', { name: 'Descargar Normativa de acceso' })
    expect(link).toHaveAttribute('download', 'nor13.pdf')
  })

  it('sube con la categoría y el nombre del fichero como título', async () => {
    let sent: FormData | undefined
    documentsApi(
      [],
      {
        'POST /admin/documents': (init) => {
          sent = init!.body as FormData
          return jsonResponse([
            { filename: 'a.md', document: doc('a', 'a.md'), duplicate: false, error: null },
          ])
        },
      },
      [rrhh],
    )
    renderApp('/admin/documents')

    await screen.findAllByRole('option', { name: 'Recursos humanos' })
    await userEvent.selectOptions(
      await screen.findByRole('combobox', { name: 'Categoría de los documentos que subas' }),
      'Recursos humanos',
    )
    await userEvent.click(
      screen.getByRole('checkbox', { name: 'Usar el nombre del fichero como título' }),
    )
    await userEvent.upload(screen.getByTestId('file-input'), file('a.md'))

    await waitFor(() => expect(sent).toBeDefined())
    expect(sent!.get('category_id')).toBe('1')
    expect(sent!.get('use_filename')).toBe('true')
  })

  it('por defecto sube sin categoría y con el título de los metadatos', async () => {
    let sent: FormData | undefined
    documentsApi([], {
      'POST /admin/documents': (init) => {
        sent = init!.body as FormData
        return jsonResponse([
          { filename: 'a.md', document: doc('a', 'a.md'), duplicate: false, error: null },
        ])
      },
    })
    renderApp('/admin/documents')

    await userEvent.upload(await screen.findByTestId('file-input'), file('a.md'))

    await waitFor(() => expect(sent).toBeDefined())
    expect(sent!.has('category_id')).toBe(false)
    expect(sent!.has('use_filename')).toBe(false)
  })

  it('edita el título y la categoría de un documento', async () => {
    let body: unknown
    const { state } = documentsApi(
      [doc('1', 'nor13.pdf', { title: 'Normativa', kind: 'pdf' })],
      {
        'PATCH /admin/documents/1': (init) => {
          body = JSON.parse(init!.body as string)
          state.docs = [{ ...state.docs[0], title: 'Normativa de acceso', category_id: 2 }]
          return jsonResponse(state.docs[0])
        },
      },
      [rrhh, calidad],
    )
    renderApp('/admin/documents')

    await userEvent.click(await screen.findByRole('button', { name: 'Editar: Normativa' }))
    const form = screen.getByRole('form', { name: 'Editar «Normativa»' })
    const title = within(form).getByRole('textbox', { name: 'Título' })
    expect(title).toHaveAccessibleDescription('Fichero: nor13.pdf')
    await userEvent.click(within(form).getByRole('button', { name: 'Usar el nombre del fichero' }))
    expect(title).toHaveValue('nor13.pdf')
    await userEvent.clear(title)
    await userEvent.type(title, '  Normativa de acceso ')
    await userEvent.selectOptions(
      within(form).getByRole('combobox', { name: 'Categoría' }),
      'Calidad',
    )
    await userEvent.click(within(form).getByRole('button', { name: 'Guardar' }))

    expect(await screen.findByRole('row', { name: /Normativa de acceso/ })).toHaveTextContent(
      'Calidad',
    )
    expect(body).toEqual({ title: 'Normativa de acceso', category_id: 2 })
    expect(screen.queryByRole('form', { name: /Editar/ })).toBeNull()
  })

  it('cancela la edición y no guarda un título vacío', async () => {
    const { api } = documentsApi([doc('1', 'a.md')])
    renderApp('/admin/documents')

    await userEvent.click(await screen.findByRole('button', { name: 'Editar: a.md' }))
    const form = screen.getByRole('form', { name: 'Editar «a.md»' })
    await userEvent.clear(within(form).getByRole('textbox', { name: 'Título' }))
    expect(within(form).getByRole('button', { name: 'Guardar' })).toBeDisabled()
    await userEvent.click(within(form).getByRole('button', { name: 'Cancelar' }))

    expect(screen.queryByRole('form', { name: /Editar/ })).toBeNull()
    expect(api.mock.calls.some(([, init]) => init?.method === 'PATCH')).toBe(false)
  })

  it('filtra los documentos por categoría', async () => {
    documentsApi(
      [
        doc('1', 'a.md', { category_id: 1 }),
        doc('2', 'b.md'),
        doc('3', 'c.md', { category_id: 2 }),
      ],
      {},
      [rrhh, calidad],
    )
    renderApp('/admin/documents')
    const filter = await screen.findByRole('combobox', { name: 'Categoría' })
    const names = () =>
      screen.getAllByRole('link', { name: /^Descargar/ }).map((link) => link.textContent)

    expect(names()).toEqual(['a.md', 'b.md', 'c.md'])
    await userEvent.selectOptions(filter, 'Recursos humanos')
    expect(names()).toEqual(['a.md'])
    await userEvent.selectOptions(filter, 'Sin categoría')
    expect(names()).toEqual(['b.md'])
  })

  it('crea, renombra y elimina categorías', async () => {
    const state = { categories: [rrhh] }
    documentsApi([], {
      'GET /admin/categories': () => jsonResponse(state.categories),
      'POST /admin/categories': (init) => {
        const { name } = JSON.parse(init!.body as string) as { name: string }
        if (name === 'Recursos humanos') {
          return jsonResponse({ detail: 'Ya existe la categoría «Recursos humanos»' }, 409)
        }
        state.categories = [...state.categories, { id: 3, name, documents: 0 }]
        return jsonResponse(state.categories.at(-1), 201)
      },
      'PATCH /admin/categories/1': () => {
        state.categories = state.categories.map((c) =>
          c.id === 1 ? { ...c, name: 'Personas' } : c,
        )
        return jsonResponse(state.categories[0])
      },
      'DELETE /admin/categories/3': () => {
        state.categories = state.categories.filter((c) => c.id !== 3)
        return jsonResponse(null, 204)
      },
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderApp('/admin/documents')
    const list = await screen.findByRole('list', { name: 'Categorías' })
    expect(list).toHaveTextContent('Recursos humanos1 documento')

    const name = screen.getByRole('textbox', { name: 'Nueva categoría' })
    await userEvent.type(name, 'Recursos humanos')
    await userEvent.click(screen.getByRole('button', { name: 'Añadir' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Ya existe la categoría «Recursos humanos»',
    )

    await userEvent.clear(name)
    await userEvent.type(name, 'Calidad{Enter}')
    await waitFor(() => expect(list).toHaveTextContent('Calidad'))
    expect(name).toHaveValue('')
    expect(screen.queryByRole('alert')).toBeNull()

    await userEvent.click(
      screen.getByRole('button', { name: 'Renombrar la categoría «Recursos humanos»' }),
    )
    const rename = screen.getByRole('textbox', {
      name: 'Renombrar la categoría «Recursos humanos»',
    })
    await userEvent.clear(rename)
    await userEvent.type(rename, 'Personas{Enter}')
    await waitFor(() => expect(list).toHaveTextContent('Personas'))

    await userEvent.click(screen.getByRole('button', { name: 'Eliminar la categoría «Calidad»' }))
    await waitFor(() => expect(list).not.toHaveTextContent('Calidad'))
  })

  it('indica cuando no hay documentos y desactiva reindexar todo', async () => {
    documentsApi([])
    renderApp('/admin/documents')
    expect(await screen.findByText('Todavía no hay documentos.')).toBeVisible()
    expect(await screen.findByTestId('index-stats')).toHaveTextContent(
      '0 documentos · 0 fragmentos',
    )
    expect(screen.getByRole('button', { name: 'Reindexar todo' })).toBeDisabled()
  })

  it('sube ficheros y muestra el resultado de cada uno', async () => {
    const { state, api } = documentsApi([], {
      'POST /admin/documents': async (init) => {
        const sent = (init!.body as FormData).get('files') as File
        const results: Record<string, UploadResult> = {
          'nuevo.md': {
            filename: 'nuevo.md',
            document: doc('n', 'nuevo.md', { status: 'pending' }),
            duplicate: false,
            error: null,
          },
          'copia.md': {
            filename: 'copia.md',
            document: doc('n', 'nuevo.md'),
            duplicate: true,
            error: null,
          },
          'grande.pdf': {
            filename: 'grande.pdf',
            document: null,
            duplicate: false,
            error: 'Supera el tamaño máximo',
          },
        }
        const result = results[sent.name]
        if (!result) return jsonResponse({}, 500)
        if (result.document && !result.duplicate) state.docs = [result.document]
        return jsonResponse([result])
      },
    })
    renderApp('/admin/documents')
    const input = await screen.findByTestId('file-input')

    await userEvent.upload(
      input,
      [file('nuevo.md'), file('copia.md'), file('grande.pdf'), file('falla.html')],
      { applyAccept: false },
    )

    const uploads = await screen.findByRole('region', { name: 'Subidas' })
    await waitFor(() => expect(within(uploads).queryByText('Subiendo…')).toBeNull())
    expect(within(uploads).getByText('nuevo.md').nextSibling).toHaveTextContent('Subido')
    expect(within(uploads).getByText('copia.md').nextSibling).toHaveTextContent('Ya existía')
    expect(within(uploads).getByText('grande.pdf').nextSibling).toHaveTextContent(
      'Supera el tamaño máximo',
    )
    expect(within(uploads).getByText('falla.html').nextSibling).toHaveTextContent(
      'No se pudo subir',
    )
    expect(await screen.findByRole('row', { name: /nuevo\.md/ })).toHaveTextContent('Pendiente')
    expect(api.mock.calls.filter(([, i]) => i?.method === 'POST')).toHaveLength(4)
  })

  it('rechaza en el navegador los tipos no admitidos y acepta arrastrar y soltar', async () => {
    const { api } = documentsApi([], {
      'POST /admin/documents': () =>
        jsonResponse([
          { filename: 'a.md', document: doc('a', 'a.md'), duplicate: false, error: null },
        ]),
    })
    renderApp('/admin/documents')
    const zone = await screen.findByRole('region', { name: /Arrastra aquí/ })

    fireEvent.dragOver(zone)
    fireEvent.dragLeave(zone)
    fireEvent.drop(zone, { dataTransfer: { files: [file('hoja.xls'), file('a.md')] } })

    const uploads = await screen.findByRole('region', { name: 'Subidas' })
    expect(await within(uploads).findByText(/^Tipo de fichero no admitido/)).toBeVisible()
    await waitFor(() =>
      expect(within(uploads).getByText('a.md').nextSibling).toHaveTextContent('Subido'),
    )
    expect(api.mock.calls.filter(([, i]) => i?.method === 'POST')).toHaveLength(1)
  })

  it('consulta el estado mientras hay documentos en proceso', async () => {
    let calls = 0
    documentsApi([], {
      'GET /admin/documents': () => {
        calls++
        return jsonResponse([doc('1', 'a.md', { status: calls < 2 ? 'processing' : 'ready' })])
      },
    })
    renderApp('/admin/documents')

    expect(await screen.findByText('Procesando…')).toBeVisible()
    expect(await screen.findByText('Indexado', {}, { timeout: 4000 })).toBeVisible()
  })

  it('elimina tras confirmar y reindexa', async () => {
    const { state, api } = documentsApi([doc('1', 'a.md'), doc('2', 'b.md')], {
      'DELETE /admin/documents/1': () => {
        state.docs = state.docs.filter((d) => d.id !== '1')
        return jsonResponse(null, 204)
      },
      'POST /admin/documents/2/reindex': () =>
        jsonResponse(doc('2', 'b.md', { status: 'pending' })),
      'POST /admin/documents/reindex': () => jsonResponse({ scheduled: 1 }),
    })
    vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValue(true)
    renderApp('/admin/documents')

    const remove = await screen.findByRole('button', { name: 'Eliminar: a.md' })
    await userEvent.click(remove)
    expect(screen.getByRole('row', { name: /a\.md/ })).toBeVisible()
    await userEvent.click(remove)
    await waitFor(() => expect(screen.queryByRole('row', { name: /a\.md/ })).toBeNull())

    await userEvent.click(screen.getByRole('button', { name: 'Reindexar: b.md' }))
    await userEvent.click(screen.getByRole('button', { name: 'Reindexar todo' }))
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith('/api/v1/admin/documents/reindex', expect.anything()),
    )
    expect(api).toHaveBeenCalledWith('/api/v1/admin/documents/2/reindex', expect.anything())
  })

  it('muestra errores de carga y de acciones', async () => {
    documentsApi([doc('1', 'a.md')], {
      'DELETE /admin/documents/1': () => jsonResponse({}, 500),
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    renderApp('/admin/documents')
    await userEvent.click(await screen.findByRole('button', { name: 'Eliminar: a.md' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('No se pudo completar la acción.')
  })

  it('muestra error si no se pueden cargar los documentos', async () => {
    documentsApi([], { 'GET /admin/documents': () => jsonResponse({}, 500) })
    renderApp('/admin/documents')
    expect(
      await screen.findByText('No se pudieron cargar los documentos.', {}, { timeout: 4000 }),
    ).toBeVisible()
  })

  it('se accede desde la navegación de administración', async () => {
    documentsApi([], {
      'GET /admin/prompt': () =>
        jsonResponse({ content: 'x', version_id: null, updated_at: null, is_default: true }),
      'GET /admin/prompt/versions': () => jsonResponse([]),
    })
    renderApp('/admin/prompt')
    await userEvent.click(await screen.findByRole('link', { name: 'Documentos' }))
    expect(await screen.findByRole('heading', { name: 'Documentos' })).toBeVisible()
  })
})
