import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { ALL_DOCUMENTS, type SearchScope } from '../api/client'
import { createQueryClient } from '../queryClient'
import { jsonResponse, mockApi } from '../test/utils'
import { ScopePicker } from './ScopePicker'

const indexed = (id: string, filename: string, patch: object = {}) => ({
  id,
  filename,
  title: filename,
  kind: filename.endsWith('.md') ? 'markdown' : 'pdf',
  category: null,
  ...patch,
})

const DOCUMENTS = [
  indexed('d2', 'NOR-013 Control de acceso.pdf'),
  indexed('d1', 'Guía de vacaciones.md'),
  ...[3, 4, 5, 6, 7].map((n) => indexed(`d${n}`, `Anexo ${n}.pdf`)),
]

function Harness({ initial = [] as string[], categories = [] as number[] }) {
  const [selected, setSelected] = useState<SearchScope>({
    documentIds: initial,
    categoryIds: categories,
  })
  return (
    <>
      <ScopePicker selected={selected} onChange={setSelected} />
      <output aria-label="elegidos">{selected.documentIds.join(',')}</output>
      <output aria-label="categorías elegidas">{selected.categoryIds.join(',')}</output>
    </>
  )
}

function renderPicker(
  initial: string[] = [],
  documents: object[] = DOCUMENTS,
  categories: number[] = [],
) {
  mockApi({ 'GET /documents': () => jsonResponse(documents) })
  const queryClient = createQueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <Harness initial={initial} categories={categories} />
    </QueryClientProvider>,
  )
}

const button = () => screen.getByRole('button', { name: /Buscar en:/ })

