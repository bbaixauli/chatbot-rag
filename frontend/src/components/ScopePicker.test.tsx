import { QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useState } from 'react'
import { describe, expect, it } from 'vitest'
import { createQueryClient } from '../queryClient'
import { jsonResponse, mockApi } from '../test/utils'
import { ScopePicker } from './ScopePicker'

const DOCUMENTS = [
  { id: 'd2', filename: 'NOR-013 Control de acceso.pdf', kind: 'pdf' },
  { id: 'd1', filename: 'Guía de vacaciones.md', kind: 'markdown' },
  ...[3, 4, 5, 6, 7].map((n) => ({ id: `d${n}`, filename: `Anexo ${n}.pdf`, kind: 'pdf' })),
]

function Harness({ initial = [] as string[] }) {
  const [selected, setSelected] = useState(initial)
  return (
    <>
      <ScopePicker selected={selected} onChange={setSelected} />
      <output aria-label="elegidos">{selected.join(',')}</output>
    </>
  )
}

function renderPicker(initial: string[] = []) {
  mockApi({ 'GET /documents': () => jsonResponse(DOCUMENTS) })
  const queryClient = createQueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={queryClient}>
      <Harness initial={initial} />
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
        <ScopePicker selected={[]} onChange={() => {}} />
      </QueryClientProvider>,
    )
    await userEvent.click(screen.getAllByRole('button', { name: /Buscar en:/ })[1])
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'No se pudo cargar la lista de documentos.',
    )
  })
})
