import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'

// Instancia con LDAP: pantalla de inicio de sesión de usuarios (ver accessibility.spec.ts).
for (const viewport of [
  { width: 1280, height: 800 },
  { width: 320, height: 640 },
]) {
  test(`inicio de sesión accesible a ${viewport.width} px`, async ({ page }) => {
    await page.setViewportSize(viewport)
    await page.goto('/login')
    await expect(page.getByRole('heading', { level: 1, name: 'Iniciar sesión' })).toBeVisible()
    await expect(page).toHaveTitle('Iniciar sesión – Chatbot RAG')

    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze()
    const serious = results.violations
      .filter((v) => v.impact === 'serious' || v.impact === 'critical')
      .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(', ')}`)
    expect(serious).toEqual([])
    const overflow = await page.evaluate<number>(
      'document.documentElement.scrollWidth - document.documentElement.clientWidth',
    )
    expect(overflow).toBeLessThanOrEqual(0)
  })
}
