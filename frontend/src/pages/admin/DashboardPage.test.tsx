import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type { AdminSession } from '../../api/admin'
import type { DashboardSummary, LoggedQuestion, QuestionPage } from '../../api/dashboard'
import { DailyChart } from '../../components/DailyChart'
import { t } from '../../i18n/es'
import { health, jsonResponse, mockApi, renderApp } from '../../test/utils'
import {
  formatLocalDateTime,
  formatNumber,
  formatPercent,
  formatSeconds,
  isoDate,
  matchingPreset,
  niceTicks,
  plainText,
  presetRange,
} from '../../utils/dashboard'

const admin: AdminSession = { authenticated: true, username: 'admin', enabled: true }

function summary(overrides: Partial<DashboardSummary> = {}): DashboardSummary {
  return {
    start: '2026-02-10',
    end: '2026-02-12',
    timezone: 'Europe/Madrid',
    sessions: 3,
    users: 2,
    questions: 12345,
    answered: 8,
    answered_ratio: 0.8,
    no_info: 1,
    errors: 1,
    without_sources: 2,
    trimmed: 1,
    retrieval_failed: 0,
    avg_response_ms: 1500,
    median_response_ms: 1200,
    avg_first_token_ms: 300,
    days: [
      { day: '2026-02-10', sessions: 2, users: 2, questions: 6, retrieval_failed: 0 },
      { day: '2026-02-11', sessions: 1, users: 1, questions: 4, retrieval_failed: 0 },
      { day: '2026-02-12', sessions: 0, users: 0, questions: 0, retrieval_failed: 0 },
    ],
    ...overrides,
  }
}

function question(id: number, overrides: Partial<LoggedQuestion> = {}): LoggedQuestion {
  return {
    id,
    asked_at: '2026-02-11T09:05:00+01:00',
    question: `Pregunta ${id}`,
    answer: `Respuesta ${id}`,
    result: 'answered',
    response_ms: 2300,
    first_token_ms: 400,
    sources_cited: 1,
    ...overrides,
  }
}

function page(total: number, pageNumber = 1, size = 25): QuestionPage {
  const start = (pageNumber - 1) * size
  const items = Array.from({ length: Math.max(0, Math.min(size, total - start)) }, (_, i) =>
    question(total - start - i),
  )
  return { total, page: pageNumber, size, items }
}

function api(overrides: Parameters<typeof mockApi>[0] = {}) {
  return mockApi({
    'GET /health': health,
    'GET /appearance': () => jsonResponse({}, 500),
    'GET /admin/session': () => jsonResponse(admin),
    'GET /admin/dashboard/summary*': () => jsonResponse(summary()),
    'GET /admin/dashboard/questions*': (_, url) => {
      const params = new URL(url, 'http://test').searchParams
      return jsonResponse(page(60, Number(params.get('page')), Number(params.get('size'))))
    },
    'GET /admin/dashboard/no-info-phrases': () =>
      jsonResponse({ phrases: ['no tengo información'], defaults: ['a', 'b'] }),
    ...overrides,
  })
}

const calls = (fetchMock: ReturnType<typeof api>, prefix: string) =>
  fetchMock.mock.calls.map(([url]) => String(url)).filter((url) => url.includes(prefix))

