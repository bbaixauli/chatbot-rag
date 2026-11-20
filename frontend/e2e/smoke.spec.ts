import { expect, test } from '@playwright/test'

test('la página carga y conecta con el backend real', async ({ page }) => {
  await page.goto('/')

  await expect(page).toHaveTitle('Nueva conversación – Chatbot RAG')
  await expect(page.getByRole('banner').getByRole('link', { name: 'Chatbot RAG' })).toBeVisible()
  await expect(page.getByRole('status')).toHaveText('Servicio disponible')
  await expect(page.getByRole('status')).toHaveAttribute('data-state', 'ok')
})

test('muestra sin conexión cuando el backend no responde', async ({ page }) => {
  await page.route('**/api/v1/health', (route) => route.abort())
  await page.goto('/')

  await expect(page.getByRole('status')).toHaveText('Sin conexión con el servidor')
})
