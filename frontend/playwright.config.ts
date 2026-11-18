import { defineConfig, devices } from '@playwright/test'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { ADMIN_PASSWORD } from './e2e/constants.ts'

// Puertos distintos a los de desarrollo para no chocar con `npm run dev`.
const API_PORT = 8001
const WEB_PORT = 5174
const dataDir = mkdtempSync(join(tmpdir(), 'chatbot-e2e-'))

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI ? [['html', { open: 'never' }], ['list']] : 'list',
  use: {
    baseURL: `http://127.0.0.1:${WEB_PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
      testIgnore: /chat-start\.spec\.ts/,
    },
    {
      // Cambia la configuración global del chat (mensaje inicial): se ejecuta después del
      // resto para no alterar los demás tests, que comparten la base de datos.
      name: 'chromium-config-global',
      use: { ...devices['Desktop Chrome'] },
      testMatch: /chat-start\.spec\.ts/,
      dependencies: ['chromium'],
    },
  ],
  webServer: [
    {
      command: `uv run alembic upgrade head && uv run uvicorn app.main:app --host 127.0.0.1 --port ${API_PORT}`,
      cwd: '../backend',
      url: `http://127.0.0.1:${API_PORT}/api/v1/health`,
      env: {
        CHATBOT_ENVIRONMENT: 'test',
        CHATBOT_DATA_DIR: dataDir,
        // Modelo simulado: respuestas deterministas sin depender del servidor de IA.
        CHATBOT_LLM_PROVIDER: 'fake',
        CHATBOT_EMBEDDING_PROVIDER: 'fake',
        CHATBOT_ADMIN_PASSWORD: ADMIN_PASSWORD,
      },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: `npm run dev -- --host 127.0.0.1 --port ${WEB_PORT} --strictPort`,
      url: `http://127.0.0.1:${WEB_PORT}`,
      env: { API_PROXY_TARGET: `http://127.0.0.1:${API_PORT}` },
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
})
