import { expect, test, type APIRequestContext, type Page } from '@playwright/test'
import http from 'node:http'
import type { AddressInfo } from 'node:net'
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

// Parte de un índice vacío (y sin categorías) aunque se reutilice un servidor ya arrancado.
test.beforeAll(async ({ request }) => {
  await apiLogin(request)
  const documents = await (await request.get('/api/v1/admin/documents')).json()
  for (const document of documents) {
    await request.delete(`/api/v1/admin/documents/${document.id}`)
  }
  const categories = await (await request.get('/api/v1/admin/categories')).json()
  for (const category of categories) {
    await request.delete(`/api/v1/admin/categories/${category.id}`)
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
  await expect(row(page, 'manual-empleado.pdf').getByRole('cell').nth(5)).toHaveText('3')
  await expect(row(page, 'politica-gastos.html')).toContainText('HTML')
  // Títulos: el de la página HTML, el primer encabezado del Markdown y, sin metadatos, el
  // nombre del fichero del PDF.
  await expect(row(page, 'politica-gastos.html')).toContainText('Política de gastos')
  await expect(row(page, 'guia-vacaciones.md')).toContainText('Guía de vacaciones')
  await expect(
    row(page, 'manual-empleado.pdf').getByRole('link', { name: 'Descargar manual-empleado.pdf' }),
  ).toBeVisible()
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
  await page.getByRole('link', { name: 'Descargar Política de gastos' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe('politica-gastos.html')

  // «Indexado» ya era visible antes de reindexar: se espera a la respuesta del servidor
  // y a que la API confirme que ha terminado.
  const reindexed = page.waitForResponse(
    (r) => r.url().includes('/reindex') && r.request().method() === 'POST',
  )
  await row(page, 'guia-vacaciones.md')
    .getByRole('button', { name: /Reindexar/ })
    .click()
  expect((await reindexed).status()).toBe(202)
  await expect
    .poll(async () => {
      const documents = await (await page.request.get('/api/v1/admin/documents')).json()
      return documents.map((d: { status: string }) => d.status)
    })
    .toEqual(['ready', 'ready', 'ready'])
  await expect(row(page, 'guia-vacaciones.md').getByText('Indexado')).toBeVisible()

  page.once('dialog', (dialog) => void dialog.accept())
  await row(page, 'politica-gastos.html')
    .getByRole('button', { name: /Eliminar/ })
    .click()
  await expect(row(page, 'politica-gastos.html')).toHaveCount(0)
  await expect(page.getByTestId('index-stats')).toContainText('2 documentos')
})

// --- RAG: tras los tests anteriores quedan indexados guia-vacaciones.md y manual-empleado.pdf ---

const answers = (page: Page) =>
  page.getByRole('list', { name: 'Mensajes' }).locator('li[data-role="assistant"]')

async function ask(page: Page, question: string) {
  await page.getByLabel('Escribe tu mensaje').fill(question)
  await page.getByLabel('Escribe tu mensaje').press('Enter')
  await expect(answers(page).last()).toContainText(question)
  await expect(page.locator('li[aria-busy="true"]')).toHaveCount(0)
}

test('el chat responde citando el documento y enlaza al fichero', async ({ page }) => {
  await page.goto('/')
  await ask(page, '¿Cuántos días de vacaciones tengo al año?')

  const answer = answers(page).last()
  const sources = answer.getByRole('region', { name: 'Fuentes' })
  // Con el título del documento (su sección es el propio título: no se repite).
  const link = sources.getByRole('link', { name: 'Abrir fuente: [1] Guía de vacaciones' })
  await expect(link).toBeVisible()
  await expect(link).toHaveAttribute('href', /^\/api\/v1\/documents\/[0-9a-f-]{36}\/file$/)
  await expect(answer.getByRole('link', { name: '[1]', exact: true })).toBeVisible()

  const popupPromise = page.waitForEvent('popup')
  await link.click()
  const popup = await popupPromise
  await expect(popup.locator('body')).toContainText('23 días laborables de vacaciones')

  await page.reload()
  await expect(answers(page).last().getByRole('region', { name: 'Fuentes' })).toContainText(
    'Guía de vacaciones',
  )
})

test('las referencias a PDF indican la página y abren el PDF en ella', async ({ page }) => {
  await page.goto('/')
  await ask(page, '¿Cuál es el horario de oficina?')

  const link = answers(page)
    .last()
    .getByRole('link', { name: 'Abrir fuente: [1] manual-empleado.pdf · pág. 2' })
  await expect(link).toBeVisible()
  const href = await link.getAttribute('href')
  expect(href).toMatch(/\/file#page=2$/)

  const response = await page.request.get(href!.replace(/#.*$/, ''))
  expect(response.headers()['content-type']).toBe('application/pdf')
  expect(response.headers()['content-disposition']).toMatch(/^inline;/)
})

test('una pregunta sin relación con los documentos no muestra fuentes', async ({ page }) => {
  await page.goto('/')
  await ask(page, 'xyzzy plugh frobozz')

  await expect(answers(page).last().getByRole('region')).toHaveCount(0)
})

test('cambia el título y la categoría y las referencias usan el título nuevo', async ({ page }) => {
  await openDocuments(page)

  const categories = page.getByRole('region', { name: 'Categorías' })
  await categories.getByRole('textbox', { name: 'Nueva categoría' }).fill('Recursos humanos')
  await categories.getByRole('button', { name: 'Añadir' }).click()
  await expect(categories.getByRole('list', { name: 'Categorías' })).toContainText(
    'Recursos humanos0 documentos',
  )

  await page.getByRole('button', { name: 'Editar: manual-empleado.pdf' }).click()
  const form = page.getByRole('form', { name: 'Editar «manual-empleado.pdf»' })
  await form.getByRole('textbox', { name: 'Título' }).fill('Manual del empleado')
  await form.getByRole('combobox', { name: 'Categoría' }).selectOption('Recursos humanos')
  await form.getByRole('button', { name: 'Guardar' }).click()

  const manual = row(page, 'manual-empleado.pdf')
  await expect(manual).toContainText('Manual del empleado')
  await expect(manual).toContainText('Recursos humanos')
  await expect(manual).toContainText('Desactualizado')
  await expect(page.getByText(/1 documento está desactualizado/)).toBeVisible()

  await page.getByRole('button', { name: 'Reindexar desactualizados' }).click()
  await expect(manual).not.toContainText('Desactualizado')
  await expect(manual.getByText('Indexado')).toBeVisible()

  await page
    .getByRole('combobox', { name: 'Categoría', exact: true })
    .selectOption('Recursos humanos')
  await expect(page.getByRole('row')).toHaveCount(2) // cabecera y el manual

  await page.goto('/')
  await ask(page, '¿Cuál es el horario de oficina?')
  await expect(
    answers(page)
      .last()
      .getByRole('link', { name: 'Abrir fuente: [1] Manual del empleado · pág. 2' }),
  ).toBeVisible()
})

test('limita la búsqueda del chat a una categoría', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('button', { name: /Buscar en:/ }).click()
  const dialog = page.getByRole('dialog', { name: 'Elegir en qué documentos buscar' })
  await dialog
    .getByRole('group', { name: 'Categorías' })
    .getByRole('checkbox', { name: /Recursos humanos/ })
    .check()
  // Sus documentos quedan incluidos.
  await expect(dialog.getByRole('checkbox', { name: /Manual del empleado/ })).toBeChecked()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('button', { name: /Buscar en:/ })).toHaveText(
    /Buscar en: 1 categoría/,
  )

  await ask(page, '¿Cuál es el horario de oficina?')
  const answer = answers(page).last()
  await expect(answer).toContainText('Buscado solo en: Recursos humanos')
  await expect(
    answer.getByRole('link', { name: 'Abrir fuente: [1] Manual del empleado · pág. 2' }),
  ).toBeVisible()

  // La limitación queda en la conversación.
  await page.reload()
  await expect(page.getByRole('list', { name: 'Se busca solo en' })).toContainText(
    'Recursos humanos',
  )
})

test('indexa Word y Excel y cita el apartado del documento de Word', async ({ page }) => {
  await openDocuments(page)

  await page
    .getByTestId('file-input')
    .setInputFiles([fixture('normas-teletrabajo.docx'), fixture('dietas.xlsx')])

  const word = row(page, 'normas-teletrabajo.docx')
  await expect(word.getByText('Indexado')).toBeVisible()
  await expect(word).toContainText('Normas de teletrabajo') // título de las propiedades
  await expect(word).toContainText('Word')
  const excel = row(page, 'dietas.xlsx')
  await expect(excel.getByText('Indexado')).toBeVisible()
  await expect(excel).toContainText('Excel')

  await page.goto('/')
  await ask(page, '¿Cuántos días se puede teletrabajar a la semana?')
  await expect(
    answers(page)
      .last()
      .getByRole('link', { name: 'Abrir fuente: [1] Normas de teletrabajo — Teletrabajo' }),
  ).toBeVisible()
})

test('sube un ZIP con varios documentos a una categoría', async ({ page }) => {
  await openDocuments(page)

  // Contiene politica-gastos.html (eliminado en un test anterior), una copia de la guía de
  // vacaciones, un fichero no admitido y basura de macOS (que se omite).
  await page
    .getByRole('combobox', { name: 'Categoría de los documentos que subas' })
    .selectOption('Recursos humanos')
  await page.getByTestId('file-input').setInputFiles(fixture('lote.zip'))

  const uploads = page.getByRole('region', { name: 'Subidas' })
  await expect(uploads.getByRole('listitem').filter({ hasText: /^lote\.zipZIP/ })).toContainText(
    'ZIP: 2 ficheros subidos, 1 con errores',
  )
  await expect(
    uploads.getByRole('listitem').filter({ hasText: 'lote.zip › politica-gastos.html' }),
  ).toContainText('Subido')
  await expect(
    uploads.getByRole('listitem').filter({ hasText: 'lote.zip › carpeta/copia guía.md' }),
  ).toContainText('Ya existía')
  await expect(
    uploads.getByRole('listitem').filter({ hasText: 'lote.zip › notas.txt' }),
  ).toContainText('Tipo de fichero no admitido')
  await expect(uploads.getByText(/__MACOSX/)).toHaveCount(0)

  const gastos = row(page, 'politica-gastos.html')
  await expect(gastos.getByText('Indexado')).toBeVisible()
  await expect(gastos).toContainText('Recursos humanos')
})

test('añade una página web por su dirección y la cita con un enlace a ella', async ({ page }) => {
  // Servidor web propio del test: el backend descarga de él.
  const html = `<html><head><title>Seguro de viaje | Intranet</title></head><body>
    <nav>Inicio · Recursos humanos · Contacto</nav>
    <main><h1>Seguro de viaje</h1>
    <p>El seguro de viaje internacional contratado por la empresa cubre la asistencia médica
    urgente, la repatriación y la pérdida de equipaje durante los viajes de trabajo.</p></main>
    <footer>Aviso legal · Cookies</footer></body></html>`
  let gone = false
  const server = http.createServer((_, response) => {
    if (gone) {
      response.writeHead(404)
      response.end()
      return
    }
    response.writeHead(200, { 'content-type': 'text/html; charset=utf-8' })
    response.end(html)
  })
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))
  const url = `http://127.0.0.1:${(server.address() as AddressInfo).port}/seguro-de-viaje`
  try {
    await openDocuments(page)
    await page.getByRole('textbox', { name: 'Direcciones web (una por línea)' }).fill(url)
    await page.getByRole('button', { name: 'Añadir direcciones' }).click()

    const uploads = page.getByRole('region', { name: 'Subidas' })
    await expect(uploads.getByRole('listitem').filter({ hasText: url })).toContainText('Subido')
    const row = page.getByRole('row', { name: /Seguro de viaje/ })
    await expect(row.getByText('Indexado')).toBeVisible()
    await expect(row).toContainText('Página web')
    await expect(row.getByRole('link', { name: `Abrir ${url} (en otra pestaña)` })).toHaveAttribute(
      'href',
      url,
    )
    await expect(row).toContainText('Comprobado:')

    // La página desaparece: «Comprobar ahora» lo anota (se borra tras varias veces).
    gone = true
    await row.getByRole('button', { name: 'Comprobar ahora: Seguro de viaje' }).click()
    await expect(row).toContainText('No encontrado en la última comprobación')
  } finally {
    server.close()
  }

  await page.goto('/')
  await ask(page, '¿Qué cubre el seguro de viaje internacional contratado por la empresa?')
  const link = answers(page)
    .last()
    .getByRole('region', { name: 'Fuentes' })
    .getByRole('link', { name: /Abrir fuente: \[\d\] Seguro de viaje/ })
  await expect(link).toHaveAttribute('href', url)
})