describe('utilidades del cuadro de mando', () => {
  it('rangos predefinidos', () => {
    const today = new Date(2026, 1, 15) // 15/02/2026
    expect(presetRange('today', today)).toEqual({ from: '2026-02-15', to: '2026-02-15' })
    expect(presetRange('last7', today)).toEqual({ from: '2026-02-09', to: '2026-02-15' })
    expect(presetRange('last30', today)).toEqual({ from: '2026-01-17', to: '2026-02-15' })
    expect(presetRange('month', today)).toEqual({ from: '2026-02-01', to: '2026-02-15' })
    expect(matchingPreset({ from: '2026-02-09', to: '2026-02-15' }, today)).toBe('last7')
    expect(matchingPreset({ from: '2026-02-02', to: '2026-02-15' }, today)).toBeNull()
    expect(isoDate(new Date(2026, 0, 5))).toBe('2026-01-05')
  })

  it('formatos del manual de estilos', () => {
    expect(formatNumber(1234)).toBe('1234')
    expect(formatNumber(12450)).toBe('12.450')
    expect(formatPercent(0.8)).toMatch(/^80\s%$/)
    expect(formatPercent(null)).toBe('—')
    expect(formatSeconds(1500)).toBe('1,5 s')
    expect(formatSeconds(null)).toBe('—')
    expect(formatLocalDateTime('2026-02-11T09:05:00+01:00')).toBe('11/02/2026 09:05')
    expect(formatLocalDateTime('raro')).toBe('raro')
  })

  it('texto plano de las respuestas y singular o plural', () => {
    expect(plainText('**23 días** de [vacaciones](http://x) `ya`\n\n# Título')).toBe(
      '23 días de vacaciones ya Título',
    )
    expect(plainText('```\ncódigo\n```fin')).toBe('fin')
    expect(t.dashboard.unit('sessions', 1)).toBe('sesión')
    expect(t.dashboard.unit('sessions', 2)).toBe('sesiones')
    expect(t.dashboard.unit('users', 1)).toBe('usuario')
    expect(t.dashboard.unit('questions', 0)).toBe('preguntas')
  })

  it('la gráfica no etiqueta las series que acaban juntas', () => {
    const { container } = render(
      <DailyChart
        days={[
          { day: '2026-02-10', sessions: 1, users: 1, questions: 2, retrieval_failed: 0 },
          { day: '2026-02-11', sessions: 1, users: 1, questions: 30, retrieval_failed: 0 },
        ]}
      />,
    )
    // Sesiones y usuarios terminan en el mismo punto: solo se etiqueta «preguntas».
    const labels = [...container.querySelectorAll('svg text')].map((n) => n.textContent)
    expect(labels).toContain('30 preguntas')
    expect(labels.some((l) => l?.includes('sesión') || l?.includes('usuario'))).toBe(false)
  })

  it('marcas del eje en números redondos', () => {
    expect(niceTicks(7)).toEqual([0, 2, 4, 6, 8])
    expect(niceTicks(0)).toEqual([0, 1])
    expect(niceTicks(950)).toEqual([0, 500, 1000])
    expect(niceTicks(3)).toEqual([0, 1, 2, 3])
  })
})

