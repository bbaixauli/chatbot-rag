import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import type { AdminSession } from '../../api/admin'
import type { AdminAppearance, AppearanceInput, PublicAppearance } from '../../api/appearance'
import { health, jsonResponse, mockApi, renderApp } from '../../test/utils'

const admin: AdminSession = { authenticated: true, username: 'admin', enabled: true }

const COLORS: Record<string, string> = {
  marca: '#2563eb',
  'marca-oscuro': '#1e40af',
  'sobre-marca': '#ffffff',
  'marca-suave': '#eff6ff',
  enlace: '#1d4ed8',
  texto: '#111827',
  'texto-secundario': '#4b5563',
  'borde-control': '#6b7280',
  borde: '#e5e7eb',
  fondo: '#ffffff',
  'fondo-alt': '#f9fafb',
  foco: '#005fcc',
  error: '#b91c1c',
  'error-suave': '#fef2f2',
  exito: '#047857',
  'exito-suave': '#ecfdf5',
  aviso: '#92400e',
  'aviso-suave': '#fffbeb',
  info: '#1d4ed8',
  'info-suave': '#eff6ff',
}

function adminAppearance(overrides: Partial<AdminAppearance> = {}): AdminAppearance {
  return {
    app_name: 'Chatbot RAG',
    organization: '',
    header_style: 'clara',
    colors: { ...COLORS },
    font_sans: 'sistema',
    font_mono: 'sistema',
    logo_alt: '',
    footer: { unit: '', accessibility_url: '', support_text: '', support_url: '' },
    login_notice: '',
    ai_notice: '',
    logo_url: null,
    warnings: [],
    sans_fonts: { sistema: 'system-ui, sans-serif', trebuchet: "'Trebuchet MS', sans-serif" },
    mono_fonts: { sistema: 'monospace', consolas: 'Consolas, monospace' },
    ...overrides,
  }
}

function api(overrides: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    'GET /health': health,
    'GET /admin/session': () => jsonResponse(admin),
    'GET /appearance': () => jsonResponse({}, 500),
    'GET /admin/appearance': () => jsonResponse(adminAppearance()),
    ...overrides,
  })
}

const colorInput = (name: string) => screen.getByRole('textbox', { name })

