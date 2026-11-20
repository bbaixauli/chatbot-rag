import { expect, test, type Page } from '@playwright/test'
import { ADMIN_PASSWORD } from './constants.ts'

// Configuración de la aplicación: cambia la de toda la instancia, así que va en su propio
// proyecto, después de los demás (ver playwright.config.ts), y en serie.
test.describe.configure({ mode: 'serial' })

const section = (page: Page, name: string) => page.getByRole('region', { name })

async function login(page: Page) {
  await page.goto('/admin/config')
  await page.getByLabel('Usuario').fill('admin')
  await page.getByLabel('Contraseña').fill(ADMIN_PASSWORD)
  await page.getByRole('button', { name: 'Entrar' }).click()
  await expect(page).toHaveURL(/\/admin\/config$/)
}

test.afterEach(async ({ page }) => {
  // Restablece los límites que cambian estos tests (la sesión de admin sigue en el contexto).
  const response = await page.request.put('/api/v1/admin/config/chat', {
    data: { welcome_max_length: 2000 },
  })
  expect(response.ok()).toBe(true)
})

test('prueba la conexión con los modelos configurados', async ({ page }) => {
  await login(page)

  const llm = section(page, 'Modelo de lenguaje')
  await expect(llm.getByLabel('Proveedor')).toHaveValue('fake')
  await llm.getByRole('button', { name: 'Probar conexión' }).click()
  await expect(llm.getByRole('status')).toContainText('responde')

  const embeddings = section(page, 'Embeddings')
  await embeddings.getByRole('button', { name: 'Probar conexión' }).click()
  await expect(embeddings.getByRole('status')).toHaveText(
    'El modelo «fake-hashing» devuelve vectores de 256 dimensiones',
  )
})

test('un límite guardado se aplica de inmediato en otras pantallas', async ({ page }) => {
  await login(page)
  const chat = section(page, 'Conversación')
  const limit = chat.getByLabel('Longitud máxima del mensaje inicial')

  await limit.fill('5')
  await chat.getByRole('button', { name: 'Guardar' }).click()
  await expect(chat.getByRole('status')).toContainText('Cambios guardados.')
  await expect(chat.getByText(/Guardado por admin/)).toBeVisible()

  await page.reload()
  await expect(section(page, 'Conversación').getByLabel(/mensaje inicial/)).toHaveValue('5')

  await page.getByRole('link', { name: 'Mensaje inicial' }).click()
  await page.getByLabel('Mensaje inicial').fill('Demasiado largo')
  await expect(page.getByText('15 / 5 caracteres')).toBeVisible()
  await expect(page.getByRole('button', { name: 'Guardar' })).toBeDisabled()
})

test('valida los valores antes de guardar', async ({ page }) => {
  await login(page)
  const rag = section(page, 'Recuperación')

  await rag.getByLabel('Puntuación mínima (0-1)').fill('2')
  await rag.getByRole('button', { name: 'Guardar' }).click()

  await expect(rag.getByText('Debe estar entre 0 y 1.')).toBeVisible()
  await expect(rag.getByRole('alert')).toHaveText('Revisa los campos marcados.')
})
