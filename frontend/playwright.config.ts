import { defineConfig, devices } from '@playwright/test'
import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { ADMIN_PASSWORD } from './e2e/constants.ts'

// Puertos distintos a los de desarrollo para no chocar con `npm run dev`.
const API_PORT = 8001
const WEB_PORT = 5174
const dataDir = mkdtempSync(join(tmpdir(), 'chatbot-e2e-'))
// Segunda instancia con LDAP (acceso anónimo). El directorio no existe: el acceso anónimo
// y la cuenta local de administración no lo consultan.
const LDAP_API_PORT = 8002
const LDAP_WEB_PORT = 5175
const ldapDataDir = mkdtempSync(join(tmpdir(), 'chatbot-e2e-ldap-'))

const backendEnv = (dir: string) => ({
  CHATBOT_ENVIRONMENT: 'test',
  CHATBOT_DATA_DIR: dir,
  // Modelo simulado: respuestas deterministas sin depender del servidor de IA.
  CHATBOT_LLM_PROVIDER: 'fake',
  CHATBOT_EMBEDDING_PROVIDER: 'fake',
  CHATBOT_ADMIN_PASSWORD: ADMIN_PASSWORD,
  // Todos los tests comparten IP: sin límite de peticiones.
  CHATBOT_RATE_LIMIT_CHAT_PER_MINUTE: '0',
  CHATBOT_RATE_LIMIT_LOGIN_PER_MINUTE: '0',
})

const backend = (port: number, env: Record<string, string>) => ({
  command: `uv run alembic upgrade head && uv run uvicorn app.main:app --host 127.0.0.1 --port ${port}`,
  cwd: '../backend',
  url: `http://127.0.0.1:${port}/api/v1/health`,
  env,
  reuseExistingServer: !process.env.CI,
  timeout: 120_000,
})

const frontend = (port: number, apiPort: number) => ({
  command: `npm run dev -- --host 127.0.0.1 --port ${port} --strictPort`,
  url: `http://127.0.0.1:${port}`,
  env: { API_PROXY_TARGET: `http://127.0.0.1:${apiPort}` },
  reuseExistingServer: !process.env.CI,
  timeout: 120_000,
})

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
      testIgnore: /(chat-start|private-sessions|appearance|config|ldap-.*)\.spec\.ts/,
    },
    {
      // Cambia la configuración global del chat (mensaje inicial): se ejecuta después del
      // resto para no alterar los demás tests, que comparten la base de datos.
      name: 'chromium-config-global',
      use: { ...devices['Desktop Chrome'] },
      testMatch: /chat-start\.spec\.ts/,
      dependencies: ['chromium'],
    },
    {
      // Sesiones privadas: cambian cómo acceden todos a la instancia principal. Van al final,
      // tras el resto de proyectos de esa instancia, y dejan el ajuste desactivado.
      name: 'chromium-private-sessions',
      use: { ...devices['Desktop Chrome'] },
      testMatch: /private-sessions\.spec\.ts/,
      dependencies: ['chromium-config-global'],
    },
    {
      // Apariencia: cambia colores y nombre de toda la instancia principal. Va la última
      // y restablece los valores por defecto al terminar.
      name: 'chromium-appearance',
      use: { ...devices['Desktop Chrome'] },
      testMatch: /appearance\.spec\.ts/,
      dependencies: ['chromium-private-sessions'],
    },
    {
      // Configuración de la aplicación (límites, modelos): cambia la de toda la instancia
      // principal. Va después de todos y restablece los valores que toca.
      name: 'chromium-app-config',
      use: { ...devices['Desktop Chrome'] },
      testMatch: /config\.spec\.ts/,
      dependencies: ['chromium-appearance'],
    },
    {
      // Instancia con LDAP (ver arriba): acceso anónimo.
      name: 'chromium-ldap',
      use: { ...devices['Desktop Chrome'], baseURL: `http://127.0.0.1:${LDAP_WEB_PORT}` },
      testMatch: /ldap-.*\.spec\.ts/,
    },
  ],
  webServer: [
    backend(API_PORT, backendEnv(dataDir)),
    frontend(WEB_PORT, API_PORT),
    backend(LDAP_API_PORT, {
      ...backendEnv(ldapDataDir),
      CHATBOT_AUTH_MODE: 'ldap',
      CHATBOT_LDAP_URL: 'ldap://127.0.0.1:1',
      CHATBOT_LDAP_USER_BASE_DN: 'ou=people,dc=e2e',
      CHATBOT_LDAP_TIMEOUT_SECONDS: '1',
    }),
    frontend(LDAP_WEB_PORT, LDAP_API_PORT),
  ],
})
