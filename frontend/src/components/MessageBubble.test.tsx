import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { Source } from '../api/client'
import { linkCitations, sourceLabel } from '../utils/citations'
import { MessageBubble } from './MessageBubble'

const source = (number: number, patch: Partial<Source> = {}): Source => ({
  number,
  document_id: `d${number}`,
  filename: 'manual.pdf',
  page: null,
  heading: null,
  snippet: 'Fragmento de ejemplo',
  cited: true,
  ...patch,
})

function renderBubble(content: string, sources: Source[]) {
  render(
    <ul>
      <MessageBubble role="assistant" content={content} sources={sources} />
    </ul>,
  )
}

describe('sourceLabel', () => {
  it('muestra página, sección o solo el fichero', () => {
    expect(sourceLabel(source(1, { page: 3 }))).toBe('manual.pdf · pág. 3')
    expect(sourceLabel(source(1, { heading: 'A > B' }))).toBe('manual.pdf — A > B')
    expect(sourceLabel(source(1))).toBe('manual.pdf')
  })

  it('usa el título del documento y no repite la sección si es el título', () => {
    const titled = { title: 'Manual del empleado' }
    expect(sourceLabel(source(1, { ...titled, page: 3 }))).toBe('Manual del empleado · pág. 3')
    expect(sourceLabel(source(1, { ...titled, heading: 'Altas' }))).toBe(
      'Manual del empleado — Altas',
    )
    expect(sourceLabel(source(1, { ...titled, heading: 'Manual del empleado' }))).toBe(
      'Manual del empleado',
    )
  })
})

describe('linkCitations', () => {
  it('enlaza solo las citas con documento disponible', () => {
    const sources = [source(1, { page: 2 }), source(2, { document_id: null })]
    expect(linkCitations('A [1], B [2], C [3] y [enlace](x).', sources)).toBe(
      'A [\\[1\\]](/api/v1/documents/d1/file#page=2 "manual.pdf · pág. 2"), B [2], C [3] y [enlace](x).',
    )
    expect(linkCitations('Sin fuentes [1]', [])).toBe('Sin fuentes [1]')
  })

  it('escapa comillas del título', () => {
    expect(linkCitations('[1]', [source(1, { filename: 'a"b.md' })])).toContain('"a\'b.md"')
  })
})

describe('MessageBubble con fuentes', () => {
  it('muestra las fuentes citadas con enlace al documento y la página', () => {
    renderBubble('Hay 23 días [1].', [
      source(1, { page: 4 }),
      source(2, { filename: 'guia.md', heading: 'Vacaciones' }),
    ])

    const list = screen.getByRole('region', { name: 'Fuentes' })
    const pdf = within(list).getByRole('link', { name: 'Abrir fuente: [1] manual.pdf · pág. 4' })
    expect(pdf).toHaveAttribute('href', '/api/v1/documents/d1/file#page=4')
    expect(pdf).toHaveAttribute('target', '_blank')
    expect(pdf).toHaveAttribute('rel', 'noopener noreferrer')
    expect(pdf).toHaveAttribute('title', 'Fragmento de ejemplo')
    expect(
      within(list).getByRole('link', { name: 'Abrir fuente: [2] guia.md — Vacaciones' }),
    ).toHaveAttribute('href', '/api/v1/documents/d2/file')

    const inline = screen.getByRole('link', { name: '[1]' })
    expect(inline).toHaveAttribute('href', '/api/v1/documents/d1/file#page=4')
    expect(inline).toHaveAttribute('target', '_blank')
  })

  it('titula «Fuentes consultadas» si el modelo no citó', () => {
    renderBubble('Respuesta sin citas.', [source(1, { cited: false })])
    expect(screen.getByRole('region', { name: 'Fuentes consultadas' })).toBeVisible()
  })

  it('muestra las fuentes de documentos eliminados sin enlace', () => {
    renderBubble('Texto [1].', [source(1, { document_id: null })])

    const list = screen.getByRole('region', { name: 'Fuentes' })
    expect(within(list).queryByRole('link')).toBeNull()
    expect(list).toHaveTextContent('[1] manual.pdf (documento eliminado)')
    expect(screen.queryByRole('link', { name: '[1]' })).toBeNull()
  })

  it('no muestra sección de fuentes sin fuentes ni en mensajes del usuario', () => {
    renderBubble('Hola', [])
    render(
      <ul>
        <MessageBubble role="user" content="Pregunta" sources={[source(1)]} />
      </ul>,
    )
    expect(screen.queryByRole('region')).toBeNull()
  })
})
