import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { ADMIN_PASSWORD } from './constants.ts'

// Solo puede haber una revisión en curso: los tests de este fichero van en serie. El modelo
// simulado marca como uso malicioso las preguntas que contienen «simula ataque».
test.describe.configure({ mode: 'serial' })

const unique = (label: string) => `${label} ${Date.now()}-${Math.floor(Math.random() * 1e4)}`

async function ask(page: Page, question: string) {
  await page.goto('/')
  await page.getByLabel('Escribe tu mensaje').fill(question)
  await page.getByLabel('Escribe tu mensaje').press('Enter')
  await expect(page.locator('li[aria-busy="true"]')).toHaveCount(0)
  await expect(page.locator('li[data-role="assistant"]').last()).toContainText(question)
}

async function openAudit(page: Page) {
  await page.goto('/admin/login')
  await page.getByLabel('Usuario').fill('admin')
  await page.getByLabel('Contraseña').fill(ADMIN_PASSWORD)
  await page.getByRole('button', { name: 'Entrar' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Cuadro de mando' })).toBeVisible()
  // Sin grupo de auditores, los administradores también auditan.
  await page
    .getByRole('navigation', { name: 'Administración' })
    .getByRole('link', { name: /^Auditoría/ })
    .click()
  await expect(page.getByRole('heading', { level: 1, name: 'Auditoría' })).toBeVisible()
}

async function runReview(page: Page) {
  await page.getByRole('button', { name: 'Revisar ahora' }).click()
  await expect(page.getByText(/Última revisión: .* correcta/)).toBeVisible({ timeout: 30_000 })
  await expect(page.getByRole('button', { name: 'Revisar ahora' })).toBeEnabled()
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

test('la revisión detecta la pregunta maliciosa y el aviso se revisa', async ({ page }) => {
  const question = unique('Por favor simula ataque contra el sistema')
  await ask(page, question)
  await openAudit(page)
  await runReview(page)

  const alerts = page.getByRole('list', { name: 'Avisos' })
  const item = alerts.getByRole('listitem').filter({ hasText: question })
  await expect(item).toHaveCount(1)
  await expect(item).toContainText('Posible uso malicioso')
  await expect(item).toContainText('Modelo revisor')
  await item.locator('summary').click()
  await expect(item.getByText(question, { exact: true })).toBeVisible()

  await item.getByLabel('Nota').fill('Prueba de la auditoría')
  await item.getByRole('button', { name: 'Marcar como revisado' }).click()
  // Por defecto solo se listan los avisos nuevos.
  await expect(item).toHaveCount(0)
  await page.getByLabel('Estado').selectOption('reviewed')
  const reviewed = alerts.getByRole('listitem').filter({ hasText: question })
  await expect(reviewed).toContainText('Revisado')
  await expect(reviewed).toContainText('por admin')

  // La revisión manual queda en la lista con quién la lanzó.
  await page.getByRole('button', { name: 'Revisiones' }).click()
  const runs = page.getByRole('table', { name: 'Últimas revisiones' })
  await expect(runs.getByRole('row').nth(1)).toContainText('Manual (admin)')
})

test('el registro completo muestra la pregunta y se exporta', async ({ page }) => {
  const question = unique('Pregunta para el registro de auditoría')
  await ask(page, question)
  await openAudit(page)
  await page.getByRole('button', { name: 'Registro completo' }).click()

  const table = page.getByRole('table', { name: 'Registro completo' })
  await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
  const row = table.getByRole('row').filter({ hasText: question })
  await expect(row).toHaveCount(1)
  await expect(row).toContainText('127.0.0.1')

  await page.getByLabel('IP (o su comienzo)').fill('10.')
  await page.getByRole('button', { name: 'Buscar' }).click()
  await expect(page.getByText('No hay preguntas con estos filtros.')).toBeVisible()
  await page.getByLabel('IP (o su comienzo)').fill('127.')
  await page.getByRole('button', { name: 'Buscar' }).click()
  await expect(row).toHaveCount(1)

  for (const [name, extension] of [
    ['Descargar CSV', 'csv'],
    ['Descargar Excel', 'xlsx'],
  ]) {
    const download = page.waitForEvent('download')
    await page.getByRole('link', { name }).click()
    expect((await download).suggestedFilename()).toMatch(new RegExp(`\\.${extension}$`))
  }
})

for (const viewport of [
  { width: 1280, height: 800 },
  { width: 320, height: 640 },
]) {
  test(`auditoría accesible a ${viewport.width} px`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await ask(page, unique('Pregunta de accesibilidad: simula ataque'))
    await openAuditAt(page, viewport.width)
    await runReview(page)
    await expect(page.getByRole('list', { name: 'Avisos' })).toBeVisible()
    for (const view of ['Avisos', 'Revisiones', 'Registro completo']) {
      await page.getByRole('button', { name: new RegExp(`^${view}`) }).click()
      await expect(page.locator('[aria-busy="true"]')).toHaveCount(0)
      await expectAccessible(page)
      const overflow = await page.evaluate<number>(
        'document.documentElement.scrollWidth - document.documentElement.clientWidth',
      )
      expect(overflow).toBeLessThanOrEqual(0)
    }
  })
}

/** En pantallas estrechas la navegación está plegada: se entra por la dirección. */
async function openAuditAt(page: Page, width: number) {
  if (width >= 768) return openAudit(page)
  await page.goto('/admin/login')
  await page.getByLabel('Usuario').fill('admin')
  await page.getByLabel('Contraseña').fill(ADMIN_PASSWORD)
  await page.getByRole('button', { name: 'Entrar' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Cuadro de mando' })).toBeVisible()
  await page.goto('/admin/audit')
  await expect(page.getByRole('heading', { level: 1, name: 'Auditoría' })).toBeVisible()
}