describe('AppearancePage', () => {
  it('se accede desde la navegación y muestra la configuración actual', async () => {
    api()
    renderApp('/admin/prompt')
    await userEvent.click(await screen.findByRole('link', { name: 'Apariencia' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'Apariencia' })).toBeVisible()
    expect(screen.getByLabelText('Nombre de la aplicación')).toHaveValue('Chatbot RAG')
    expect(colorInput('Color de marca')).toHaveValue('#2563eb')
    expect(screen.getByRole('radio', { name: /Clara/ })).toBeChecked()
    expect(screen.getByText('Todos los contrastes cumplen WCAG 2.1 AA.')).toBeVisible()
    expect(screen.getByText(/No hay logo/)).toBeVisible()
    expect(document.title).toBe('Apariencia – Chatbot RAG')
  })

  it('avisa en directo de los contrastes insuficientes y permite guardar igualmente', async () => {
    let sent: AppearanceInput | null = null
    api({
      'PUT /admin/appearance': (init) => {
        sent = JSON.parse(init!.body as string) as AppearanceInput
        return jsonResponse(
          adminAppearance({
            ...sent,
            warnings: [
              {
                foreground: 'texto-secundario',
                background: 'fondo',
                ratio: 2.54,
                required: 4.5,
                usage: 'Texto',
              },
            ],
          }),
        )
      },
    })
    renderApp('/admin/appearance')
    const secondary = await screen.findByRole('textbox', { name: 'Texto secundario' })

    await userEvent.clear(secondary)
    await userEvent.type(secondary, '#9ca3af')

    expect(screen.getByText(/combinaciones? con contraste insuficiente/)).toBeVisible()
    expect(screen.getByText(/Texto secundario sobre Fondo: 2,54:1 \(mínimo 4,5:1\)/)).toBeVisible()

    await userEvent.click(screen.getByRole('button', { name: 'Guardar apariencia' }))
    expect(await screen.findByText('Apariencia guardada con 1 aviso de contraste.')).toBeVisible()
    expect(sent!.colors['texto-secundario']).toBe('#9ca3af')
  })

  it('guarda identidad, cabecera, tipos de letra y textos', async () => {
    let sent: AppearanceInput | null = null
    api({
      'PUT /admin/appearance': (init) => {
        sent = JSON.parse(init!.body as string) as AppearanceInput
        return jsonResponse(adminAppearance(sent))
      },
    })
    renderApp('/admin/appearance')
    await userEvent.clear(await screen.findByLabelText('Nombre de la aplicación'))
    await userEvent.type(screen.getByLabelText('Nombre de la aplicación'), 'Asistente')
    await userEvent.type(screen.getByLabelText('Organización'), 'Ayuntamiento de Ejemplo')
    await userEvent.click(screen.getByRole('radio', { name: /color de marca/ }))
    await userEvent.selectOptions(screen.getByLabelText('Tipo de letra principal'), 'Trebuchet MS')
    await userEvent.type(screen.getByLabelText('Unidad responsable'), 'Informática')
    await userEvent.type(
      screen.getByLabelText('Aviso en las pantallas de acceso'),
      'Uso exclusivo municipal.',
    )
    await userEvent.click(screen.getByRole('button', { name: 'Guardar apariencia' }))

    expect(await screen.findByText('Apariencia guardada.')).toBeVisible()
    expect(sent).toMatchObject({
      app_name: 'Asistente',
      organization: 'Ayuntamiento de Ejemplo',
      header_style: 'marca',
      font_sans: 'trebuchet',
      footer: { unit: 'Informática' },
      login_notice: 'Uso exclusivo municipal.',
    })
  })

  it('no envía colores con formato incorrecto', async () => {
    const fetchMock = api()
    renderApp('/admin/appearance')
    const brand = await screen.findByRole('textbox', { name: 'Color de marca' })
    await userEvent.clear(brand)
    await userEvent.type(brand, 'rojo')

    expect(brand).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText(/Usa el formato #RRGGBB/)).toBeVisible()
    await userEvent.click(screen.getByRole('button', { name: 'Guardar apariencia' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Corrige el formato de estos colores: Color de marca.',
    )
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(false)
  })

  it('el selector de color cambia el valor', async () => {
    api()
    renderApp('/admin/appearance')
    const picker = await screen.findByLabelText('Selector de color: Enlaces')
    // userEvent no simula el diálogo nativo del selector: se cambia su valor directamente.
    const { fireEvent } = await import('@testing-library/react')
    fireEvent.change(picker, { target: { value: '#7a0038' } })
    expect(colorInput('Enlaces')).toHaveValue('#7a0038')
  })

  it('restablece los valores por defecto tras confirmar', async () => {
    const confirm = vi.spyOn(window, 'confirm')
    api({
      'GET /admin/appearance': () =>
        jsonResponse(adminAppearance({ app_name: 'Personalizada', colors: { ...COLORS } })),
      'DELETE /admin/appearance': () => jsonResponse(adminAppearance()),
    })
    renderApp('/admin/appearance')
    expect(await screen.findByLabelText('Nombre de la aplicación')).toHaveValue('Personalizada')

    confirm.mockReturnValueOnce(false)
    await userEvent.click(screen.getByRole('button', { name: 'Restablecer valores por defecto' }))
    expect(screen.getByLabelText('Nombre de la aplicación')).toHaveValue('Personalizada')

    confirm.mockReturnValueOnce(true)
    await userEvent.click(screen.getByRole('button', { name: 'Restablecer valores por defecto' }))
    await waitFor(() =>
      expect(screen.getByLabelText('Nombre de la aplicación')).toHaveValue('Chatbot RAG'),
    )
  })

  it('sube, rechaza y quita el logo', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    let logo: string | null = null
    let reject = true
    api({
      'GET /admin/appearance': () => jsonResponse(adminAppearance({ logo_url: logo })),
      'PUT /admin/appearance/logo': () => {
        if (reject) return jsonResponse({ detail: 'Formato no admitido: usa SVG, PNG o WebP' }, 422)
        logo = '/api/v1/appearance/logo?v=abc'
        return jsonResponse(adminAppearance({ logo_url: logo }))
      },
      'DELETE /admin/appearance/logo': () => {
        logo = null
        return jsonResponse(adminAppearance())
      },
    })
    renderApp('/admin/appearance')
    const input = await screen.findByLabelText('Subir un logo nuevo')

    await userEvent.upload(input, new File(['GIF'], 'logo.gif', { type: 'image/gif' }), {
      applyAccept: false,
    })
    expect(await screen.findByRole('alert')).toHaveTextContent('Formato no admitido')

    reject = false
    await userEvent.upload(input, new File(['<svg/>'], 'logo.svg', { type: 'image/svg+xml' }))
    expect(await screen.findByText('Logo actualizado.')).toBeVisible()
    expect(screen.getByRole('img', { name: 'Logo actual' })).toHaveAttribute(
      'src',
      '/api/v1/appearance/logo?v=abc',
    )

    await userEvent.click(screen.getByRole('button', { name: 'Quitar el logo' }))
    expect(await screen.findByText(/No hay logo/)).toBeVisible()
  })

  it('informa si no se puede cargar', async () => {
    api({ 'GET /admin/appearance': () => jsonResponse({}, 500) })
    renderApp('/admin/appearance')
    expect(await screen.findByRole('alert')).toHaveTextContent('No se pudo cargar la apariencia.')
  })
})

describe('Apariencia aplicada', () => {
  const configured: PublicAppearance = {
    app_name: 'Asistente municipal',
    organization: 'Ayuntamiento de Ejemplo',
    header_style: 'marca',
    logo_url: '/api/v1/appearance/logo?v=1',
    logo_alt: 'Escudo del Ayuntamiento',
    footer: {
      unit: 'Servicio de Informática',
      accessibility_url: 'https://ejemplo.org/accesibilidad',
      support_text: 'Soporte 010',
      support_url: 'https://ejemplo.org/soporte',
    },
    login_notice: 'Uso exclusivo para fines municipales.',
    ai_notice: 'Respuestas generadas por IA.',
  }

  it('cabecera, pie, título y aviso de IA usan la configuración', async () => {
    mockApi({
      'GET /health': health,
      'GET /appearance': () => jsonResponse(configured),
      'GET /conversations': () => jsonResponse([]),
      'GET /chat/config': () => jsonResponse({ welcome_message: null, suggested_questions: [] }),
    })
    renderApp()

    const banner = screen.getByRole('banner')
    expect(
      await within(banner).findByRole('img', { name: 'Escudo del Ayuntamiento' }),
    ).toBeVisible()
    expect(within(banner).getByText('Asistente municipal')).toBeVisible()
    const footer = screen.getByRole('contentinfo')
    expect(within(footer).getByText('Servicio de Informática')).toBeVisible()
    expect(
      within(footer).getByRole('link', { name: 'Declaración de accesibilidad' }),
    ).toHaveAttribute('href', 'https://ejemplo.org/accesibilidad')
    expect(within(footer).getByRole('link', { name: 'Soporte 010' })).toBeVisible()
    expect(screen.getByText('Respuestas generadas por IA.')).toBeVisible()
    await waitFor(() =>
      expect(document.title).toBe(
        'Nueva conversación – Asistente municipal – Ayuntamiento de Ejemplo',
      ),
    )
  })

  it('las pantallas de acceso muestran el logo y el aviso de uso', async () => {
    mockApi({
      'GET /health': health,
      'GET /appearance': () => jsonResponse(configured),
      'GET /admin/session': () =>
        jsonResponse({ authenticated: false, username: null, enabled: true }),
    })
    renderApp('/admin/login')

    expect(await screen.findByText('Uso exclusivo para fines municipales.')).toBeVisible()
    expect(screen.getByRole('img', { name: 'Escudo del Ayuntamiento' })).toBeVisible()
    expect(screen.getByText('Asistente municipal')).toBeVisible()
  })

  it('soporte sin enlace se muestra como texto', async () => {
    mockApi({
      'GET /health': health,
      'GET /appearance': () =>
        jsonResponse({
          ...configured,
          logo_url: null,
          footer: { ...configured.footer, support_url: '', accessibility_url: '' },
        }),
      'GET /conversations': () => jsonResponse([]),
      'GET /chat/config': () => jsonResponse({ welcome_message: null, suggested_questions: [] }),
    })
    renderApp()
    const footer = await screen.findByRole('contentinfo')
    expect(await within(footer).findByText('Soporte 010')).toBeVisible()
    expect(within(footer).queryByRole('link')).toBeNull()
    expect(within(screen.getByRole('banner')).queryByRole('img')).toBeNull()
  })
})
