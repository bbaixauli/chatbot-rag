import AxeBuilder from '@axe-core/playwright'
import { expect, test, type Page } from '@playwright/test'
import { ADMIN_PASSWORD } from './constants.ts'

// Manual de estilos (5.4 y 8.6): axe-core en los E2E; falla con incidencias graves o
// críticas de WCAG 2.1 A/AA. Además, sin desplazamiento horizontal a 320 px (WCAG 1.4.10).

const WIDTHS = [
  { name: 'escritorio', width: 1280, height: 800 },
  { name: 'móvil (320 px)', width: 320, height: 640 },
]

async function expectAccessible(page: Page) {
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze()
  const serious = results.violations
    .filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)
  expect(serious).toEqual([])
}

async function expectNoHorizontalScroll(page: Page) {
  // Expresión evaluada en el navegador (los E2E se compilan sin la librería DOM).
  const overflow = await page.evaluate<number>(
    'document.documentElement.scrollWidth - document.documentElement.clientWidth',
  )
  expect(overflow).toBeLessThanOrEqual(0)
}

async function adminLogin(page: Page) {
  await page.goto('/admin/login')
  await page.getByLabel('Usuario').fill('admin')
  await page.getByLabel('Contraseña').fill(ADMIN_PASSWORD)
  await page.getByRole('button', { name: 'Entrar' }).click()
  await expect(page.getByRole('heading', { level: 1, name: 'Cuadro de mando' })).toBeVisible()
}

for (const viewport of WIDTHS) {
  test.describe(`accesibilidad en ${viewport.name}`, () => {
    test.use({ viewport: { width: viewport.width, height: viewport.height } })

    test('chat: portada y conversación con respuesta', async ({ page }) => {
      await page.goto('/')
      await expect(page.getByRole('heading', { name: '¿En qué puedo ayudarte?' })).toBeVisible()
      await expect(page).toHaveTitle('Nueva conversación – Chatbot RAG')
      await expectAccessible(page)
      await expectNoHorizontalScroll(page)

      const question = `Pregunta de accesibilidad ${Date.now()}`
      await page.getByLabel('Escribe tu mensaje').fill(question)
      await page.getByLabel('Escribe tu mensaje').press('Enter')
      await expect(page.locator('li[aria-busy="true"]')).toHaveCount(0)
      await expect(page.locator('li[data-role="assistant"]').last()).toContainText(question)
      await expect(page).toHaveTitle(`${question} – Chatbot RAG`)
      await expectAccessible(page)
      await expectNoHorizontalScroll(page)
    })

    test('administración: acceso, prompt, documentos y mensaje inicial', async ({ page }) => {
      await page.goto('/admin/login')
      await expect(page).toHaveTitle('Acceso de administración – Chatbot RAG')
      await expectAccessible(page)
      await expectNoHorizontalScroll(page)

      await adminLogin(page)
      for (const [path, heading] of [
        ['/admin/dashboard', 'Cuadro de mando'],
        ['/admin/prompt', 'Prompt de sistema'],
        ['/admin/documents', 'Documentos'],
        ['/admin/chat', 'Mensaje inicial del chat'],
        ['/admin/appearance', 'Apariencia'],
        ['/admin/config', 'Configuración'],
      ]) {
        await page.goto(path)
        await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible()
        await expect(page).toHaveTitle(`${heading} – Chatbot RAG`)
        await expectAccessible(page)
        await expectNoHorizontalScroll(page)
      }
    })
  })
}

test.describe('estructura y teclado', () => {
  test('el primer elemento enfocable salta al contenido principal', async ({ page }) => {
    await page.goto('/')
    await page.keyboard.press('Tab')
    const skip = page.getByRole('link', { name: 'Saltar al contenido principal' })
    await expect(skip).toBeFocused()
    await skip.press('Enter')
    await expect(page.locator('main#contenido')).toBeFocused()
  })

  test('cada página tiene un único h1, pie con versión y aviso de IA', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('h1')).toHaveCount(1)
    await expect(page.getByRole('contentinfo')).toContainText(/versión \d+\.\d+\.\d+/)
    await expect(page.getByText(/sistema de inteligencia artificial/)).toBeVisible()
  })

  test.describe('menú en pantallas estrechas', () => {
    test.use({ viewport: { width: 320, height: 640 } })

    test('el historial se pliega tras «Menú» y se cierra al navegar o con Esc', async ({
      page,
    }) => {
      await page.goto('/')
      const menu = page.getByRole('button', { name: 'Menú' })
      const history = page.getByRole('navigation', { name: 'Historial de conversaciones' })
      await expect(menu).toHaveAttribute('aria-expanded', 'false')
      await expect(history).toBeHidden()

      await menu.click()
      await expect(menu).toHaveAttribute('aria-expanded', 'true')
      await expect(history).toBeVisible()
      await page.keyboard.press('Escape')
      await expect(history).toBeHidden()

      await menu.click()
      await history.getByRole('button', { name: 'Nueva conversación' }).click()
      await expect(history).toBeHidden()
    })
  })
})
