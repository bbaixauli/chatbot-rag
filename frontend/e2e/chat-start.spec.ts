import { expect, test, type Page } from '@playwright/test'
import { ADMIN_PASSWORD } from './constants.ts'

// Configuración global del chat: se ejecuta en serie y en su propio proyecto (ver
// playwright.config.ts) para no afectar a los demás tests.
test.describe.configure({ mode: 'serial' })

const messages = (page: Page) => page.getByRole('list', { name: 'Mensajes' })
const suggestions = (page: Page) => page.getByRole('list', { name: 'Preguntas sugeridas' })

async function login(page: Page) {
  await page.goto('/admin/chat')
  await page.getByLabel('Usuario').fill('admin')
  await page.getByLabel('Contraseña').fill(ADMIN_PASSWORD)
  await page.getByRole('button', { name: 'Entrar' }).click()
  await expect(page).toHaveURL(/\/admin\/chat$/)
}

test.afterEach(async ({ page }) => {
  // Deja el chat sin mensaje inicial ni sugerencias (la sesión de admin sigue en el contexto).
  const response = await page.request.put('/api/v1/admin/chat-config', {
    data: { welcome_message: null, suggested_questions: [] },
  })
  expect(response.ok()).toBe(true)
})

test('configura el mensaje inicial y las preguntas sugeridas y se usan en el chat', async ({
  page,
}) => {
  await login(page)
  await page.getByLabel('Mensaje inicial').fill('¡Hola! Soy el asistente de **RR. HH.**')
  await page.getByLabel('Pregunta sugerida 1').fill('¿Cuántos días de vacaciones tengo?')
  await page.getByLabel('Pregunta sugerida 3').fill('¿Cómo justifico un gasto?')
  await page.getByRole('button', { name: 'Guardar' }).click()
  await expect(page.getByRole('status')).toHaveText('Configuración guardada.')

  await page.reload()
  await expect(page.getByLabel('Pregunta sugerida 2')).toHaveValue('¿Cómo justifico un gasto?')

  await page.getByRole('link', { name: 'Volver al chat' }).click()
  const welcome = messages(page).locator('li[data-role="assistant"]').first()
  await expect(welcome).toContainText('¡Hola! Soy el asistente de')
  await expect(welcome.locator('strong')).toHaveText('RR. HH.')
  await expect(page.getByRole('heading', { name: '¿En qué puedo ayudarte?' })).toHaveCount(0)

  const input = page.getByLabel('Escribe tu mensaje')
  await input.fill('texto que se descarta')
  await suggestions(page).getByRole('button', { name: '¿Cómo justifico un gasto?' }).click()
  await expect(input).toHaveValue('¿Cómo justifico un gasto?')
  await expect(messages(page).locator('li[data-role="user"]')).toHaveCount(0)

  await input.press('End')
  await input.pressSequentially(' Es urgente')
  await input.press('Enter')

  const question = '¿Cómo justifico un gasto? Es urgente'
  await expect(messages(page).locator('li[data-role="user"]')).toHaveText([`Tú: ${question}`])
  await expect(
    messages(page).locator('li[data-role="assistant"]').filter({ hasText: question }),
  ).toBeVisible()
  await expect(suggestions(page)).toHaveCount(0)

  // Al reabrir la conversación se mantiene el mensaje inicial como primera burbuja.
  await page.reload()
  await expect(messages(page).locator('li[data-role]')).toHaveCount(3)
  await expect(messages(page).locator('li[data-role]').first()).toContainText('Soy el asistente')
})

test('sin configuración el chat muestra la portada habitual', async ({ page }) => {
  await login(page)
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '¿En qué puedo ayudarte?' })).toBeVisible()
  await expect(suggestions(page)).toHaveCount(0)
})
