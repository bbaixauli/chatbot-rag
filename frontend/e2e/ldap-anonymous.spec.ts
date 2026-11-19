import { expect, test, type Browser, type Page } from '@playwright/test'
import { ADMIN_PASSWORD } from './constants.ts'

// Instancia con LDAP (proyecto `chromium-ldap`). El acceso anónimo es un ajuste global:
// los tests se ejecutan en serie.
test.describe.configure({ mode: 'serial' })

const unique = (label: string) => `${label} ${Date.now()}-${Math.floor(Math.random() * 1e4)}`

const history = (page: Page) =>
  page.getByRole('navigation', { name: 'Historial de conversaciones' })
const anonymousButton = (page: Page) =>
  page.getByRole('button', { name: 'Acceder de forma anónima' })

/** La administración (cuenta local) activa o desactiva el acceso anónimo. */
async function setAnonymousAccess(browser: Browser, allowed: boolean) {
  const context = await browser.newContext()
  const page = await context.newPage()
  await page.goto('/admin/chat')
  await page.getByLabel('Usuario').fill('admin')
  await page.getByLabel('Contraseña').fill(ADMIN_PASSWORD)
  await page.getByRole('button', { name: 'Entrar' }).click()

  const checkbox = page.getByRole('checkbox', { name: 'Permitir el acceso anónimo' })
  await expect(checkbox).toBeVisible()
  if ((await checkbox.isChecked()) !== allowed) {
    await checkbox.click()
    await expect(page.getByText('Acceso actualizado.')).toBeVisible()
  }
  await expect(checkbox).toBeChecked({ checked: allowed })
  await context.close()
}

async function enterAnonymously(page: Page) {
  await page.goto('/')
  await expect(page).toHaveURL(/\/login$/)
  await anonymousButton(page).click()
  await expect(page.getByRole('banner').getByText('Anónimo')).toBeVisible()
}

async function ask(page: Page, question: string) {
  await page.getByLabel('Escribe tu mensaje').fill(question)
  await page.getByLabel('Escribe tu mensaje').press('Enter')
  await expect(history(page).getByRole('link', { name: question })).toBeVisible()
}

test('sin el check no se puede acceder de forma anónima', async ({ browser, page }) => {
  await setAnonymousAccess(browser, false)

  await page.goto('/')
  await expect(page).toHaveURL(/\/login$/)
  await expect(page.getByRole('heading', { name: 'Iniciar sesión' })).toBeVisible()
  await expect(anonymousButton(page)).toHaveCount(0)
  // Tampoco llamando directamente a la API.
  expect((await page.request.post('/api/v1/auth/anonymous')).status()).toBe(403)
  expect((await page.request.get('/api/v1/conversations')).status()).toBe(401)
  await page.goto('/c/cualquiera')
  await expect(page).toHaveURL(/\/login$/)
})

test('cada acceso anónimo ve solo sus conversaciones y empieza de cero', async ({ browser }) => {
  await setAnonymousAccess(browser, true)
  const first = await browser.newContext()
  const second = await browser.newContext()
  const firstPage = await first.newPage()
  const secondPage = await second.newPage()
  const firstQuestion = unique('Pregunta de la primera persona')
  const secondQuestion = unique('Pregunta de la segunda persona')

  // Dos personas a la vez.
  await enterAnonymously(firstPage)
  await enterAnonymously(secondPage)
  await ask(firstPage, firstQuestion)
  await ask(secondPage, secondQuestion)

  await firstPage.reload()
  await secondPage.reload()
  await expect(history(firstPage).getByRole('link')).toHaveText([firstQuestion])
  await expect(history(secondPage).getByRole('link')).toHaveText([secondQuestion])
  // La conversación de la otra persona no se puede abrir por URL.
  const firstUrl = new URL(firstPage.url()).pathname
  await secondPage.goto(firstUrl)
  await expect(secondPage.getByRole('main')).not.toContainText(firstQuestion)

  // Al salir y volver a entrar, no aparece ninguna conversación.
  await firstPage.getByRole('button', { name: 'Cerrar sesión' }).click()
  await expect(anonymousButton(firstPage)).toBeVisible()
  await anonymousButton(firstPage).click()
  await expect(firstPage.getByRole('banner').getByText('Anónimo')).toBeVisible()
  await expect(firstPage.getByRole('heading', { name: '¿En qué puedo ayudarte?' })).toBeVisible()
  await expect(history(firstPage).getByRole('link')).toHaveCount(0)

  await first.close()
  await second.close()
})

test('al desactivar el check se cierran las sesiones anónimas abiertas', async ({ browser }) => {
  await setAnonymousAccess(browser, true)
  const context = await browser.newContext()
  const page = await context.newPage()
  await enterAnonymously(page)
  await ask(page, unique('Pregunta antes de desactivar'))

  await setAnonymousAccess(browser, false)

  await page.reload()
  await expect(page).toHaveURL(/\/login$/)
  await expect(anonymousButton(page)).toHaveCount(0)
  await context.close()
})
