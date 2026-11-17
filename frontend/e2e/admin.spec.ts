import { expect, test, type Page } from '@playwright/test'
import { ADMIN_PASSWORD } from './constants.ts'

// Comparten el prompt global: se ejecutan en serie.
test.describe.configure({ mode: 'serial' })

const unique = (label: string) => `${label} ${Date.now()}`

async function login(page: Page, password = ADMIN_PASSWORD) {
  await page.getByLabel('Usuario').fill('admin')
  await page.getByLabel('Contraseña').fill(password)
  await page.getByRole('button', { name: 'Entrar' }).click()
}

test('la administración exige iniciar sesión', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: 'Administración' }).click()

  await expect(page).toHaveURL(/\/admin\/login$/)
  await login(page, 'incorrecta')
  await expect(page.getByRole('alert')).toHaveText('Usuario o contraseña incorrectos.')

  await login(page)
  await expect(page).toHaveURL(/\/admin\/prompt$/)
  await expect(page.getByRole('heading', { name: 'Prompt de sistema' })).toBeVisible()
})

test('edita el prompt, lo conserva al recargar y restaura una versión', async ({ page }) => {
  const first = unique('Eres un asistente experto en contratos.')
  const second = unique('Eres un asistente experto en facturas.')
  await page.goto('/admin/prompt')
  await login(page)

  const editor = page.getByLabel('Prompt de sistema')
  const save = page.getByRole('button', { name: 'Guardar' })
  await expect(save).toBeDisabled()

  for (const content of [first, second]) {
    await editor.fill(content)
    await save.click()
    await expect(page.getByRole('status')).toHaveText('Prompt guardado.')
    await expect(editor).toHaveValue(content)
  }

  await page.reload()
  await expect(editor).toHaveValue(second)
  const history = page.getByRole('region', { name: 'Historial de versiones' })
  const current = history.getByRole('listitem').filter({ hasText: second })
  await expect(current.getByText('Vigente')).toBeVisible()

  page.once('dialog', (dialog) => void dialog.accept())
  await history
    .getByRole('listitem')
    .filter({ hasText: first })
    .first()
    .getByRole('button', { name: 'Restaurar' })
    .click()

  await expect(page.getByRole('status')).toHaveText('Versión restaurada.')
  await expect(editor).toHaveValue(first)
  await expect(history.getByRole('listitem').first()).toContainText(first)
  await expect(history.getByRole('listitem').first().getByText('Vigente')).toBeVisible()
})

test('cerrar sesión protege de nuevo la administración', async ({ page }) => {
  await page.goto('/admin/login')
  await login(page)
  await expect(page.getByLabel('Prompt de sistema')).toBeVisible()

  await page.getByRole('button', { name: 'Cerrar sesión' }).click()
  await expect(page).toHaveURL(/\/admin\/login$/)

  await page.goto('/admin/prompt')
  await expect(page).toHaveURL(/\/admin\/login$/)
  await page.getByRole('link', { name: 'Volver al chat' }).click()
  await expect(page.getByLabel('Escribe tu mensaje')).toBeVisible()
})
