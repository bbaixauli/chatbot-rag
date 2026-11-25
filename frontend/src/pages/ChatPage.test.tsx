import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { health, jsonResponse, mockApi, renderApp, sseResponse } from '../test/utils'

const msg = (id: number, role: 'user' | 'assistant', content: string) => ({
  id,
  role,
  content,
  created_at: '2026-01-01T00:00:00',
})

async function ask(text: string) {
  await userEvent.type(screen.getByLabelText('Escribe tu mensaje'), `${text}{Enter}`)
}

describe('ChatPage', () => {
  it('crea una conversación nueva, muestra la respuesta y la añade al historial', async () => {
    let created = false
    mockApi({
      'GET /health': health,
      'GET /conversations': () =>
        jsonResponse(created ? [{ id: 'n1', title: 'Hola', created_at: '', updated_at: '' }] : []),
      'POST /chat': (init) => {
        expect(JSON.parse(init!.body as string)).toEqual({ message: 'Hola', conversation_id: null })
        created = true
        return sseResponse([
          ['conversation', { id: 'n1', title: 'Hola', user_message_id: 1 }],
          ['token', { content: 'Respuesta ' }],
          ['token', { content: '**en negrita**' }],
          ['done', { message_id: 2 }],
        ])
      },
      'GET /conversations/n1/messages': () =>
        jsonResponse([msg(1, 'user', 'Hola'), msg(2, 'assistant', 'Respuesta **en negrita**')]),
    })
    renderApp()
    expect(await screen.findByText('¿En qué puedo ayudarte?')).toBeInTheDocument()

    await ask('Hola')

    const list = await screen.findByRole('list', { name: 'Mensajes' })
    expect(await within(list).findByText('en negrita')).toContainHTML('strong')
    await waitFor(() => expect(list.querySelectorAll('li')).toHaveLength(2))
    expect(within(list).getAllByText('Hola')).toHaveLength(1)
    expect(await screen.findByRole('link', { name: 'Hola' })).toHaveAttribute('href', '/c/n1')
  })

  it('muestra las fuentes durante el streaming y al recargar el histórico', async () => {
    const source = {
      number: 1,
      document_id: 'd1',
      filename: 'manual.pdf',
      page: 2,
      heading: null,
      snippet: 'Horario de oficina',
      cited: true,
    }
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => (release = resolve))
    mockApi({
      'GET /health': health,
      'GET /conversations': () => jsonResponse([]),
      'POST /chat': () =>
        sseResponse([
          ['conversation', { id: 's1', title: 'Horario', user_message_id: 1 }],
          ['token', { content: 'De 8 a 15 [1].' }],
          ['sources', { sources: [source] }],
          ['done', { message_id: 2 }],
        ]),
      'GET /conversations/s1/messages': async () => {
        await gate
        return jsonResponse([
          msg(1, 'user', 'Horario'),
          { ...msg(2, 'assistant', 'De 8 a 15 [1].'), sources: [source] },
        ])
      },
    })
    renderApp()
    await ask('Horario')

    const link = await screen.findByRole('link', { name: 'Abrir fuente: [1] manual.pdf · pág. 2' })
    expect(link.closest('li[data-role]')).toHaveAttribute('aria-busy', 'true')
    release()
    await waitFor(() =>
      expect(
        screen
          .getByRole('link', { name: 'Abrir fuente: [1] manual.pdf · pág. 2' })
          .closest('li[data-role]'),
      ).toHaveAttribute('aria-busy', 'false'),
    )
    expect(screen.getByRole('link', { name: '[1]' })).toHaveAttribute(
      'href',
      '/api/v1/documents/d1/file#page=2',
    )
  })

  it('carga el histórico y continúa una conversación existente', async () => {
    let messages = [msg(1, 'user', 'Primera'), msg(2, 'assistant', 'Uno')]
    mockApi({
      'GET /health': health,
      'GET /conversations': () => jsonResponse([]),
      'GET /conversations/c1/messages': () => jsonResponse(messages),
      'POST /chat': (init) => {
        expect(JSON.parse(init!.body as string).conversation_id).toBe('c1')
        messages = [...messages, msg(3, 'user', 'Segunda'), msg(4, 'assistant', 'Dos')]
        return sseResponse([
          ['conversation', { id: 'c1', title: 'Primera', user_message_id: 3 }],
          ['token', { content: 'Dos' }],
          ['done', { message_id: 4 }],
        ])
      },
    })
    renderApp('/c/c1')
    expect(await screen.findByText('Primera')).toBeInTheDocument()

    await ask('Segunda')

    const list = screen.getByRole('list', { name: 'Mensajes' })
    await waitFor(() => expect(list.querySelectorAll('li')).toHaveLength(4))
    expect(within(list).getByText('Dos')).toBeInTheDocument()
  })

  it('muestra el error del modelo y conserva la pregunta en el historial', async () => {
    mockApi({
      'GET /health': health,
      'GET /conversations': () => jsonResponse([]),
      'GET /conversations/c1/messages': () => jsonResponse([msg(1, 'user', 'hola')]),
      'POST /chat': () =>
        sseResponse([
          ['conversation', { id: 'c1', title: 'hola', user_message_id: 1 }],
          ['error', { detail: 'El modelo no está disponible. Inténtalo de nuevo.' }],
        ]),
    })
    renderApp('/c/c1')
    await screen.findByText('hola')
    await userEvent.type(screen.getByLabelText('Escribe tu mensaje'), 'hola{Enter}')

    expect(await screen.findByRole('alert')).toHaveTextContent('El modelo no está disponible')
  })

  it('no se queda con una carga de mensajes anterior a la respuesta', async () => {
    // Como un servidor real: cada carga devuelve lo guardado en el momento de pedirla,
    // y tarda en responder. La respuesta se guarda justo antes del evento `done`.
    let saved = false
    const sse = (event: string, data: unknown) =>
      new TextEncoder().encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
    mockApi({
      'GET /health': health,
      'GET /conversations': () => jsonResponse([]),
      'POST /chat': () =>
        new Response(
          new ReadableStream<Uint8Array>({
            async start(controller) {
              controller.enqueue(
                sse('conversation', { id: 'r1', title: 'Hola', user_message_id: 1 }),
              )
              controller.enqueue(sse('token', { content: 'Respuesta final' }))
              await new Promise((resolve) => setTimeout(resolve, 50))
              saved = true
              controller.enqueue(sse('done', { message_id: 2 }))
              controller.close()
            },
          }),
          { headers: { 'Content-Type': 'text/event-stream' } },
        ),
      'GET /conversations/r1/messages': async () => {
        const snapshot = saved
          ? [msg(1, 'user', 'Hola'), msg(2, 'assistant', 'Respuesta final')]
          : [msg(1, 'user', 'Hola')]
        await new Promise((resolve) => setTimeout(resolve, 100))
        return jsonResponse(snapshot)
      },
    })
    renderApp()
    await ask('Hola')

    const list = await screen.findByRole('list', { name: 'Mensajes' })
    await waitFor(() => expect(list.querySelectorAll('li[aria-busy="false"]')).toHaveLength(2))
    await new Promise((resolve) => setTimeout(resolve, 150))
    expect(within(list).getByText('Respuesta final')).toBeInTheDocument()
  })

  it('avisa si la respuesta se corta sin terminar', async () => {
    mockApi({
      'GET /health': health,
      'GET /conversations': () => jsonResponse([]),
      'GET /conversations/c1/messages': () => jsonResponse([msg(1, 'user', 'hola')]),
      'POST /chat': () =>
        sseResponse([
          ['conversation', { id: 'c1', title: 'hola', user_message_id: 1 }],
          ['token', { content: 'Resp' }],
        ]),
    })
    renderApp('/c/c1')
    await screen.findByText('hola')
    await ask('hola')

    expect(await screen.findByRole('alert')).toHaveTextContent('No se pudo enviar el mensaje')
  })

  it('muestra un error si falla el envío', async () => {
    mockApi({
      'GET /health': health,
      'GET /conversations': () => jsonResponse([]),
      'POST /chat': () => jsonResponse({}, 500),
    })
    renderApp()
    await ask('hola')

    expect(await screen.findByRole('alert')).toHaveTextContent('No se pudo enviar el mensaje')
    expect(screen.getByText('¿En qué puedo ayudarte?')).toBeInTheDocument()
  })

  it('avisa si se supera el límite de mensajes', async () => {
    mockApi({
      'GET /health': health,
      'GET /conversations': () => jsonResponse([]),
      'POST /chat': () => jsonResponse({ detail: 'Demasiadas peticiones' }, 429),
    })
    renderApp()
    await ask('hola')

    expect(await screen.findByRole('alert')).toHaveTextContent('muchos mensajes seguidos')
  })

  it('indica que la conversación no existe y desactiva el envío', async () => {
    mockApi({
      'GET /health': health,
      'GET /conversations': () => jsonResponse([]),
      'GET /conversations/zz/messages': () => jsonResponse({}, 404),
    })
    renderApp('/c/zz')

    expect(await screen.findByText('La conversación no existe.')).toBeInTheDocument()
    expect(screen.getByLabelText('Escribe tu mensaje')).toBeInTheDocument()
    await ask('hola')
    expect(screen.getByRole('button', { name: 'Enviar' })).toBeDisabled()
  })

  it('muestra error genérico si fallan los mensajes', async () => {
    mockApi({
      'GET /health': health,
      'GET /conversations': () => jsonResponse([]),
      'GET /conversations/c1/messages': () => jsonResponse({}, 500),
    })
    renderApp('/c/c1')
    expect(await screen.findByText('No se pudieron cargar los mensajes.')).toBeInTheDocument()
  })

  it('muestra "Pensando…" hasta que llega el primer fragmento', async () => {
    let release: () => void = () => {}
    mockApi({
      'GET /health': health,
      'GET /conversations': () => jsonResponse([]),
      'POST /chat': () =>
        new Promise<Response>((resolve) => {
          release = () =>
            resolve(sseResponse([['conversation', { id: 'p1', title: 'x', user_message_id: 1 }]]))
        }),
      'GET /conversations/p1/messages': () => jsonResponse([msg(1, 'user', 'x')]),
    })
    renderApp()
    await ask('x')

    expect(await screen.findByText('Pensando…')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Enviar' })).toBeDisabled()
    release()
    await waitFor(() => expect(screen.queryByText('Pensando…')).toBeNull())
  })

  describe('inicio configurado', () => {
    const config = {
      welcome_message: '¡Hola! Soy el asistente de **RR. HH.**',
      suggested_questions: ['¿Cuántos días de vacaciones tengo?', '¿Cómo pido gastos?'],
    }

    it('muestra el mensaje inicial y las sugerencias en una conversación nueva', async () => {
      mockApi({
        'GET /health': health,
        'GET /conversations': () => jsonResponse([]),
        'GET /chat/config': () => jsonResponse(config),
        'POST /chat': (init) => {
          expect(JSON.parse(init!.body as string).message).toBe('¿Cómo pido gastos? Urgente')
          return sseResponse([
            ['conversation', { id: 'w1', title: 'Gastos', user_message_id: 1 }],
            ['token', { content: 'Así' }],
            ['done', { message_id: 2 }],
          ])
        },
        'GET /conversations/w1/messages': () =>
          jsonResponse([msg(1, 'user', '¿Cómo pido gastos? Urgente'), msg(2, 'assistant', 'Así')]),
      })
      renderApp()

      const list = await screen.findByRole('list', { name: 'Mensajes' })
      expect(await within(list).findByText('RR. HH.')).toContainHTML('strong')
      expect(screen.queryByText('¿En qué puedo ayudarte?')).toBeNull()
      const suggestions = screen.getByRole('list', { name: 'Preguntas sugeridas' })

      await userEvent.click(within(suggestions).getByRole('button', { name: '¿Cómo pido gastos?' }))
      await ask(' Urgente')

      await waitFor(() => expect(list.querySelectorAll('li[data-role]')).toHaveLength(3))
      expect(list.querySelector('li[data-role]')).toHaveTextContent('Soy el asistente')
      // Con la conversación empezada ya no se ofrecen sugerencias.
      expect(screen.queryByRole('list', { name: 'Preguntas sugeridas' })).toBeNull()
    })

    it('muestra el mensaje inicial también al reabrir una conversación, sin sugerencias', async () => {
      mockApi({
        'GET /health': health,
        'GET /conversations': () => jsonResponse([]),
        'GET /chat/config': () => jsonResponse(config),
        'GET /conversations/c1/messages': () =>
          jsonResponse([msg(1, 'user', 'Primera'), msg(2, 'assistant', 'Uno')]),
      })
      renderApp('/c/c1')

      const list = await screen.findByRole('list', { name: 'Mensajes' })
      await waitFor(() => expect(list.querySelectorAll('li[data-role]')).toHaveLength(3))
      expect(list.querySelector('li[data-role]')).toHaveAttribute('data-role', 'assistant')
      expect(list.querySelector('li[data-role]')).toHaveTextContent('Soy el asistente')
      expect(screen.queryByRole('list', { name: 'Preguntas sugeridas' })).toBeNull()
    })

    it('solo sugerencias: mantiene la portada vacía y muestra las preguntas', async () => {
      mockApi({
        'GET /health': health,
        'GET /conversations': () => jsonResponse([]),
        'GET /chat/config': () =>
          jsonResponse({ welcome_message: null, suggested_questions: ['Una'] }),
      })
      renderApp()

      expect(await screen.findByRole('button', { name: 'Una' })).toBeInTheDocument()
      expect(screen.getByText('¿En qué puedo ayudarte?')).toBeInTheDocument()
    })
  })
  it('limita la búsqueda de una conversación nueva a los documentos elegidos', async () => {
    const scope = { documents: [{ id: 'd1', filename: 'manual.pdf' }], source: 'selector' }
    let created = false
    mockApi({
      'GET /health': health,
      'GET /conversations': () =>
        jsonResponse(
          created
            ? [{ id: 'n1', title: 'Horario', created_at: '', updated_at: '', document_ids: ['d1'] }]
            : [],
        ),
      'GET /documents': () =>
        jsonResponse([
          { id: 'd1', filename: 'manual.pdf', title: 'manual.pdf', kind: 'pdf', category: null },
        ]),
      'POST /chat': (init) => {
        expect(JSON.parse(init!.body as string)).toEqual({
          message: 'Horario',
          conversation_id: null,
          document_ids: ['d1'],
        })
        created = true
        return sseResponse([
          ['conversation', { id: 'n1', title: 'Horario', user_message_id: 1 }],
          ['scope', scope],
          ['token', { content: 'De 8 a 15.' }],
          ['done', { message_id: 2 }],
        ])
      },
      'GET /conversations/n1/messages': () =>
        jsonResponse([msg(1, 'user', 'Horario'), { ...msg(2, 'assistant', 'De 8 a 15.'), scope }]),
    })
    renderApp()

    await userEvent.click(await screen.findByRole('button', { name: /Buscar en:/ }))
    await userEvent.click(await screen.findByLabelText('manual.pdf'))
    await userEvent.click(screen.getByRole('button', { name: 'Cerrar' }))
    await ask('Horario')

    const list = await screen.findByRole('list', { name: 'Mensajes' })
    expect(await within(list).findByText(/Buscado solo en: manual\.pdf/)).toBeInTheDocument()
    await waitFor(() => expect(list.querySelectorAll('li')).toHaveLength(2))
    // La limitación queda guardada en la conversación.
    expect(
      await screen.findByRole('button', { name: /Buscar en: 1 documento/ }),
    ).toBeInTheDocument()
  })

  it('cambia en qué documentos busca una conversación existente', async () => {
    const sent: unknown[] = []
    mockApi({
      'GET /health': health,
      'GET /conversations': () =>
        jsonResponse([
          { id: 'c1', title: 'Normas', created_at: '', updated_at: '', document_ids: ['d1'] },
        ]),
      'GET /conversations/c1/messages': () => jsonResponse([]),
      'GET /documents': () =>
        jsonResponse([
          { id: 'd1', filename: 'manual.pdf', title: 'manual.pdf', kind: 'pdf', category: null },
        ]),
      'PATCH /conversations/c1': (init) => {
        sent.push(JSON.parse(init!.body as string))
        return jsonResponse({
          id: 'c1',
          title: 'Normas',
          created_at: '',
          updated_at: '',
          document_ids: [],
        })
      },
    })
    renderApp('/c/c1')

    const chips = await screen.findByRole('list', { name: 'Se busca solo en' })
    expect(await within(chips).findByText('manual.pdf')).toBeInTheDocument()
    await userEvent.click(
      screen.getByRole('button', { name: 'Buscar también fuera de manual.pdf' }),
    )

    await waitFor(() => expect(sent).toEqual([{ document_ids: [] }]))
    expect(
      screen.getByRole('button', { name: /Buscar en: Todos los documentos/ }),
    ).toBeInTheDocument()
  })

  it('indica cuando la pregunta nombra un documento', async () => {
    const scope = {
      documents: [{ id: 'd1', filename: 'NOR-013.pdf', title: 'Control de acceso' }],
      source: 'detected',
    }
    mockApi({
      'GET /health': health,
      'GET /conversations': () => jsonResponse([]),
      'POST /chat': () =>
        sseResponse([
          ['conversation', { id: 'n2', title: 'NOR-013', user_message_id: 1 }],
          ['scope', scope],
          ['token', { content: 'Resumen.' }],
          ['done', { message_id: 2 }],
        ]),
      'GET /conversations/n2/messages': () =>
        jsonResponse([
          msg(1, 'user', 'Resume el NOR-013'),
          { ...msg(2, 'assistant', 'Resumen.'), scope },
        ]),
    })
    renderApp()

    await ask('Resume el NOR-013')

    const list = await screen.findByRole('list', { name: 'Mensajes' })
    expect(
      await within(list).findByText(
        /Buscado solo en: Control de acceso \(nombrado en la pregunta\)/,
      ),
    ).toBeInTheDocument()
  })
})
