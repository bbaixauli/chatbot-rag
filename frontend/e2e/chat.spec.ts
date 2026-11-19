import { expect, test, type Page } from '@playwright/test'

// Cada test usa textos únicos porque todos comparten la misma base de datos.
const unique = (label: string) => `${label} ${Date.now()}-${Math.floor(Math.random() * 1e4)}`

const history = (page: Page) =>
  page.getByRole('navigation', { name: 'Historial de conversaciones' })
const messages = (page: Page) => page.getByRole('list', { name: 'Mensajes' })

async function ask(page: Page, text: string) {
  await page.getByLabel('Escribe tu mensaje').fill(text)
  await page.getByLabel('Escribe tu mensaje').press('Enter')
}

async function waitForAnswer(page: Page, question: string) {
  // El modelo simulado responde «Respuesta simulada a: **<pregunta>**».
  await expect(
    messages(page).locator('li[data-role="assistant"]').filter({ hasText: question }),
  ).toBeVisible()
  await expect(messages(page).locator('li[aria-busy="true"]')).toHaveCount(0)
}

test('nueva conversación: respuesta en streaming, historial y persistencia', async ({ page }) => {
  const question = unique('¿Qué es un RAG?')
  await page.goto('/')
  await expect(page.getByRole('heading', { name: '¿En qué puedo ayudarte?' })).toBeVisible()

  await ask(page, question)
  await waitForAnswer(page, question)

  await expect(page).toHaveURL(/\/c\/[0-9a-f-]{36}$/)
  const answer = messages(page).locator('li[data-role="assistant"]').last()
  await expect(answer).toContainText('Respuesta simulada a:')
  await expect(answer.locator('strong')).toHaveText(question)
  await expect(messages(page).locator('li[data-role="user"]')).toHaveText([`Tú: ${question}`])

  const link = history(page).getByRole('link', { name: question })
  await expect(link).toBeVisible()
  await expect(link).toHaveAttribute('aria-current', 'page')

  await page.reload()
  await expect(messages(page).locator('li')).toHaveCount(2)
  await expect(messages(page)).toContainText(question)
})

test('continúa una conversación existente desde el historial', async ({ page }) => {
  const first = unique('Primera pregunta')
  const second = unique('Segunda pregunta')
  await page.goto('/')
  await ask(page, first)
  await waitForAnswer(page, first)

  await history(page).getByRole('button', { name: 'Nueva conversación' }).click()
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole('heading', { name: '¿En qué puedo ayudarte?' })).toBeVisible()

  await history(page).getByRole('link', { name: first }).click()
  await expect(messages(page).locator('li')).toHaveCount(2)
  await ask(page, second)
  await waitForAnswer(page, second)

  await expect(messages(page).locator('li')).toHaveCount(4)
  await expect(history(page).getByRole('link', { name: first })).toHaveCount(1)
})

test('renombra y elimina conversaciones', async ({ page }) => {
  const question = unique('Para renombrar')
  const renamed = unique('Renombrada')
  await page.goto('/')
  await ask(page, question)
  await waitForAnswer(page, question)

  const item = history(page).getByRole('listitem').filter({ hasText: question })
  await item.hover()
  await item.getByRole('button', { name: `Renombrar: ${question}` }).click()
  const input = history(page).getByLabel('Nuevo título')
  await input.fill(renamed)
  await input.press('Enter')
  await expect(history(page).getByRole('link', { name: renamed })).toBeVisible()

  page.once('dialog', (dialog) => {
    expect(dialog.message()).toBe(`¿Eliminar la conversación «${renamed}»?`)
    void dialog.accept()
  })
  const renamedItem = history(page).getByRole('listitem').filter({ hasText: renamed })
  await renamedItem.hover()
  await renamedItem.getByRole('button', { name: `Eliminar: ${renamed}` }).click()

  await expect(history(page).getByRole('link', { name: renamed })).toHaveCount(0)
  await expect(page).toHaveURL(/\/$/)
})

test('Mayús+Intro crea saltos de línea y una URL inexistente muestra aviso', async ({ page }) => {
  await page.goto('/c/00000000-0000-0000-0000-000000000000')
  await expect(page.getByText('La conversación no existe.')).toBeVisible()

  await page.goto('/')
  const input = page.getByLabel('Escribe tu mensaje')
  await input.fill('línea 1')
  await input.press('Shift+Enter')
  await input.pressSequentially('línea 2')
  await expect(input).toHaveValue('línea 1\nlínea 2')
  await expect(page.getByRole('button', { name: 'Enviar' })).toBeEnabled()
})
