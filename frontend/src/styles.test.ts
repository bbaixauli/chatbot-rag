import { describe, expect, it } from 'vitest'

// Código fuente de los componentes (sin los tests).
const sources = import.meta.glob<string>(['./**/*.tsx', '!./**/*.test.tsx'], {
  query: '?raw',
  import: 'default',
  eager: true,
})

// Paleta por defecto de Tailwind: está desactivada en index.css, así que estas clases no
// producirían ningún estilo. Los componentes deben usar los tokens del manual de estilos.
const PALETTE = [
  'slate',
  'gray',
  'zinc',
  'neutral',
  'stone',
  'red',
  'orange',
  'amber',
  'yellow',
  'lime',
  'green',
  'emerald',
  'teal',
  'cyan',
  'sky',
  'blue',
  'indigo',
  'violet',
  'purple',
  'fuchsia',
  'pink',
  'rose',
  'white',
  'black',
]
const LITERAL_COLOR = new RegExp(
  `\\b(?:bg|text|border|ring|outline|divide|fill|stroke|accent|from|to|via|decoration)-(?:${PALETTE.join('|')})(?:-\\d{2,3})?\\b`,
  'g',
)

describe('estilos', () => {
  it('los componentes usan los tokens de color, nunca colores literales', () => {
    expect(Object.keys(sources).length).toBeGreaterThan(10)
    const found = Object.entries(sources).flatMap(([file, code]) =>
      [...code.matchAll(LITERAL_COLOR)].map((m) => `${file}: ${m[0]}`),
    )
    expect(found).toEqual([])
  })

  it('el foco nunca se oculta sin sustituto', () => {
    const found = Object.entries(sources)
      .filter(([, code]) => /\b(?:focus:)?outline-none\b/.test(code))
      .map(([file]) => file)
    expect(found).toEqual([])
  })
})
