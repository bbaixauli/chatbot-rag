import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import { fileURLToPath } from 'node:url'
import { ADMIN_PASSWORD } from './constants.ts'

// Comparten el índice de documentos: se ejecutan en serie.
test.describe.configure({ mode: 'serial' })

const fixture = (name: string) => fileURLToPath(new URL(`./fixtures/${name}`, import.meta.url))
const FILES = ['guia-vacaciones.md', 'politica-gastos.html', 'manual-empleado.pdf']

async function apiLogin(request: APIRequestContext) {
  const response = await request.post('/api/v1/admin/session', {
    data: { username: 'admin', password: ADMIN_PASSWORD },
  })
  expect(response.ok()).toBeTruthy()
}

// Parte de un índice vacío aunque se reutilice un servidor ya arrancado.
test.beforeAll(async ({ request }) => {
  await apiLogin(request)
  const documents = await (await request.get('/api/v1/admin/documents')).json()
  for (const document of documents) {
    await request.delete(`/api/v1/admin/documents/${document.id}`)
  }
})

async function openDocuments(page: Page) {
  await page.goto('/admin/login')
  await page.getByLabel('Usuario').fill('admin')
  await page.getByLabel('Contraseña').fill(ADMIN_PASSWORD)
  await page.getByRole('button', { name: 'Entrar' }).click()
  await page
    .getByRole('navigation', { name: 'Administración' })
    .getByRole('link', { name: 'Documentos' })
    .click()
  await expect(page.getByRole('heading', { name: 'Documentos' })).toBeVisible()
}

const row = (page: Page, filename: string) => page.getByRole('row', { name: new RegExp(filename) })

test('sube PDF, Markdown y HTML y quedan indexados', async ({ page }) => {
  await openDocuments(page)
  await expect(page.getByText('Todavía no hay documentos.')).toBeVisible()

  await page.getByTestId('file-input').setInputFiles(FILES.map(fixture))

  const uploads = page.getByRole('region', { name: 'Subidas' })
  for (const name of FILES) {
    await expect(uploads.getByRole('listitem').filter({ hasText: name })).toContainText('Subido')
    await expect(row(page, name).getByText('Indexado')).toBeVisible()
  }
  await expect(row(page, 'manual-empleado.pdf')).toContainText('PDF')
  await expect(row(page, 'manual-empleado.pdf').getByRole('cell').nth(4)).toHaveText('3')
  await expect(row(page, 'politica-gastos.html')).toContainText('HTML')
  await expect(page.getByTestId('index-stats')).toContainText('3 documentos')
  await expect(page.getByTestId('index-stats')).toContainText('fake-hashing')
})

test('detecta duplicados y rechaza tipos no admitidos', async ({ page }) => {
  await openDocuments(page)

  await page
    .getByTestId('file-input')
    .setInputFiles([fixture('guia-vacaciones.md'), fixture('notas.txt')])

  const uploads = page.getByRole('region', { name: 'Subidas' })
  await expect(
    uploads.getByRole('listitem').filter({ hasText: 'guia-vacaciones.md' }),
  ).toContainText('Ya existía')
  await expect(uploads.getByRole('listitem').filter({ hasText: 'notas.txt' })).toContainText(
    'Tipo de fichero no admitido',
  )
  await expect(page.getByTestId('index-stats')).toContainText('3 documentos')
})

test('descarga, reindexa y elimina un documento', async ({ page }) => {
  await openDocuments(page)

  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('link', { name: 'Descargar politica-gastos.html' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe('politica-gastos.html')

  await row(page, 'guia-vacaciones.md')
    .getByRole('button', { name: /Reindexar/ })
    .click()
  await expect(row(page, 'guia-vacaciones.md').getByText('Indexado')).toBeVisible()

  page.once('dialog', (dialog) => void dialog.accept())
  await row(page, 'politica-gastos.html')
    .getByRole('button', { name: /Eliminar/ })
    .click()
  await expect(row(page, 'politica-gastos.html')).toHaveCount(0)
  await expect(page.getByTestId('index-stats')).toContainText('2 documentos')
})