describe('ScopePicker', () => {
  it('elige documentos, los muestra y permite quitarlos', async () => {
    renderPicker()
    expect(button()).toHaveTextContent('Buscar en: Todos los documentos')

    await userEvent.click(button())
    const dialog = screen.getByRole('dialog', { name: 'Elegir en qué documentos buscar' })
    // Ordenados por nombre.
    const boxes = await screen.findAllByRole('checkbox')
    expect(boxes.map((b) => b.closest('label')!.textContent)).toEqual([
      'Anexo 3.pdf',
      'Anexo 4.pdf',
      'Anexo 5.pdf',
      'Anexo 6.pdf',
      'Anexo 7.pdf',
      'Guía de vacaciones.md',
      'NOR-013 Control de acceso.pdf',
    ])
    await userEvent.type(screen.getByLabelText('Filtrar documentos'), 'nor')
    await userEvent.click(screen.getByLabelText('NOR-013 Control de acceso.pdf'))
    expect(screen.getByLabelText('elegidos')).toHaveTextContent('d2')
    expect(button()).toHaveTextContent('Buscar en: 1 documento')

    await userEvent.keyboard('{Escape}')
    expect(dialog).not.toBeInTheDocument()
    expect(button()).toHaveFocus()
    const chips = screen.getByRole('list', { name: 'Se busca solo en' })
    expect(chips).toHaveTextContent('NOR-013 Control de acceso.pdf')

    await userEvent.click(
      screen.getByRole('button', { name: 'Buscar también fuera de NOR-013 Control de acceso.pdf' }),
    )
    expect(screen.getByLabelText('elegidos')).toHaveTextContent('')
    expect(screen.queryByRole('list', { name: 'Se busca solo en' })).not.toBeInTheDocument()
  })

  it('muestra el título con la categoría y el fichero, y busca en los tres', async () => {
    renderPicker(
      [],
      [
        indexed('d1', 'nor13.pdf', {
          title: 'Normativa de acceso',
          category: 'Seguridad',
          category_id: 2,
        }),
        indexed('d2', 'dietas.md', { title: 'Gastos de viaje' }),
        indexed('d3', 'Guía.md', { category: 'Recursos humanos', category_id: 1 }),
      ],
    )
    await userEvent.click(button())

    await screen.findAllByRole('checkbox')
    const group = (name: string) => screen.getByRole('group', { name })
    const labels = (name: string) =>
      within(group(name))
        .getAllByRole('checkbox')
        .map((b) => b.closest('label')!.textContent)
    // Por título.
    expect(labels('Documentos')).toEqual([
      'Gastos de viajedietas.md',
      'Guía.mdRecursos humanos',
      'Normativa de accesoSeguridad · nor13.pdf',
    ])
    expect(labels('Categorías')).toEqual(['Recursos humanos1 documento', 'Seguridad1 documento'])
    const filter = screen.getByLabelText('Filtrar documentos')
    for (const text of ['NOR13', 'normativa']) {
      await userEvent.clear(filter)
      await userEvent.type(filter, text)
      expect(screen.getAllByRole('checkbox')).toHaveLength(1)
    }
    await userEvent.click(screen.getByRole('checkbox'))
    await userEvent.keyboard('{Escape}')
    expect(screen.getByRole('list', { name: 'Se busca solo en' })).toHaveTextContent(
      'Normativa de acceso',
    )
  })

  it('limita la selección a 5 documentos y permite volver a buscar en todos', async () => {
    renderPicker(['d3', 'd4', 'd5', 'd6', 'd7'])

    await userEvent.click(button())

    expect(await screen.findByLabelText('Guía de vacaciones.md')).toBeDisabled()
    expect(screen.getByLabelText('Anexo 3.pdf')).toBeChecked()
    expect(screen.getByText('Puedes elegir hasta 5 documentos.')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Buscar en todos' }))
    expect(screen.getByLabelText('Guía de vacaciones.md')).toBeEnabled()
    expect(button()).toHaveTextContent('Todos los documentos')
  })

  it('elige categorías enteras, sin límite, junto con documentos sueltos', async () => {
    const rrhh = (id: string, name: string) =>
      indexed(id, name, { category: 'Recursos humanos', category_id: 1 })
    renderPicker(
      ['d3', 'd4', 'd5', 'd6', 'd7'],
      [
        ...DOCUMENTS.slice(2),
        rrhh('d1', 'Guía de vacaciones.md'),
        rrhh('d8', 'Dietas.md'),
        indexed('d2', 'NOR-013.pdf'),
      ],
    )
    await userEvent.click(button())
    expect(
      await screen.findByText(
        'Puedes elegir hasta 5 documentos. Las categorías no tienen límite: se busca en todos sus documentos.',
      ),
    ).toBeInTheDocument()
    expect(screen.getByLabelText('NOR-013.pdf')).toBeDisabled()

    // Aunque ya haya 5 documentos sueltos.
    await userEvent.type(screen.getByLabelText('Filtrar documentos'), 'recursos')
    await userEvent.click(screen.getByRole('checkbox', { name: /^Recursos humanos/ }))
    expect(screen.getByLabelText('categorías elegidas')).toHaveTextContent('1')
    // Sus documentos se incluyen.
    expect(screen.getByLabelText(/Dietas\.md/)).toBeChecked()
    expect(screen.getByLabelText(/Dietas\.md/)).toBeDisabled()
    expect(button()).toHaveTextContent('Buscar en: 1 categoría y 5 documentos')

    await userEvent.keyboard('{Escape}')
    const chips = screen.getByRole('list', { name: 'Se busca solo en' })
    expect(within(chips).getAllByRole('listitem')[0]).toHaveTextContent('Recursos humanos')
    await userEvent.click(
      screen.getByRole('button', { name: 'Buscar también fuera de Recursos humanos' }),
    )
    expect(screen.getByLabelText('categorías elegidas')).toHaveTextContent('')
    expect(button()).toHaveTextContent('Buscar en: 5 documentos')
  })

  it('se cierra al pulsar fuera e informa si no hay coincidencias o falla la carga', async () => {
    renderPicker()
    await userEvent.click(button())
    await userEvent.type(await screen.findByLabelText('Filtrar documentos'), 'zzz')
    expect(await screen.findByText('No hay documentos que coincidan.')).toBeInTheDocument()

    await userEvent.click(document.body)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()

    mockApi({ 'GET /documents': () => jsonResponse({ detail: 'error' }, 500) })
    const queryClient = createQueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={queryClient}>
        <ScopePicker selected={ALL_DOCUMENTS} onChange={() => {}} />
      </QueryClientProvider>,
    )
    await userEvent.click(screen.getAllByRole('button', { name: /Buscar en:/ })[1])
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'No se pudo cargar la lista de documentos.',
    )
  })
})
