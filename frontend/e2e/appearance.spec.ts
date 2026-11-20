import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Browser, type Page } from '@playwright/test'
import { ADMIN_PASSWORD } from './constants.ts'

// La apariencia es global: este proyecto se ejecuta al final y la restablece al terminar.
test.describe.configure({ mode: 'serial' })

const LOGO = Buffer.from(
  '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 40 40">' +
    '<rect width="40" height="40" fill="#ffffff"/></svg>',
)

// Valores del manual de estilos del Ayuntamiento de Valladolid (3.1).
const BRAND = {
  'Color de marca': '#980046',
  'Marca oscuro (botón al pasar el ratón)': '#4e1036',
  'Marca suave (opción activa)': '#fbeff4',
  Enlaces: '#7a0038',
  Texto: '#1f1a1c',
  'Texto secundario': '#5c5458',
}

async function adminLogin(page: Page) {
  await page.goto('/admin/appearance')
  await page.getByLabel('Usuario').fill('admin')
  await page.getByLabel('Contraseña').fill(ADMIN_PASSWORD)
  await page.getByRole('button', { name: 'Entrar' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Apariencia' })).toBeVisible()
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

async function restoreDefaults(browser: Browser) {
  const context = await browser.newContext()
  const request = context.request
  await request.post('/api/v1/admin/session', {
    data: { username: 'admin', password: ADMIN_PASSWORD },
  })
  expect((await request.delete('/api/v1/admin/appearance')).ok()).toBe(true)
  expect((await request.delete('/api/v1/admin/appearance/logo')).ok()).toBe(true)
  await context.close()
}

test.afterAll(async ({ browser }) => restoreDefaults(browser))

test('configura la identidad municipal y se aplica en toda la aplicación', async ({
  browser,
  page,
}) => {
  await adminLogin(page)
  await expectAccessible(page)

  await page.getByLabel('Nombre de la aplicación').fill('Asistente municipal')
  await page.getByLabel('Organización').fill('Ayuntamiento de Ejemplo')
  await page.getByLabel('Texto alternativo del logo').fill('Escudo de ejemplo')
  await page.getByRole('radio', { name: /color de marca/ }).check()
  for (const [label, value] of Object.entries(BRAND)) {
    await page.getByRole('textbox', { name: label, exact: true }).fill(value)
  }
  await page.getByLabel('Tipo de letra principal').selectOption({ label: 'Trebuchet MS' })
  await page.getByLabel('Unidad responsable').fill('Servicio de Informática')
  await page
    .getByLabel('Aviso en las pantallas de acceso')
    .fill('Sistema de uso exclusivo para fines municipales.')
  await expect(page.getByText('Todos los contrastes cumplen WCAG 2.1 AA.')).toBeVisible()
  await page.getByRole('button', { name: 'Guardar apariencia' }).click()
  await expect(page.getByText('Apariencia guardada.')).toBeVisible()

  await page.getByLabel('Subir un logo nuevo').setInputFiles({
    name: 'escudo.svg',
    mimeType: 'image/svg+xml',
    buffer: LOGO,
  })
  await expect(page.getByText('Logo actualizado.')).toBeVisible()

  // El chat (otra página) usa la nueva apariencia.
  await page.goto('/')
  const header = page.getByRole('banner')
  await expect(header.getByRole('img', { name: 'Escudo de ejemplo' })).toBeVisible()
  await expect(header).toContainText('Asistente municipal')
  await expect(header).toHaveCSS('background-color', 'rgb(152, 0, 70)')
  await expect(page.locator('body')).toHaveCSS('font-family', /Trebuchet MS/)
  await expect(page).toHaveTitle(
    'Nueva conversación – Asistente municipal – Ayuntamiento de Ejemplo',
  )
  await expect(page.getByRole('contentinfo')).toContainText('Servicio de Informática')
  await expectAccessible(page)

  // Pantalla de acceso (sin sesión): logo y aviso de uso.
  const visitor = await browser.newPage()
  await visitor.goto('/admin/login')
  await expect(visitor.getByText('Sistema de uso exclusivo para fines municipales.')).toBeVisible()
  await expect(visitor.getByRole('img', { name: 'Escudo de ejemplo' })).toBeVisible()
  await visitor.context().close()
})

test('avisa de un contraste insuficiente pero permite guardar', async ({ page }) => {
  await adminLogin(page)
  await page.getByRole('textbox', { name: 'Enlaces', exact: true }).fill('#93c5fd')

  await expect(page.getByText(/Enlaces sobre Fondo: .*\(mínimo 4,5:1\)/)).toBeVisible()
  await page.getByRole('button', { name: 'Guardar apariencia' }).click()
  await expect(page.getByText('Apariencia guardada con 1 aviso de contraste.')).toBeVisible()
})

test('restablecer vuelve al tema genérico', async ({ page }) => {
  await adminLogin(page)
  page.once('dialog', (dialog) => void dialog.accept())
  await page.getByRole('button', { name: 'Restablecer valores por defecto' }).click()
  await expect(page.getByLabel('Nombre de la aplicación')).toHaveValue('Chatbot RAG')

  await page.goto('/')
  await expect(page.getByRole('banner')).toHaveCSS('background-color', 'rgb(255, 255, 255)')
  await expect(page).toHaveTitle('Nueva conversación – Chatbot RAG')
})
