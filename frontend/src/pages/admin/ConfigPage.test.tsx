import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import type {
  AppConfig,
  ConfigChanges,
  ConfigGroup,
  ConfigGroupName,
  ConfigSaveResult,
} from '../../api/config'
import { health, jsonResponse, mockApi, renderApp } from '../../test/utils'

const group = (values: ConfigGroup['values'], patch: Partial<ConfigGroup> = {}): ConfigGroup => ({
  values,
  secret_fields: Object.entries(values)
    .filter(([, v]) => typeof v === 'object' && v !== null)
    .map(([k]) => k),
  reindex_fields: [],
  updated_by: 'env',
  updated_at: '2026-11-20T10:00:00Z',
  ...patch,
})

function appConfig(): AppConfig {
  return {
    groups: {
      llm: group({
        provider: 'openai',
        base_url: 'http://ia:4000/v1',
        api_key: { configured: true, unreadable: false },
        model: 'llama3.1',
        temperature: 0.2,
        timeout_seconds: 120,
        max_retries: 2,
      }),
      embeddings: group(
        {
          provider: 'openai',
          base_url: null,
          api_key: { configured: false, unreadable: false },
          model: 'bge-m3',
          batch_size: 32,
        },
        { reindex_fields: ['provider', 'model', 'base_url'] },
      ),
      indexing: group(
        {
          upload_max_mb: 50,
          chunk_size_chars: 3000,
          chunk_overlap_chars: 400,
          strip_repeated_lines: true,
          repeated_line_min_ratio: 0.5,
          clean_toc_leaders: true,
          context_prefix: true,
        },
        {
          reindex_fields: [
            'chunk_size_chars',
            'chunk_overlap_chars',
            'strip_repeated_lines',
            'repeated_line_min_ratio',
            'clean_toc_leaders',
            'context_prefix',
          ],
        },
      ),
      rag: group(
        {
          enabled: true,
          top_k: 5,
          min_score: 0.3,
          max_context_chars: 12000,
          condense_question: true,
          condense_history_messages: 6,
          toc_score_factor: 0.8,
          scoped_min_score: 0,
          detect_document_names: true,
          neighbor_chunks: 0,
          full_document_max_chars: 0,
        },
        { updated_by: 'ana', updated_at: '2026-11-20T12:30:00Z' },
      ),
      chat: group({
        history_max_messages: 20,
        max_message_length: 8000,
        welcome_max_length: 2000,
        suggested_question_max_length: 300,
        system_prompt_max_length: 20000,
      }),
      limits: group({ chat_per_minute: 20, login_per_minute: 5 }),
      users: group({
        auth_mode: 'none',
        ldap_url: 'ldap://localhost:389',
        ldap_start_tls: false,
        ldap_ca_cert: null,
        ldap_timeout_seconds: 10,
        ldap_bind_dn: null,
        ldap_bind_password: { configured: false, unreadable: false },
        ldap_user_base_dn: '',
        ldap_user_filter: '(uid={username})',
        ldap_user_id_attribute: 'uid',
        ldap_display_name_attribute: 'cn',
        ldap_admin_group_dn: null,
        ldap_auditor_group_dn: null,
        ldap_nested_groups: false,
      }),
      audit: group({
        enabled: true,
        schedule: 'daily',
        daily_time: '02:00',
        use_llm: true,
        llm_model: null,
        max_per_run: 500,
        llm_history_days: 7,
        retention_days: 365,
        notify_enabled: false,
        notify_min_severity: 'medium',
        notify_to: null,
        smtp_host: null,
        smtp_port: 587,
        smtp_security: 'starttls',
        smtp_username: null,
        smtp_password: { configured: false, unreadable: false },
        smtp_from: null,
      }),
    },
    providers: { llm: ['openai', 'fake'], embeddings: ['openai', 'local', 'fake'] },
  }
}

interface Sent {
  group: string
  body: ConfigChanges
}

function configApi(overrides: Parameters<typeof mockApi>[0] = {}) {
  const config = appConfig()
  const sent: Sent[] = []
  const api = mockApi({
    'GET /health': health,
    'GET /admin/session': () =>
      jsonResponse({ authenticated: true, username: 'admin', enabled: true }),
    'GET /admin/config': () => jsonResponse(config),
    'PUT /admin/config/*': (init, url) => {
      const name = url.split('/').pop() as ConfigGroupName
      const body = JSON.parse(init!.body as string) as ConfigChanges
      sent.push({ group: name, body })
      const values = { ...config.groups[name].values }
      for (const [key, value] of Object.entries(body)) {
        if (key !== 'clear_secrets' && !config.groups[name].secret_fields.includes(key)) {
          values[key] = value as string | number | boolean | null
        }
      }
      const result: ConfigSaveResult = {
        group: name,
        config: { ...config.groups[name], values, updated_by: 'admin' },
        requires_reindex: false,
        warnings: [],
      }
      return jsonResponse(result)
    },
    ...overrides,
  })
  return { api, sent, config }
}