describe('DashboardPage', () => {
  it('es la página de entrada de la administración y muestra las cifras', async () => {
    api()
    renderApp('/admin')

    expect(await screen.findByRole('heading', { level: 1, name: 'Cuadro de mando' })).toBeVisible()
    await waitFor(() => expect(document.title).toBe('Cuadro de mando – Chatbot RAG'))
    const figures = await screen.findByRole('region', { name: /Cifras del 10\/02\/2026/ })
    const value = (label: string) =>
      within(figures).getByText(label).parentElement!.querySelector('dd')!.textContent
    expect(value('Sesiones')).toBe('3')
    expect(value('Usuarios')).toBe('2')
    expect(value('Preguntas')).toBe('12.345')
    expect(value('Tiempo medio de respuesta')).toBe('1,5 s')
    expect(within(figures).getByText('Mediana 1,2 s · primer fragmento 0,3 s')).toBeVisible()
    expect(value('Respondidas')).toMatch(/^80\s%$/)
    expect(
      within(figures).getByText(
        /1 sin información · 1 con error · 2 sin documentación encontrada · 1 con el prompt recortado/,
      ),
    ).toBeVisible()
    // Sin fallos de la búsqueda no hay aviso.
    expect(screen.queryByRole('note')).toBeNull()
  })

  it('avisa de las preguntas sin documentación por un fallo de la búsqueda', async () => {
    api({
      'GET /admin/dashboard/summary*': () =>
        jsonResponse(
          summary({
            retrieval_failed: 3,
            days: [{ day: '2026-02-10', sessions: 2, users: 2, questions: 6, retrieval_failed: 3 }],
          }),
        ),
    })
    renderApp('/admin/dashboard?from=2026-02-10&to=2026-02-12')

    expect(await screen.findByRole('note')).toHaveTextContent(
      'En 3 preguntas no se pudo consultar la documentación',
    )
    await userEvent.click(screen.getByRole('button', { name: 'Ver como tabla' }))
    const table = screen.getByRole('table', { name: 'Actividad diaria por día' })
    expect(
      within(table).getByRole('columnheader', { name: 'Sin documentación por un fallo' }),
    ).toBeInTheDocument()
    expect(within(table).getAllByRole('row')[1]).toHaveTextContent('10/02/20266223')
  })

  it('los atajos y el rango a medida cambian las cifras', async () => {
    const fetchMock = api()
    renderApp('/admin/dashboard')
    await screen.findByRole('region', { name: /Cifras/ })
    const last30 = screen.getByRole('button', { name: 'Últimos 30 días' })
    expect(last30).toHaveAttribute('aria-pressed', 'true')

    await userEvent.click(screen.getByRole('button', { name: 'Últimos 7 días' }))
    const week = presetRange('last7')
    await waitFor(() =>
      expect(calls(fetchMock, '/summary')).toContain(
        `/api/v1/admin/dashboard/summary?from=${week.from}&to=${week.to}`,
      ),
    )
    expect(screen.getByRole('button', { name: 'Últimos 7 días' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )

    fireEvent.change(screen.getByLabelText('Desde'), { target: { value: '2026-01-01' } })
    fireEvent.change(screen.getByLabelText('Hasta'), { target: { value: '2026-01-31' } })
    await userEvent.click(screen.getByRole('button', { name: 'Aplicar rango' }))
    await waitFor(() =>
      expect(calls(fetchMock, '/summary')).toContain(
        '/api/v1/admin/dashboard/summary?from=2026-01-01&to=2026-01-31',
      ),
    )
    // Las descargas usan el mismo rango.
    expect(screen.getByRole('link', { name: 'Descargar CSV' })).toHaveAttribute(
      'href',
      '/api/v1/admin/dashboard/questions.csv?from=2026-01-01&to=2026-01-31',
    )
    expect(screen.getByRole('link', { name: 'Descargar Excel' })).toHaveAttribute(
      'href',
      '/api/v1/admin/dashboard/questions.xlsx?from=2026-01-01&to=2026-01-31',
    )
  })

  it('no aplica un rango con las fechas invertidas', async () => {
    const fetchMock = api()
    renderApp('/admin/dashboard?from=2026-02-10&to=2026-02-12')
    await screen.findByRole('region', { name: /Cifras/ })
    const before = calls(fetchMock, '/summary').length

    fireEvent.change(screen.getByLabelText('Desde'), { target: { value: '2026-03-01' } })
    expect(screen.getByRole('alert')).toHaveTextContent('anterior o igual')
    await userEvent.click(screen.getByRole('button', { name: 'Aplicar rango' }))
    expect(calls(fetchMock, '/summary')).toHaveLength(before)
  })

  it('gráfica con leyenda, recorrido con teclado y tabla equivalente', async () => {
    api()
    renderApp('/admin/dashboard?from=2026-02-10&to=2026-02-12')
    const chart = await screen.findByRole('img', { name: /Gráfica de preguntas/ })
    const legend = screen.getByRole('list', { name: 'Leyenda' })
    expect(
      within(legend)
        .getAllByRole('listitem')
        .map((li) => li.textContent),
    ).toEqual(['Preguntas', 'Sesiones', 'Usuarios'])

    chart.focus()
    await userEvent.keyboard('{ArrowRight}')
    const tooltip = await screen.findByRole('status')
    expect(tooltip).toHaveTextContent('10/02/2026')
    expect(tooltip).toHaveTextContent('6preguntas')
    await userEvent.keyboard('{End}')
    expect(screen.getByRole('status')).toHaveTextContent('12/02/2026')

    await userEvent.click(screen.getByRole('button', { name: 'Ver como tabla' }))
    const table = screen.getByRole('table', { name: 'Actividad diaria por día' })
    const rows = within(table)
      .getAllByRole('row')
      .map((r) => r.textContent)
    expect(rows[1]).toBe('10/02/20266220')
    expect(screen.getByRole('button', { name: 'Ver como gráfica' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
  })

  it('tabla de preguntas paginada con resultado en texto', async () => {
    const fetchMock = api({
      'GET /admin/dashboard/questions*': (_, url) => {
        const params = new URL(url, 'http://test').searchParams
        const data = page(60, Number(params.get('page')), Number(params.get('size')))
        data.items[0] = question(data.items[0]!.id, { result: 'no_info' })
        data.items[1] = question(data.items[1]!.id, { result: 'error', answer: null })
        return jsonResponse(data)
      },
    })
    renderApp('/admin/dashboard?from=2026-02-10&to=2026-02-12')
    const table = await screen.findByRole('table', { name: 'Preguntas y respuestas' })
    const rows = within(table).getAllByRole('row')
    expect(rows).toHaveLength(26)
    expect(rows[1]).toHaveTextContent('11/02/2026 09:05')
    expect(rows[1]).toHaveTextContent('Pregunta 60')
    expect(rows[1]).toHaveTextContent('Sin información')
    expect(rows[1]).toHaveTextContent('2,3 s')
    expect(rows[2]).toHaveTextContent('Error')
    expect(screen.getByText('Mostrando 1–25 de 60')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Anterior' })).toBeDisabled()

    await userEvent.click(screen.getByRole('button', { name: 'Siguiente' }))
    expect(await screen.findByText('Mostrando 26–50 de 60')).toBeVisible()
    await userEvent.selectOptions(screen.getByLabelText('Filas por página'), '50')
    expect(await screen.findByText('Mostrando 1–50 de 60')).toBeVisible()
    expect(calls(fetchMock, '/questions')).toContain(
      '/api/v1/admin/dashboard/questions?from=2026-02-10&to=2026-02-12&page=1&size=50',
    )
  })

  it('rango sin preguntas', async () => {
    api({
      'GET /admin/dashboard/summary*': () =>
        jsonResponse(summary({ questions: 0, answered_ratio: null, avg_response_ms: null })),
      'GET /admin/dashboard/questions*': () => jsonResponse(page(0)),
    })
    renderApp('/admin/dashboard')
    await waitFor(() =>
      expect(screen.getAllByText(/No hay preguntas en este rango/)).toHaveLength(2),
    )
    expect(screen.queryByRole('img', { name: /Gráfica/ })).toBeNull()
  })

  it('guarda las frases de «sin información» y recalcula', async () => {
    let saved: string[] = []
    const fetchMock = api({
      'PUT /admin/dashboard/no-info-phrases': (init) => {
        saved = (JSON.parse(init!.body as string) as { phrases: string[] }).phrases
        return jsonResponse({ phrases: saved.filter(Boolean), defaults: ['a', 'b'] })
      },
    })
    renderApp('/admin/dashboard')
    await userEvent.click(await screen.findByText('Detección de respuestas sin información'))
    const textarea = await screen.findByLabelText('Frases (una por línea)')
    await waitFor(() => expect(textarea).toHaveValue('no tengo información'))

    await userEvent.click(screen.getByRole('button', { name: 'Restaurar las frases por defecto' }))
    expect(textarea).toHaveValue('a\nb')
    await userEvent.type(textarea, '\nlo desconozco')
    const summariesBefore = calls(fetchMock, '/summary').length
    await userEvent.click(screen.getByRole('button', { name: 'Guardar frases' }))

    expect(await screen.findByText('Frases guardadas y preguntas reclasificadas.')).toBeVisible()
    expect(saved).toEqual(['a', 'b', 'lo desconozco'])
    await waitFor(() =>
      expect(calls(fetchMock, '/summary').length).toBeGreaterThan(summariesBefore),
    )
  })

  it('informa de errores', async () => {
    api({
      'GET /admin/dashboard/summary*': () => jsonResponse({}, 500),
    })
    renderApp('/admin/dashboard')
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'No se pudieron cargar las estadísticas.',
    )
  })

  it('frases no válidas', async () => {
    api({ 'PUT /admin/dashboard/no-info-phrases': () => jsonResponse({}, 422) })
    renderApp('/admin/dashboard')
    await userEvent.click(await screen.findByText('Detección de respuestas sin información'))
    await userEvent.click(await screen.findByRole('button', { name: 'Guardar frases' }))
    expect(await screen.findByText(/como máximo 50/)).toBeVisible()
  })
})
