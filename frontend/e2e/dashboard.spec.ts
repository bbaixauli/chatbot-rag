import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { ADMIN_PASSWORD } from './constants.ts'

// Comparte la base de datos con el resto: las cifras crecen con otros tests, así que se
// comprueba la pregunta propia en la tabla y que las cifras no estén vacías.

const unique = (label: string) => `${label} ${Date.now()}-${Math.floor(Math.random() * 1e4)}`

async function ask(page: Page, question: string) {
  await page.goto('/')
  await page.getByLabel('Escribe tu mensaje').fill(question)
  await page.getByLabel('Escribe tu mensaje').press('Enter')
  await expect(page.locator('li[aria-busy="true"]')).toHaveCount(0)
  await expect(page.locator('li[data-role="assistant"]').last()).toContainText(question)
}

async function openDashboard(page: Page) {
  await page.goto('/admin/login')
  await page.getByLabel('Usuario').fill('admin')
  await page.getByLabel('Contraseña').fill(ADMIN_PASSWORD)
  await page.getByRole('button', { name: 'Entrar' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Cuadro de mando' })).toBeVisible()
  await page.getByRole('button', { name: 'Hoy' }).click()
  await expect(page.getByRole('button', { name: 'Hoy' })).toHaveAttribute('aria-pressed', 'true')
  await waitUntilLoaded(page)
}

/** Mientras se recargan, los datos anteriores se muestran atenuados (`aria-busy`). */
async function waitUntilLoaded(page: Page) {
  await expect(page.getByRole('table', { name: 'Preguntas y respuestas' })).toBeVisible()
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
}

async function expectAccessible(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  const serious = results.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)
  expect(serious).toEqual([])
}

test('la pregunta aparece en el cuadro de mando con su tiempo y se exporta', async ({ page }) => {
  const question = unique('Pregunta para el cuadro de mando')
  await ask(page, question)
  await openDashboard(page)

  const figures = page.getByRole('region', { name: /Cifras del/ })
  await expect(figures).toBeVisible()
  await expect(figures.locator('dd').nth(2)).not.toHaveText('0') // preguntas
  const table = page.getByRole('table', { name: 'Preguntas y respuestas' })
  const row = table.getByRole('row').filter({ hasText: question })
  await expect(row).toHaveCount(1)
  await expect(row).toContainText(/\d{2}\/\d{2}\/\d{4} \d{2}:\d{2}/)
  await expect(row).toContainText(/\d+,\d s/)
  await expect(row).toContainText('Respondida')
  await expect(page.getByText(/Mostrando 1–\d+ de \d+/)).toBeVisible()

  // La respuesta completa se despliega.
  await row.locator('summary').click()
  await expect(row.locator('details')).toHaveAttribute('open', '')

  for (const [name, extension] of [
    ['Descargar CSV', 'csv'],
    ['Descargar Excel', 'xlsx'],
  ]) {
    const download = page.waitForEvent('download')
    await page.getByRole('link', { name }).click()
    expect((await download).suggestedFilename()).toMatch(
      new RegExp(`^preguntas_\\d{4}-\\d{2}-\\d{2}_\\d{4}-\\d{2}-\\d{2}\\.${extension}$`),
    )
  }
})

test('gráfica recorrible con teclado y vista de tabla', async ({ page }) => {
  await ask(page, unique('Pregunta para la gráfica'))
  await openDashboard(page)
  await page.getByRole('button', { name: 'Últimos 7 días' }).click()
  await waitUntilLoaded(page)

  const chart = page.getByRole('img', { name: /Gráfica de preguntas/ })
  await chart.focus()
  await page.keyboard.press('End')
  await expect(page.getByRole('status').filter({ hasText: /pregunta/ })).toBeVisible()

  await page.getByRole('button', { name: 'Ver como tabla' }).click()
  const table = page.getByRole('table', { name: 'Actividad diaria por día' })
  await expect(table.getByRole('row')).toHaveCount(8) // cabecera + 7 días
})

for (const viewport of [
  { width: 1280, height: 800 },
  { width: 320, height: 640 },
]) {
  test(`cuadro de mando accesible a ${viewport.width} px`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await ask(page, unique('Pregunta de accesibilidad del cuadro'))
    await openDashboard(page)
    await expect(page.getByRole('table', { name: 'Preguntas y respuestas' })).toBeVisible()
    await expectAccessible(page)
    const overflow = await page.evaluate<number>(
      'document.documentElement.scrollWidth - document.documentElement.clientWidth',
    )
    expect(overflow).toBeLessThanOrEqual(0)
  })
}
