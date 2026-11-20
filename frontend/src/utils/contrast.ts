/** Contraste WCAG 2.1 (luminancia relativa), igual que el servidor. */
function luminance(color: string) {
  const channels = [1, 3, 5].map((i) => parseInt(color.slice(i, i + 2), 16) / 255)
  const [r, g, b] = channels.map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

export const isHexColor = (value: string) => /^#[0-9a-fA-F]{6}$/.test(value)

export function contrastRatio(foreground: string, background: string) {
  const [lighter, darker] = [luminance(foreground), luminance(background)].sort((a, b) => b - a)
  return (lighter + 0.05) / (darker + 0.05)
}

export interface ContrastCheck {
  foreground: string
  background: string
  ratio: number
  required: number
}

/**
 * Pares de colores que se usan juntos y su mínimo (texto 4,5:1; controles y foco 3:1).
 * Debe coincidir con `contrast_warnings` del servidor.
 */
export function contrastWarnings(
  colors: Record<string, string>,
  headerStyle: 'clara' | 'marca',
): ContrastCheck[] {
  const c = colors
  const header = headerStyle === 'marca' ? [c.marca, c['sobre-marca']] : [c.fondo, c.texto]
  const pairs: [string, string, string, string, number][] = [
    ['texto', 'fondo', c.texto, c.fondo, 4.5],
    ['texto', 'fondo-alt', c.texto, c['fondo-alt'], 4.5],
    ['texto', 'marca-suave', c.texto, c['marca-suave'], 4.5],
    ['texto-secundario', 'fondo', c['texto-secundario'], c.fondo, 4.5],
    ['texto-secundario', 'fondo-alt', c['texto-secundario'], c['fondo-alt'], 4.5],
    ['enlace', 'fondo', c.enlace, c.fondo, 4.5],
    ['marca', 'fondo', c.marca, c.fondo, 4.5],
    ['sobre-marca', 'marca', c['sobre-marca'], c.marca, 4.5],
    ['sobre-marca', 'marca-oscuro', c['sobre-marca'], c['marca-oscuro'], 4.5],
    ['cabecera (texto)', 'cabecera (fondo)', header[1], header[0], 4.5],
    ['borde-control', 'fondo', c['borde-control'], c.fondo, 3],
    ['foco', 'fondo', c.foco, c.fondo, 3],
    ['fondo', 'error', c.fondo, c.error, 4.5],
  ]
  for (const state of ['error', 'exito', 'aviso', 'info']) {
    pairs.push([state, 'fondo', c[state], c.fondo, 4.5])
    pairs.push([state, `${state}-suave`, c[state], c[`${state}-suave`], 4.5])
  }
  return pairs
    .filter(([, , fg, bg]) => isHexColor(fg) && isHexColor(bg))
    .map(([foreground, background, fg, bg, required]) => ({
      foreground,
      background,
      ratio: Math.round(contrastRatio(fg, bg) * 100) / 100,
      required,
    }))
    .filter((check) => check.ratio < check.required)
}