const section = async (title: string) => within(await screen.findByRole('region', { name: title }))

describe('ConfigPage', () => {
  it('se accede desde la navegación y muestra los grupos', async () => {
    configApi()
    renderApp('/admin/prompt')
    await userEvent.click(await screen.findByRole('link', { name: 'Configuración' }))

    const llm = await section('Modelo de lenguaje')
    expect(llm.getByLabelText('Modelo')).toHaveValue('llama3.1')
    expect(llm.getByLabelText('Temperatura')).toHaveValue(0.2)
    expect(llm.getByLabelText('Proveedor')).toHaveValue('openai')
    // El secreto no se muestra: solo si hay uno guardado.
    expect(llm.getByLabelText('Clave de la API')).toHaveValue('')
    expect(llm.getByText(/Hay una clave guardada/)).toBeInTheDocument()
    expect(llm.getByText('Valores iniciales tomados del fichero .env.')).toBeInTheDocument()
    const rag = await section('Recuperación')
    expect(rag.getByLabelText('Usar los documentos para responder')).toBeChecked()
    expect(rag.getByText(/Guardado por ana/)).toBeInTheDocument()
    expect((await section('Indexación')).getAllByText(/tendrás que reindexar/).length).toBe(6)
    expect(document.title).toMatch(/^Configuración/)
  })

  it('muestra el umbral de líneas repetidas solo si se quitan', async () => {
    configApi()
    renderApp('/admin/config')

    const indexing = await section('Indexación')
    const ratio = 'Proporción de páginas en que se repiten (0-1)'
    expect(indexing.getByLabelText(ratio)).toHaveValue(0.5)
    await userEvent.click(
      indexing.getByLabelText('Quitar encabezados y pies repetidos en las páginas de los PDF'),
    )
    expect(indexing.queryByLabelText(ratio)).not.toBeInTheDocument()
  })

  it('guarda solo el grupo editado', async () => {
    const { sent } = configApi()
    renderApp('/admin/config')
    const rag = await section('Recuperación')
    const save = rag.getByRole('button', { name: 'Guardar' })
    expect(save).toBeDisabled()

    await userEvent.clear(rag.getByLabelText('Fragmentos por pregunta (top-k)'))
    await userEvent.type(rag.getByLabelText('Fragmentos por pregunta (top-k)'), '8')
    await userEvent.click(rag.getByLabelText(/Reformular las preguntas/))
    await userEvent.click(save)

    expect(await rag.findByText('Cambios guardados.')).toBeInTheDocument()
    expect(sent).toEqual([
      {
        group: 'rag',
        body: {
          enabled: true,
          top_k: 8,
          min_score: 0.3,
          max_context_chars: 12000,
          condense_question: false,
          condense_history_messages: 6,
          toc_score_factor: 0.8,
          scoped_min_score: 0,
          detect_document_names: true,
          neighbor_chunks: 0,
          full_document_max_chars: 0,
        },
      },
    ])
    expect(rag.getByLabelText('Fragmentos por pregunta (top-k)')).toHaveValue(8)
    expect(save).toBeDisabled()
  })

  it('valida los campos antes de enviar', async () => {
    const { sent } = configApi()
    renderApp('/admin/config')
    const rag = await section('Recuperación')
    const minScore = rag.getByLabelText('Puntuación mínima (0-1)')

    await userEvent.clear(minScore)
    await userEvent.type(minScore, '1.5')
    await userEvent.click(rag.getByRole('button', { name: 'Guardar' }))

    expect(await rag.findByText('Debe estar entre 0 y 1.')).toBeInTheDocument()
    expect(minScore).toHaveAttribute('aria-invalid', 'true')
    expect(rag.getByRole('alert')).toHaveTextContent('Revisa los campos marcados.')
    const topK = rag.getByLabelText('Fragmentos por pregunta (top-k)')
    await userEvent.clear(topK)
    expect(await rag.findByText('Obligatorio.')).toBeInTheDocument()
    await userEvent.type(topK, '2.5')
    expect(await rag.findByText('Debe ser un número entero.')).toBeInTheDocument()
    expect(sent).toEqual([])
  })

  it('cambia, conserva o quita los secretos', async () => {
    const { sent } = configApi()
    renderApp('/admin/config')
    const llm = await section('Modelo de lenguaje')

    await userEvent.type(llm.getByLabelText('Clave de la API'), 'sk-nueva')
    await userEvent.click(llm.getByRole('button', { name: 'Guardar' }))
    await llm.findByText('Cambios guardados.')
    expect(sent[0].body.api_key).toBe('sk-nueva')
    expect(llm.getByLabelText('Clave de la API')).toHaveValue('')

    await userEvent.clear(llm.getByLabelText('Modelo'))
    await userEvent.type(llm.getByLabelText('Modelo'), 'otro')
    await userEvent.click(llm.getByRole('button', { name: 'Guardar' }))
    await waitFor(() => expect(sent).toHaveLength(2))
    expect(sent[1].body).not.toHaveProperty('api_key')

    await userEvent.click(llm.getByLabelText('Quitar la clave guardada'))
    expect(llm.getByLabelText('Clave de la API')).toBeDisabled()
    await userEvent.click(llm.getByRole('button', { name: 'Guardar' }))
    await waitFor(() => expect(sent).toHaveLength(3))
    expect(sent[2].body.clear_secrets).toEqual(['api_key'])
  })

  it('oculta la URL y la clave con proveedores sin servidor', async () => {
    const { sent } = configApi()
    renderApp('/admin/config')
    const embeddings = await section('Embeddings')
    expect(embeddings.getByLabelText('URL del servidor')).toHaveValue('')
    expect(embeddings.getByText(/Sin clave propia se usa la del modelo/)).toBeInTheDocument()

    await userEvent.selectOptions(embeddings.getByLabelText('Proveedor'), 'local')
    expect(embeddings.queryByLabelText('URL del servidor')).toBeNull()
    expect(embeddings.queryByLabelText('Clave de la API')).toBeNull()
    await userEvent.click(embeddings.getByRole('button', { name: 'Guardar' }))

    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0].body).toEqual({ provider: 'local', model: 'bge-m3', batch_size: 32 })
  })

  it('ofrece reindexar si el cambio lo requiere', async () => {
    let reindexed = false
    configApi({
      'PUT /admin/config/indexing': () =>
        jsonResponse({
          group: 'indexing',
          config: appConfig().groups.indexing,
          requires_reindex: true,
          warnings: ['Los documentos se indexaron con la configuración anterior.'],
        }),
      'POST /admin/documents/reindex': () => {
        reindexed = true
        return jsonResponse({ scheduled: 3 }, 202)
      },
    })
    renderApp('/admin/config')
    const indexing = await section('Indexación')

    await userEvent.clear(indexing.getByLabelText('Tamaño de fragmento (caracteres)'))
    await userEvent.type(indexing.getByLabelText('Tamaño de fragmento (caracteres)'), '1500')
    await userEvent.click(indexing.getByRole('button', { name: 'Guardar' }))

    expect(
      await indexing.findByText('Los documentos se indexaron con la configuración anterior.'),
    ).toBeInTheDocument()
    await userEvent.click(indexing.getByRole('button', { name: 'Reindexar todo ahora' }))
    expect(await indexing.findByText(/Reindexación iniciada \(3 documentos\)/)).toBeInTheDocument()
    expect(reindexed).toBe(true)
  })

  it('muestra el motivo de un error al guardar', async () => {
    configApi({
      'PUT /admin/config/embeddings': () =>
        jsonResponse({ detail: 'Hay 2 documentos pendientes de indexar' }, 409),
    })
    renderApp('/admin/config')
    const embeddings = await section('Embeddings')

    await userEvent.clear(embeddings.getByLabelText('Modelo'))
    await userEvent.type(embeddings.getByLabelText('Modelo'), 'otro')
    await userEvent.click(embeddings.getByRole('button', { name: 'Guardar' }))

    expect(await embeddings.findByRole('alert')).toHaveTextContent(
      'Hay 2 documentos pendientes de indexar',
    )
  })

  it('prueba la conexión con los valores del formulario', async () => {
    const tested: ConfigChanges[] = []
    configApi({
      'POST /admin/config/llm/test': (init) => {
        tested.push(JSON.parse(init!.body as string) as ConfigChanges)
        return jsonResponse({ ok: false, detail: 'No se pudo conectar con el modelo.' })
      },
      'POST /admin/config/embeddings/test': () =>
        jsonResponse({ ok: true, detail: 'Vectores de 1024 dimensiones' }),
    })
    renderApp('/admin/config')
    const llm = await section('Modelo de lenguaje')

    await userEvent.clear(llm.getByLabelText('URL del servidor'))
    await userEvent.type(llm.getByLabelText('URL del servidor'), 'http://otro/v1')
    await userEvent.click(llm.getByRole('button', { name: 'Probar conexión' }))

    expect(await llm.findByText('No se pudo conectar con el modelo.')).toHaveClass('text-error')
    expect(tested[0].base_url).toBe('http://otro/v1')
    const embeddings = await section('Embeddings')
    await userEvent.click(embeddings.getByRole('button', { name: 'Probar conexión' }))
    expect(await embeddings.findByText('Vectores de 1024 dimensiones')).toHaveClass('text-exito')
    const indexing = await section('Indexación')
    expect(indexing.queryByRole('button', { name: 'Probar conexión' })).toBeNull()
  })

  it('avisa si un secreto guardado no se puede descifrar', async () => {
    const config = appConfig()
    config.groups.llm.values.api_key = { configured: true, unreadable: true }
    configApi({ 'GET /admin/config': () => jsonResponse(config) })
    renderApp('/admin/config')

    expect(
      (await section('Modelo de lenguaje')).getByText(/no se puede descifrar/),
    ).toBeInTheDocument()
  })

  it('muestra los proveedores disponibles y conserva el guardado', async () => {
    const config = appConfig()
    config.groups.llm.values.provider = 'fake'
    config.providers.llm = ['openai']
    configApi({ 'GET /admin/config': () => jsonResponse(config) })
    renderApp('/admin/config')
    const select = (await section('Modelo de lenguaje')).getByLabelText('Proveedor')

    expect(select).toHaveValue('fake')
    expect(
      within(select)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['Simulado (solo pruebas)', 'Servidor compatible con la API de OpenAI'])
  })

  it('configura el acceso con LDAP', async () => {
    const tested: ConfigChanges[] = []
    const { sent } = configApi({
      'POST /admin/config/users/test': (init) => {
        tested.push(JSON.parse(init!.body as string) as ConfigChanges)
        return jsonResponse({ ok: true, detail: 'Usuario «ana» válido; es administrador' })
      },
    })
    renderApp('/admin/config')
    const users = await section('Usuarios')
    // Sin LDAP solo se elige el modo.
    expect(users.queryByLabelText('URL del directorio')).toBeNull()
    expect(users.queryByText('Probar con un usuario (opcional)')).toBeNull()

    await userEvent.selectOptions(users.getByLabelText('Acceso al chat'), 'ldap')
    await userEvent.clear(users.getByLabelText('URL del directorio'))
    await userEvent.type(users.getByLabelText('URL del directorio'), 'ldaps://dc:636')
    await userEvent.click(users.getByRole('button', { name: 'Guardar' }))
    expect(await users.findByText('Obligatorio.')).toBeInTheDocument()
    expect(sent).toEqual([])

    await userEvent.type(users.getByLabelText('Base de búsqueda de usuarios'), 'ou=people,dc=x')
    await userEvent.type(users.getByLabelText('Contraseña de la cuenta de servicio'), 'clave')
    await userEvent.type(users.getByLabelText('Usuario de prueba'), 'ana')
    await userEvent.type(users.getByLabelText('Contraseña de prueba'), 'secreta')
    await userEvent.click(users.getByRole('button', { name: 'Probar conexión' }))
    expect(await users.findByText('Usuario «ana» válido; es administrador')).toBeInTheDocument()
    expect(tested[0]).toMatchObject({
      auth_mode: 'ldap',
      ldap_url: 'ldaps://dc:636',
      ldap_bind_password: 'clave',
      test_username: 'ana',
      test_password: 'secreta',
    })

    await userEvent.click(users.getByRole('button', { name: 'Guardar' }))
    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0].body).toMatchObject({
      auth_mode: 'ldap',
      ldap_user_base_dn: 'ou=people,dc=x',
      ldap_ca_cert: null,
      ldap_bind_dn: null,
      ldap_nested_groups: false,
    })
    expect(sent[0].body).not.toHaveProperty('test_username')
  })

  it('muestra un error si no se puede cargar', async () => {
    configApi({ 'GET /admin/config': () => jsonResponse({ detail: 'x' }, 500) })
    renderApp('/admin/config')

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'No se pudo cargar la configuración.',
    )
  })
})
