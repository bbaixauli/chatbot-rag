// Textos de la interfaz centralizados para facilitar una futura traducción.
export const t = {
  appName: 'Chatbot RAG',
  health: {
    checking: 'Comprobando conexión…',
    ok: 'Servicio disponible',
    degraded: 'Servicio degradado',
    offline: 'Sin conexión con el servidor',
  },
  sidebar: {
    label: 'Historial de conversaciones',
    newChat: 'Nueva conversación',
    empty: 'Aún no hay conversaciones.',
    loadError: 'No se pudo cargar el historial.',
    rename: 'Renombrar',
    renameInput: 'Nuevo título',
    delete: 'Eliminar',
    confirmDelete: (title: string) => `¿Eliminar la conversación «${title}»?`,
  },
  chat: {
    emptyTitle: '¿En qué puedo ayudarte?',
    emptyHint: 'Escribe una pregunta para empezar una conversación.',
    messages: 'Mensajes',
    you: 'Tú',
    assistant: 'Asistente',
    inputLabel: 'Escribe tu mensaje',
    inputPlaceholder: 'Escribe tu mensaje… (Mayús+Intro para nueva línea)',
    send: 'Enviar',
    thinking: 'Pensando…',
    loadError: 'No se pudieron cargar los mensajes.',
    sendError: 'No se pudo enviar el mensaje. Inténtalo de nuevo.',
    notFound: 'La conversación no existe.',
  },
} as const
