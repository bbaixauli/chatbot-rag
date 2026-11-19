import { expect, test, type Browser, type Page } from '@playwright/test'
import { ADMIN_PASSWORD } from './constants.ts'

// Instancia principal (sin LDAP). Las sesiones privadas son un ajuste global: este proyecto
// se ejecuta después del resto, en serie, y deja el ajuste desactivado al terminar.
test.describe.configure({ mode: 'serial' })

const unique = (label: string) => `${label} ${Date.now()}-${Math.floor(Math.random() * 1e4)}`

const history = (page: Page) =>
  page.getByRole('navigation', { name: 'Historial de conversaciones' })
const privateBadge = (page: Page) => page.getByRole('banner').getByText('Sesión privada')

async function adminLogin(page: Page) {
  await page.getByLabel('Usuario').fill('admin')
  await page.getByLabel('Contraseña').fill(ADMIN_PASSWORD)
  await page.getByRole('button', { name: 'Entrar' }).click()
}

/** La administración activa o desactiva las sesiones privadas. */
async function setPrivateSessions(browser: Browser, enabled: boolean) {
  const context = await browser.newContext()
  const page = await context.newPage()
  await page.goto('/admin/chat')
  await adminLogin(page)
  const checkbox = page.getByRole('checkbox', { name: 'Sesiones privadas y temporales' })
  await expect(checkbox).toBeVisible()
  if ((await checkbox.isChecked()) !== enabled) {
    await checkbox.click()
    await expect(page.getByText('Acceso actualizado.')).toBeVisible()
  }
  await expect(checkbox).toBeChecked({ checked: enabled })
  await context.close()
}

async function ask(page: Page, question: string) {
  await page.getByLabel('Escribe tu mensaje').fill(question)
  await page.getByLabel('Escribe tu mensaje').press('Enter')
  await expect(history(page).getByRole('link', { name: question })).toBeVisible()
}

const sharedQuestion = unique('Pregunta del historial compartido')
const privateQuestion = unique('Pregunta de una sesión privada')

test.afterAll(async ({ browser }) => {
  await setPrivateSessions(browser, false)
})

test('desactivadas, todos comparten el historial', async ({ browser }) => {
  await setPrivateSessions(browser, false)
  const first = await browser.newPage()
  const second = await browser.newPage()

  await first.goto('/')
  await ask(first, sharedQuestion)
  await second.goto('/')

  await expect(history(second).getByRole('link', { name: sharedQuestion })).toBeVisible()
  await expect(privateBadge(second)).toHaveCount(0)
  await first.context().close()
  await second.context().close()
})

test('activadas, cada visitante ve solo sus conversaciones', async ({ browser }) => {
  await setPrivateSessions(browser, true)
  const first = await browser.newPage()
  const second = await browser.newPage()
  const otherQuestion = unique('Pregunta de otra sesión privada')

  // Dos personas a la vez: la sesión se crea sola al entrar.
  await first.goto('/')
  await second.goto('/')
  await expect(privateBadge(first)).toBeVisible()
  await expect(privateBadge(second)).toBeVisible()
  await expect(history(first).getByRole('link', { name: sharedQuestion })).toHaveCount(0)

  await ask(first, privateQuestion)
  await ask(second, otherQuestion)
  await first.reload()
  await second.reload()
  await expect(history(first).getByRole('link')).toHaveText([privateQuestion])
  await expect(history(second).getByRole('link')).toHaveText([otherQuestion])

  // La conversación de la otra persona no se puede abrir por URL.
  await second.goto(new URL(first.url()).pathname)
  await expect(second.getByRole('main')).not.toContainText(privateQuestion)

  // Entrar y salir de la administración no cierra la sesión privada.
  await first.getByRole('banner').getByRole('link', { name: 'Administración' }).click()
  await adminLogin(first)
  await expect(first.getByRole('heading', { name: 'Prompt de sistema' })).toBeVisible()
  await first.getByRole('button', { name: 'Cerrar sesión' }).click()
  await first.goto('/')
  await expect(history(first).getByRole('link')).toHaveText([privateQuestion])

  // «Terminar sesión» empieza una sesión vacía.
  first.once('dialog', (dialog) => void dialog.accept())
  await first.getByRole('button', { name: 'Terminar sesión' }).click()
  await expect(first.getByRole('heading', { name: '¿En qué puedo ayudarte?' })).toBeVisible()
  await expect(privateBadge(first)).toBeVisible()
  await expect(history(first).getByRole('link')).toHaveCount(0)

  await first.context().close()
  await second.context().close()
})

test('al desactivarlas se vuelve al historial compartido', async ({ browser }) => {
  await setPrivateSessions(browser, true)
  const page = await browser.newPage()
  await page.goto('/')
  await expect(privateBadge(page)).toBeVisible()

  await setPrivateSessions(browser, false)
  await page.reload()

  await expect(history(page).getByRole('link', { name: sharedQuestion })).toBeVisible()
  await expect(history(page).getByRole('link', { name: privateQuestion })).toHaveCount(0)
  await expect(privateBadge(page)).toHaveCount(0)
  await page.context().close()
})
