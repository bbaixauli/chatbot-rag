import type { PromptTrim, SearchedIn } from '../api/audit'

// Textos de la interfaz centralizados para facilitar una futura traducción.
export const t = {
  appName: 'Chatbot RAG',
  a11y: {
    skipToContent: 'Saltar al contenido principal',
  },
  footer: {
    version: (version: string) => `versión ${version}`,
    accessibility: 'Declaración de accesibilidad',
    support: 'Soporte',
  },
  health: {
    checking: 'Comprobando conexión…',
    ok: 'Servicio disponible',
    degraded: 'Servicio degradado',
    // Fallan el índice o los embeddings, pero no la base de datos (#98).
    documentation: 'Documentación no disponible',
    offline: 'Sin conexión con el servidor',
  },
  nav: {
    admin: 'Administración',
    menu: 'Menú',
    backToChat: 'Volver al chat',
    prompt: 'Prompt de sistema',
    documents: 'Documentos',
    chatStart: 'Mensaje inicial',
    appearance: 'Apariencia',
    dashboard: 'Cuadro de mando',
    audit: 'Auditoría',
    config: 'Configuración',
    logout: 'Cerrar sesión',
    anonymousSession:
      'Acceso anónimo: al cerrar sesión no podrás volver a ver estas conversaciones.',
    privateSession: 'Sesión privada',
    privateSessionHint:
      'Solo tú ves estas conversaciones. Al terminar la sesión no podrás volver a verlas.',
    endPrivateSession: 'Terminar sesión',
    confirmEndPrivateSession:
      '¿Terminar la sesión privada? Empezarás una nueva y no podrás volver a ver estas conversaciones.',
    endPrivateSessionError: 'No se pudo terminar la sesión. Inténtalo de nuevo.',
  },
  privateSession: {
    starting: 'Preparando tu sesión privada…',
    error: 'No se pudo iniciar la sesión privada.',
    retry: 'Reintentar',
  },
  login: {
    title: 'Acceso de administración',
    username: 'Usuario',
    password: 'Contraseña',
    submit: 'Entrar',
    invalid: 'Usuario o contraseña incorrectos.',
    error: 'No se pudo iniciar sesión. Inténtalo de nuevo.',
    tooManyAttempts: 'Demasiados intentos. Espera un minuto antes de volver a intentarlo.',
    forbidden: 'Tu usuario no tiene permisos de administración.',
    unavailable: 'No se puede validar el usuario en este momento. Inténtalo más tarde.',
    adminLdapHint: 'Usa tu usuario corporativo (debe pertenecer al grupo de administración).',
    disabled:
      'La administración no está configurada. Define CHATBOT_ADMIN_PASSWORD en el servidor.',
  },
  userLogin: {
    title: 'Iniciar sesión',
    hint: 'Accede con tu usuario y contraseña corporativos.',
    anonymous: 'Acceder de forma anónima',
    anonymousHint: 'Sin cuenta: tus conversaciones solo estarán disponibles durante esta sesión.',
    anonymousError: 'No se pudo acceder de forma anónima. Inténtalo de nuevo.',
  },
  prompt: {
    title: 'Prompt de sistema',
    description:
      'Instrucciones que recibe el modelo al inicio de cada conversación. Los cambios se aplican a los mensajes nuevos.',
    label: 'Prompt de sistema',
    usingDefault: 'Se está usando el prompt predeterminado del servidor.',
    characters: (n: number) => `${n.toLocaleString('es-ES')} caracteres`,
    save: 'Guardar',
    saving: 'Guardando…',
    saved: 'Prompt guardado.',
    saveError: 'No se pudo guardar el prompt.',
    loadError: 'No se pudo cargar el prompt.',
    history: 'Historial de versiones',
    noHistory: 'Todavía no hay versiones guardadas.',
    current: 'Vigente',
    restore: 'Restaurar',
    restored: 'Versión restaurada.',
    confirmRestore: (id: number) => `¿Restaurar la versión ${id}? Se creará una nueva versión.`,
    by: (author: string, date: string) => `${author} · ${date}`,
  },
  chatStart: {
    title: 'Mensaje inicial del chat',
    description:
      'Personaliza el comienzo de las conversaciones. Se aplica a todos los usuarios y no se envía al modelo.',
    welcomeLabel: 'Mensaje inicial',
    welcomeHint:
      'Si se define, aparece como primer mensaje del asistente en cada conversación. Admite Markdown.',
    questionsTitle: 'Preguntas sugeridas',
    questionsHint:
      'Hasta 5 preguntas que se muestran sobre el campo de texto al empezar una conversación. Al pulsar una, se copia en el campo para poder editarla antes de enviarla.',
    questionLabel: (n: number) => `Pregunta sugerida ${n}`,
    save: 'Guardar',
    saving: 'Guardando…',
    saved: 'Configuración guardada.',
    saveError: 'No se pudo guardar la configuración.',
    loadError: 'No se pudo cargar la configuración.',
    characters: (n: number, max: number) =>
      `${n.toLocaleString('es-ES')} / ${max.toLocaleString('es-ES')} caracteres`,
    accessTitle: 'Acceso al chat',
    anonymousLabel: 'Permitir el acceso anónimo',
    anonymousHint:
      'Muestra «Acceder de forma anónima» en la pantalla de inicio de sesión. Cada acceso anónimo solo ve sus propias conversaciones y deja de verlas al cerrar sesión; se conservan en el servidor para auditoría.',
    anonymousWarning:
      'Cualquiera que llegue a la aplicación podrá usar el chat y abrir los documentos citados en las respuestas. Al desactivarlo, las sesiones anónimas abiertas se cierran.',
    accessSaved: 'Acceso actualizado.',
    accessSaveError: 'No se pudo cambiar el acceso.',
    accessLoadError: 'No se pudo cargar la configuración de acceso.',
    privateSessionsLabel: 'Sesiones privadas y temporales',
    privateSessionsHint:
      'Cada visitante tiene su propia sesión: solo ve sus conversaciones y deja de verlas al terminarla. Mientras esté activado, el historial compartido no se muestra (no se borra). Las conversaciones se conservan en el servidor para auditoría.',
    privateSessionsWarning:
      'Al desactivarlo se vuelve al historial compartido y las sesiones privadas abiertas se cierran.',
  },
  config: {
    title: 'Configuración',
    description:
      'Modelos de IA, indexación de documentos, recuperación y límites. Los cambios se aplican al guardar, sin reiniciar.',
    loadError: 'No se pudo cargar la configuración.',
    save: 'Guardar',
    saving: 'Guardando…',
    saved: 'Cambios guardados.',
    noChanges: 'No había cambios que guardar.',
    saveError: 'No se pudo guardar.',
    test: 'Probar conexión',
    testing: 'Probando…',
    testError: 'No se pudo hacer la prueba.',
    fromEnv: 'Valores iniciales tomados del fichero .env.',
    updated: (by: string, at: string) =>
      `Guardado por ${by} el ${new Date(at).toLocaleString('es-ES', {
        dateStyle: 'short',
        timeStyle: 'short',
      })}.`,
    reindexNote: 'Si cambias estos campos tendrás que reindexar los documentos.',
    reindexNow: 'Reindexar todo ahora',
    reindexing: 'Reindexando…',
    reindexStarted: (n: number) =>
      `Reindexación iniciada (${n} ${n === 1 ? 'documento' : 'documentos'}). Sigue el progreso en Documentos.`,
    reindexError: 'No se pudo iniciar la reindexación.',
    invalid: 'Revisa los campos marcados.',
    required: 'Obligatorio.',
    range: (min?: number, max?: number) =>
      min !== undefined && max !== undefined
        ? `Debe estar entre ${min} y ${max}.`
        : min !== undefined
          ? `Debe ser como mínimo ${min}.`
          : `Debe ser como máximo ${max}.`,
    integer: 'Debe ser un número entero.',
    secret: {
      configured: 'Hay una clave guardada. Escribe otra para cambiarla.',
      empty: 'No hay ninguna clave guardada.',
      unreadable:
        'La clave guardada no se puede descifrar (ha cambiado CHATBOT_SESSION_SECRET): se usa la del fichero .env. Vuelve a introducirla.',
      clear: 'Quitar la clave guardada',
    },
    testUser: {
      legend: 'Probar con un usuario (opcional)',
      hint: 'Al probar la conexión, comprueba también que este usuario puede iniciar sesión y si es administrador. No se guarda.',
      username: 'Usuario de prueba',
      password: 'Contraseña de prueba',
    },
    options: {
      none: 'Sin inicio de sesión (chat anónimo)',
      ldap: 'Usuarios del directorio (LDAP o Active Directory)',
      hourly: 'Cada hora',
      daily: 'A diario',
      low: 'Baja o superior',
      medium: 'Media o superior',
      high: 'Solo alta',
      starttls: 'STARTTLS (puerto 587)',
      ssl: 'SSL/TLS (puerto 465)',
      plain: 'Sin cifrar (solo en la red interna)',
    } as Record<string, string>,
    providers: {
      openai: 'Servidor compatible con la API de OpenAI',
      local: 'Local (sentence-transformers en este servidor)',
      fake: 'Simulado (solo pruebas)',
    } as Record<string, string>,
    groups: {
      llm: {
        title: 'Modelo de lenguaje',
        description: 'Servidor de IA que genera las respuestas (LiteLLM, vLLM, Ollama…).',
        fields: {
          provider: 'Proveedor',
          base_url: 'URL del servidor',
          api_key: 'Clave de la API',
          model: 'Modelo',
          temperature: 'Temperatura',
          timeout_seconds: 'Tiempo máximo de espera (segundos)',
          max_retries: 'Reintentos',
          context_window_tokens: 'Ventana de contexto (tokens)',
          response_reserve_tokens: 'Reserva para la respuesta (tokens)',
        },
        hints: {
          base_url: 'Por ejemplo, http://servidor-ia:4000/v1',
          temperature: 'De 0 (respuestas más previsibles) a 2 (más creativas).',
          context_window_tokens:
            'La configurada en el servidor del modelo: num_ctx en Ollama, --max-model-len en vLLM, max_input_tokens en LiteLLM. Si el prompt no cabe, se ajusta antes de enviarlo (el documento completo pasa a fragmentos y se quitan el historial antiguo y los fragmentos menos parecidos). 0: no se comprueba, y algunos servidores recortan el prompt sin avisar.',
          response_reserve_tokens: 'Parte de la ventana que se deja libre para la respuesta.',
        },
      },
      embeddings: {
        title: 'Embeddings',
        description: 'Modelo que convierte documentos y preguntas en vectores para buscarlos.',
        fields: {
          provider: 'Proveedor',
          base_url: 'URL del servidor',
          api_key: 'Clave de la API',
          model: 'Modelo',
          batch_size: 'Fragmentos por petición',
        },
        hints: {
          base_url: 'Vacía: la del modelo de lenguaje.',
          api_key: 'Sin clave propia se usa la del modelo de lenguaje.',
          model: 'Con LiteLLM, el model_name de su configuración.',
          batch_size: 'Si el servidor da resultados distintos en lotes, bájalo.',
        },
      },
      indexing: {
        title: 'Indexación',
        description:
          'Subida de documentos, tamaño de los fragmentos en que se dividen y limpieza del texto antes de indexarlo.',
        fields: {
          upload_max_mb: 'Tamaño máximo de un fichero (MB)',
          chunk_size_chars: 'Tamaño de fragmento (caracteres)',
          chunk_overlap_chars: 'Solapamiento entre fragmentos (caracteres)',
          strip_repeated_lines: 'Quitar encabezados y pies repetidos en las páginas de los PDF',
          repeated_line_min_ratio: 'Proporción de páginas en que se repiten (0-1)',
          clean_toc_leaders: 'Quitar los puntos de relleno de los índices',
          context_prefix:
            'Añadir el título, el fichero, la categoría y la sección del documento a cada fragmento',
          pdf_headings: 'Detectar los apartados numerados de los PDF',
        },
        hints: {
          upload_max_mb: 'El proxy (CHATBOT_MAX_BODY_SIZE) debe admitir peticiones mayores.',
          strip_repeated_lines:
            'Sellos de firma electrónica, cabeceras, «Página 3 de 27»… Restan precisión a la búsqueda.',
          repeated_line_min_ratio:
            'Se quitan las líneas que aparecen en al menos esa parte de las páginas (mínimo 3).',
          context_prefix:
            'Solo para buscar: ayuda con preguntas que nombran el documento. El texto mostrado no cambia.',
          pdf_headings:
            'Encabezados como «4.2 Altas de usuarios»: las fuentes indican el apartado y se puede preguntar por él. Cambiarlo solo obliga a reindexar los PDF.',
        },
      },
      rag: {
        title: 'Recuperación',
        description: 'Cómo se buscan los fragmentos que se envían al modelo con cada pregunta.',
        fields: {
          enabled: 'Usar los documentos para responder',
          top_k: 'Fragmentos por pregunta (top-k)',
          min_score: 'Puntuación mínima (0-1)',
          max_context_chars: 'Máximo de contexto (caracteres)',
          neighbor_chunks: 'Fragmentos vecinos que se añaden (0-3)',
          full_document_max_chars: 'Documento completo hasta (caracteres)',
          condense_question: 'Reformular las preguntas de seguimiento antes de buscar',
          condense_history_messages: 'Mensajes previos para reformular',
          toc_score_factor: 'Peso de los fragmentos de índice (0-1)',
          detect_document_names: 'Buscar solo en los documentos que nombre la pregunta',
          scoped_min_score: 'Puntuación mínima al buscar en documentos concretos (0-1)',
          locate_sections: 'Añadir el apartado que cite la pregunta',
        },
        hints: {
          min_score: 'Similitud mínima para usar un fragmento; depende del modelo de embeddings.',
          max_context_chars: 'Protege la ventana de contexto del modelo.',
          full_document_max_chars:
            'Si se busca en un solo documento y cabe, se envía entero (0 = nunca). Debe caber en la ventana de contexto del modelo (unos 3,5 caracteres por token); si no cabe, se envían sus fragmentos.',
          neighbor_chunks:
            'Anteriores y posteriores a cada fragmento recuperado, dentro del máximo de contexto. Útil si la respuesta queda partida entre fragmentos.',
          condense_question: 'Cuesta una llamada extra al modelo cuando hay historial.',
          toc_score_factor:
            'Multiplica su puntuación para que no desplacen al contenido; 1 = sin cambios.',
          detect_document_names:
            'Por su código («NOR-013») o su título completo. El chat indica en qué documentos busca.',
          scoped_min_score:
            'Cuando se limita la búsqueda a unos documentos; con 0 basta con el top-k.',
          locate_sections:
            '«El apartado 4.2», «el capítulo V», «la sección de altas»: si se busca en unos documentos concretos, se envía el apartado completo.',
        },
      },
      chat: {
        title: 'Conversación',
        description: 'Historial que se envía al modelo y longitud máxima de los textos.',
        fields: {
          history_max_messages: 'Mensajes de historial enviados al modelo',
          max_message_length: 'Longitud máxima de una pregunta',
          welcome_max_length: 'Longitud máxima del mensaje inicial',
          suggested_question_max_length: 'Longitud máxima de una pregunta sugerida',
          system_prompt_max_length: 'Longitud máxima del prompt de sistema',
        },
        hints: {},
      },
      limits: {
        title: 'Límites de peticiones',
        description: 'Peticiones por IP y minuto (0 = sin límite).',
        fields: {
          chat_per_minute: 'Preguntas al chat por minuto',
          login_per_minute: 'Inicios de sesión por minuto',
        },
        hints: {},
      },
      users: {
        title: 'Usuarios',
        description:
          'Cómo se accede al chat. La cuenta local de administración del fichero .env sigue funcionando siempre.',
        fields: {
          auth_mode: 'Acceso al chat',
          ldap_url: 'URL del directorio',
          ldap_start_tls: 'Cifrar con StartTLS',
          ldap_ca_cert: 'Certificado de la CA (PEM)',
          ldap_timeout_seconds: 'Tiempo máximo de espera (segundos)',
          ldap_bind_dn: 'Cuenta de servicio (DN)',
          ldap_bind_password: 'Contraseña de la cuenta de servicio',
          ldap_user_base_dn: 'Base de búsqueda de usuarios',
          ldap_user_filter: 'Filtro de usuarios',
          ldap_user_id_attribute: 'Atributo del identificador',
          ldap_display_name_attribute: 'Atributo del nombre visible',
          ldap_admin_group_dn: 'Grupo de administración (DN)',
          ldap_auditor_group_dn: 'Grupo de auditores (DN)',
          ldap_nested_groups: 'Tener en cuenta grupos anidados (solo Active Directory)',
        },
        hints: {
          auth_mode:
            'Al guardar, se comprueba que el directorio responde. Las sesiones abiertas del modo anterior se cierran.',
          ldap_url: 'ldaps://servidor:636, o ldap://servidor:389 con StartTLS.',
          ldap_ca_cert:
            'Solo si la CA del directorio no está en el almacén del sistema ni en el fichero de CHATBOT_LDAP_CA_CERT_FILE. Tiene prioridad sobre ese fichero.',
          ldap_bind_dn: 'Vacía: búsqueda anónima.',
          ldap_user_filter:
            '{username} se sustituye por el usuario. Active Directory: (sAMAccountName={username})',
          ldap_user_id_attribute: 'OpenLDAP: uid. Active Directory: sAMAccountName.',
          ldap_admin_group_dn:
            'Sus miembros administran la aplicación. Vacío: solo la cuenta local.',
          ldap_auditor_group_dn:
            'Sus miembros ven la auditoría (y solo ellos, ni la cuenta local). Vacío: la ven los administradores.',
        },
      },
      audit: {
        title: 'Auditoría',
        description:
          'Revisión periódica de preguntas y respuestas en busca de usos maliciosos, respuestas inapropiadas o información sensible.',
        fields: {
          enabled: 'Revisión programada',
          schedule: 'Frecuencia',
          daily_time: 'Hora de la revisión diaria (hh:mm)',
          max_per_run: 'Preguntas por revisión',
          use_llm: 'Revisar también con el modelo',
          llm_model: 'Modelo revisor',
          llm_history_days: 'Antigüedad máxima para el modelo (días)',
          retention_days: 'Anonimizar pasados (días)',
          notify_enabled: 'Avisar por correo',
          notify_min_severity: 'Gravedad mínima para avisar',
          notify_to: 'Destinatarios',
          smtp_host: 'Servidor SMTP',
          smtp_port: 'Puerto',
          smtp_security: 'Seguridad de la conexión',
          smtp_username: 'Usuario SMTP',
          smtp_password: 'Contraseña SMTP',
          smtp_from: 'Remitente',
        },
        hints: {
          enabled:
            'Las reglas (datos sensibles, intentos de manipulación) se aplican siempre; también se puede revisar a mano desde Auditoría.',
          daily_time: 'Hora de Madrid, por ejemplo 02:00.',
          use_llm: 'Además de las reglas, el modelo clasifica cada pregunta y respuesta.',
          llm_model:
            'Vacío: el modelo del chat. Con LiteLLM puede ser otro, por ejemplo uno específico de seguridad.',
          llm_history_days:
            'Lo más antiguo solo pasa las reglas (evita miles de llamadas en la primera revisión).',
          retention_days:
            'El usuario pasa a un seudónimo y se borran la IP, el navegador y el nombre; los textos se conservan. 0: nunca.',
          notify_to: 'Una o varias direcciones separadas por comas.',
        },
      },
      web: {
        title: 'Documentos web',
        description:
          'Descarga de los documentos que se añaden por su dirección web y su actualización periódica.',
        fields: {
          timeout_seconds: 'Tiempo máximo de cada descarga (segundos)',
          allowed_domains: 'Dominios permitidos',
          refresh_enabled: 'Comprobar periódicamente si han cambiado',
          refresh_interval_hours: 'Cada cuántas horas',
          not_found_limit: 'Borrar tras no encontrarse (comprobaciones seguidas)',
        },
        hints: {
          refresh_enabled:
            'Si la página o el documento han cambiado, se actualiza y se reindexa. Solo se vuelve a indexar si cambia el contenido principal.',
          not_found_limit:
            'Solo cuentan las respuestas «no encontrado» (404 o 410): un fallo de red o del servidor no borra nada.',
          allowed_domains:
            'Separados por comas; cada uno admite también sus subdominios (empresa.es incluye intranet.empresa.es). Vacío: cualquiera.',
        },
      },
      tls: {
        title: 'Certificados',
        description:
          'Conexiones HTTPS con los servidores de los documentos web, del modelo de lenguaje y de los embeddings. El directorio LDAP tiene su propio certificado en Usuarios.',
        fields: {
          ca_certs: 'Certificados de confianza (PEM)',
          strict_x509: 'Comprobación X.509 estricta',
        },
        hints: {
          ca_certs:
            'Certificados de las CA propias de la organización (uno o varios, con sus líneas BEGIN y END CERTIFICATE). Se suman a las CA públicas.',
          strict_x509:
            'Exige que los certificados cumplan el RFC 5280. Desactívala si los de la organización no la superan (p. ej. les falta el Authority Key Identifier): se siguen comprobando la CA, la caducidad y el nombre del servidor.',
        },
      },
    },
  },
  documents: {
    title: 'Documentos',
    description:
      'Ficheros que el asistente consulta para responder. Se admiten PDF, Markdown, HTML, Word, PowerPoint, Excel y sus equivalentes de OpenDocument (LibreOffice), sueltos o en un ZIP.',
    stats: (documents: number, chunks: number) =>
      `${documents} ${documents === 1 ? 'documento' : 'documentos'} · ${chunks} ${
        chunks === 1 ? 'fragmento indexado' : 'fragmentos indexados'
      }`,
    model: (name: string) => `Modelo de embeddings: ${name}`,
    outdated: (n: number) =>
      n === 1
        ? '1 documento está desactualizado (se indexó con otro modelo de embeddings, otro troceado u otro título o categoría): pulsa «Reindexar desactualizados».'
        : `${n} documentos están desactualizados (se indexaron con otro modelo de embeddings, otro troceado u otro título o categoría): pulsa «Reindexar desactualizados».`,
    outdatedBadge: 'Desactualizado: hay que reindexarlo',
    reindexOutdated: 'Reindexar desactualizados',
    reindexAll: 'Reindexar todo',
    confirmReindexAll:
      '¿Reindexar todos los documentos? Es necesario si ha cambiado el modelo de embeddings. ' +
      'Hasta que termine, el chat responderá sin ellos.',
    dropzone:
      'Arrastra aquí ficheros PDF, Markdown, HTML, Word, PowerPoint, Excel u OpenDocument, o un ZIP con varios',
    or: 'o',
    select: 'Seleccionar ficheros',
    uploads: 'Subidas',
    uploading: 'Subiendo…',
    uploaded: 'Subido',
    archiveSummary: (uploaded: number, failed: number) =>
      `ZIP: ${uploaded} ${uploaded === 1 ? 'fichero subido' : 'ficheros subidos'}` +
      (failed ? `, ${failed} con errores` : ''),
    duplicate: 'Ya existía',
    uploadFailed: 'No se pudo subir',
    unsupported:
      'Tipo de fichero no admitido (los formatos antiguos .doc, .xls y .ppt hay que guardarlos como .docx, .xlsx o .pptx)',
    uploadOptions: 'Opciones de la subida',
    uploadCategory: 'Categoría de los documentos que subas',
    useFilename: 'Usar el nombre del fichero como título',
    titleHint:
      'Si no, el título se toma de los metadatos del fichero (o del título de la página o el primer encabezado); si no tiene uno válido, se usa el nombre del fichero.',
    noCategory: 'Sin categoría',
    filter: 'Categoría',
    allCategories: 'Todas',
    empty: 'Todavía no hay documentos.',
    emptyFilter: 'No hay documentos en esta categoría.',
    tableCaption: 'Documentos subidos',
    loadError: 'No se pudieron cargar los documentos.',
    columns: {
      name: 'Título',
      category: 'Categoría',
      kind: 'Tipo',
      size: 'Tamaño',
      status: 'Estado',
      chunks: 'Fragmentos',
      uploaded: 'Subido',
      actions: 'Acciones',
    },
    kinds: {
      pdf: 'PDF',
      markdown: 'Markdown',
      html: 'HTML',
      docx: 'Word',
      pptx: 'PowerPoint',
      xlsx: 'Excel',
      odt: 'Texto OpenDocument',
      odp: 'Presentación OpenDocument',
      ods: 'Hoja de cálculo OpenDocument',
      web: 'Página web',
    },
    status: {
      pending: 'Pendiente',
      processing: 'Procesando…',
      ready: 'Indexado',
      error: 'Error',
    },
    download: (name: string) => `Descargar ${name}`,
    openUrl: (url: string) => `Abrir ${url} (en otra pestaña)`,
    refresh: 'Comprobar ahora',
    checked: (at: string) => `Comprobado: ${at}`,
    changed: (at: string) => `Cambiado: ${at}`,
    notFound: (n: number) =>
      n === 1
        ? 'No encontrado en la última comprobación'
        : `No encontrado en las ${n} últimas comprobaciones`,
    urls: {
      label: 'Direcciones web (una por línea)',
      hint: 'De las páginas web se guarda su contenido principal (sin menús ni pies); los PDF y documentos de Office, tal cual. Las referencias del chat enlazan a la dirección original.',
      add: 'Añadir direcciones',
      adding: 'Descargando…',
      tooMany: (max: number) => `Como mucho ${max} direcciones de una vez.`,
    },
    edit: 'Editar',
    editing: (name: string) => `Editar «${name}»`,
    titleLabel: 'Título',
    fileLabel: (name: string) => `Fichero: ${name}`,
    titleFromFilename: 'Usar el nombre del fichero',
    categoryLabel: 'Categoría',
    save: 'Guardar',
    cancel: 'Cancelar',
    editHint:
      'El título se muestra en las referencias del chat. El título y la categoría ayudan a encontrar el documento: al cambiarlos, hay que reindexarlo.',
    reindex: 'Reindexar',
    delete: 'Eliminar',
    confirmDelete: (name: string) =>
      `¿Eliminar «${name}»? El asistente dejará de usarlo en sus respuestas.`,
    actionError: 'No se pudo completar la acción.',
  },
  categories: {
    title: 'Categorías',
    description:
      'Agrupan los documentos. Al renombrar o eliminar una categoría, sus documentos quedan desactualizados hasta que se reindexen.',
    newName: 'Nueva categoría',
    add: 'Añadir',
    empty: 'Todavía no hay categorías.',
    documents: (n: number) => (n === 1 ? '1 documento' : `${n} documentos`),
    rename: (name: string) => `Renombrar la categoría «${name}»`,
    renameShort: 'Renombrar',
    delete: (name: string) => `Eliminar la categoría «${name}»`,
    deleteShort: 'Eliminar',
    confirmDelete: (name: string) =>
      `¿Eliminar la categoría «${name}»? Sus documentos se conservan, sin categoría.`,
    save: 'Guardar',
    cancel: 'Cancelar',
    error: 'No se pudo guardar la categoría.',
  },
  audit: {
    title: 'Auditoría',
    description:
      'Avisos de posibles usos maliciosos, respuestas inapropiadas o información sensible, y registro completo de preguntas y respuestas.',
    views: 'Secciones de la auditoría',
    viewLabels: { alerts: 'Avisos', runs: 'Revisiones', log: 'Registro completo' },
    newAlertsBadge: (n: number) => (n === 1 ? 'aviso nuevo' : 'avisos nuevos'),
    running: (since: string) => `Revisión en curso desde ${since}…`,
    lastRun: (at: string) => `Última revisión: ${at} ·`,
    runAlerts: (n: number) => (n === 1 ? '1 aviso' : `${n} avisos`),
    neverRun: 'Todavía no se ha hecho ninguna revisión.',
    runNow: 'Revisar ahora',
    alreadyRunning: 'Ya hay una revisión en curso. Espera a que termine.',
    runError: 'No se pudo lanzar la revisión.',
    testEmail: 'Enviar correo de prueba',
    testEmailSent: 'Correo de prueba enviado.',
    testEmailNotConfigured:
      'Configura el servidor SMTP, el remitente y los destinatarios en Configuración → Auditoría.',
    testEmailError: 'No se pudo enviar el correo de prueba. Revisa la configuración SMTP.',
    runStatus: {
      running: 'en curso',
      ok: 'correcta',
      partial: 'con errores del modelo',
      failed: 'fallida',
    } as Record<string, string>,
    trigger: { scheduled: 'Programada', manual: 'Manual', cli: 'Línea de comandos' } as Record<
      string,
      string
    >,
    severity: { low: 'Baja', medium: 'Media', high: 'Alta' } as Record<string, string>,
    category: {
      malicious_user: 'Posible uso malicioso',
      inappropriate_response: 'Respuesta inapropiada',
      sensitive_information: 'Información sensible',
    } as Record<string, string>,
    source: { rule: 'Regla', model: 'Modelo revisor' } as Record<string, string>,
    alertStatus: { new: 'Nuevo', reviewed: 'Revisado', dismissed: 'Descartado' } as Record<
      string,
      string
    >,
    entryStatus: { ok: 'Respondida', error: 'Error', cancelled: 'Cancelada' } as Record<
      string,
      string
    >,
    all: 'Todos',
    filters: {
      status: 'Estado',
      category: 'Categoría',
      severity: 'Gravedad',
      user: 'Usuario (identificador o nombre)',
      ip: 'IP (o su comienzo)',
    },
    alertsTitle: 'Avisos',
    alertsPagination: 'Paginación de los avisos',
    noAlerts: 'No hay avisos con estos filtros.',
    loadError: 'No se pudo cargar la auditoría. Inténtalo de nuevo.',
    alertDate: (at: string) => `detectado el ${at}`,
    unknownUser: 'Usuario sin nombre',
    ip: (ip: string) => `IP ${ip}`,
    showConversation: 'Ver la pregunta y la respuesta',
    question: 'Pregunta',
    answer: 'Respuesta',
    statusLine: (status: string) => `Estado: ${status}`,
    reviewedBy: (who: string, when: string) => `por ${who} el ${when}`,
    note: 'Nota',
    markReviewed: 'Marcar como revisado',
    dismiss: 'Descartar',
    reopen: 'Volver a marcar como nuevo',
    updateError: 'No se pudo actualizar el aviso.',
    runsTitle: 'Últimas revisiones',
    runColumns: {
      startedAt: 'Inicio',
      trigger: 'Origen',
      status: 'Resultado',
      reviewed: 'Preguntas revisadas',
      alerts: 'Avisos',
      anonymized: 'Anonimizadas',
    },
    byModel: (n: string, model: string) => `${n} con el modelo ${model}`,
    modelErrors: (n: number) => (n === 1 ? '1 sin poder revisar' : `${n} sin poder revisar`),
    emailFailed: (error: string) => `No se pudo avisar por correo: ${error}`,
    logTitle: 'Registro completo',
    logHint:
      'Cada pregunta con su respuesta, usuario, IP y datos de la respuesta. Pasado el plazo de anonimización, el usuario aparece como seudónimo y sin IP.',
    logPagination: 'Paginación del registro',
    search: 'Buscar',
    noEntries: 'No hay preguntas con estos filtros.',
    logColumns: {
      askedAt: 'Fecha y hora',
      user: 'Usuario',
      ip: 'IP',
      question: 'Pregunta',
      details: 'Respuesta y detalles',
      status: 'Resultado',
      time: 'Tiempo',
    },
    details: 'Ver',
    meta: {
      model: 'Modelo',
      promptVersion: 'Versión del prompt',
      defaultPrompt: 'Predeterminado',
      searchQuery: 'Consulta de búsqueda',
      documents: 'Documentos mostrados',
      userAgent: 'Navegador',
      error: 'Motivo del error',
      promptSize: 'Tamaño del prompt',
      promptTrim: 'Recortes del prompt',
      retrievalFailed: 'Documentación',
      searchedIn: 'Búsqueda limitada a',
    },
    retrievalFailed: 'No se pudo consultar (falló la búsqueda)',
    everywhere: 'Todos los documentos',
    searchedIn: (where: SearchedIn) =>
      (where.categories.length
        ? `${where.categories.join(', ')} (${where.document_count} ${where.document_count === 1 ? 'documento' : 'documentos'})`
        : where.documents.join(', ')) +
      (where.source === 'detected' ? ' (nombrados en la pregunta)' : ''),
    promptTokens: (estimated: number, counted: number | null | undefined) =>
      `unos ${estimated} tokens` + (counted == null ? '' : ` (el servidor contó ${counted})`),
    trim: (trim: PromptTrim) =>
      [
        trim.full_document ? 'documento completo sustituido por fragmentos' : '',
        trim.sources ? `${trim.sources} ${trim.sources === 1 ? 'fragmento' : 'fragmentos'}` : '',
        trim.history
          ? `${trim.history} ${trim.history === 1 ? 'mensaje' : 'mensajes'} del historial`
          : '',
        trim.server ? 'recortado por el servidor' : '',
      ]
        .filter(Boolean)
        .join('; '),
  },
  dashboard: {
    title: 'Cuadro de mando',
    description:
      'Uso del asistente en el rango de fechas elegido. Las fechas y los días están en la hora de Madrid.',
    loadError: 'No se pudieron cargar las estadísticas. Inténtalo de nuevo.',
    range: 'Rango de fechas',
    presets: {
      today: 'Hoy',
      last7: 'Últimos 7 días',
      last30: 'Últimos 30 días',
      month: 'Este mes',
    },
    from: 'Desde',
    to: 'Hasta',
    apply: 'Aplicar rango',
    invalidRange: 'La fecha «Desde» debe ser anterior o igual a «Hasta».',
    figures: (from: string, to: string) => `Cifras del ${from} al ${to}`,
    sessions: 'Sesiones',
    users: 'Usuarios',
    usersHint: 'Cada acceso anónimo cuenta como un usuario; el historial compartido, como uno.',
    questions: 'Preguntas',
    avgResponse: 'Tiempo medio de respuesta',
    responseDetail: (median: string, firstToken: string) =>
      `Mediana ${median} · primer fragmento ${firstToken}`,
    answered: 'Respondidas',
    answeredDetail: (noInfo: string, errors: string, withoutSources: string, trimmed: string) =>
      `${noInfo} sin información · ${errors} con error · ${withoutSources} sin documentación encontrada` +
      (trimmed === '0' ? '' : ` · ${trimmed} con el prompt recortado`),
    activity: 'Actividad diaria',
    activityTable: 'Actividad diaria por día',
    showTable: 'Ver como tabla',
    showChart: 'Ver como gráfica',
    emptyActivity: 'No hay preguntas en este rango. Prueba con otro rango de fechas.',
    legend: 'Leyenda',
    series: { questions: 'Preguntas', sessions: 'Sesiones', users: 'Usuarios' },
    unit: (key: 'questions' | 'sessions' | 'users', n: number) =>
      ({
        questions: n === 1 ? 'pregunta' : 'preguntas',
        sessions: n === 1 ? 'sesión' : 'sesiones',
        users: n === 1 ? 'usuario' : 'usuarios',
      })[key],
    chartLabel: 'Gráfica de preguntas, sesiones y usuarios por día',
    chartHelp:
      'Usa las flechas izquierda y derecha para recorrer los días. Los mismos datos están en «Ver como tabla».',
    day: 'Día',
    questionsTitle: 'Preguntas y respuestas',
    questionsHint: 'De la más reciente a la más antigua. Pulsa una respuesta para verla completa.',
    questionsError: 'No se pudieron cargar las preguntas.',
    emptyQuestions: 'No hay preguntas en este rango. Prueba con otro rango de fechas.',
    exportCsv: 'Descargar CSV',
    exportXlsx: 'Descargar Excel',
    columns: {
      askedAt: 'Fecha y hora',
      question: 'Pregunta',
      answer: 'Respuesta',
      time: 'Tiempo de respuesta',
      result: 'Resultado',
      sources: 'Fuentes citadas',
    },
    results: {
      answered: 'Respondida',
      no_info: 'Sin información',
      error: 'Error',
      cancelled: 'Cancelada',
    },
    pagination: 'Paginación de las preguntas',
    showing: (first: string, last: string, total: string) =>
      `Mostrando ${first}–${last} de ${total}`,
    pageSize: 'Filas por página',
    previous: 'Anterior',
    next: 'Siguiente',
    noInfoTitle: 'Detección de respuestas sin información',
    noInfoHint:
      'Una respuesta cuenta como «sin información» si contiene alguna de estas frases (sin distinguir mayúsculas ni tildes). Al guardar se vuelven a clasificar todas las preguntas, también las anteriores.',
    noInfoLabel: 'Frases (una por línea)',
    noInfoSave: 'Guardar frases',
    noInfoDefaults: 'Restaurar las frases por defecto',
    noInfoSaved: 'Frases guardadas y preguntas reclasificadas.',
    noInfoSaveError: 'No se pudieron guardar las frases.',
    noInfoInvalid: 'Revisa las frases: como máximo 50, de hasta 200 caracteres cada una.',
    noInfoLoadError: 'No se pudieron cargar las frases.',
  },
  appearance: {
    title: 'Apariencia',
    description:
      'Colores, tipos de letra, logo y textos de la aplicación. Los cambios se aplican a todas las pantallas al guardar.',
    loadError: 'No se pudo cargar la apariencia.',
    identity: 'Identidad',
    appName: 'Nombre de la aplicación',
    organization: 'Organización',
    organizationHint: 'Se añade al título de las pestañas: «Página – Aplicación – Organización».',
    logoAlt: 'Texto alternativo del logo',
    logoAltHint:
      'Describe el logo para los lectores de pantalla (por ejemplo, «Escudo del Ayuntamiento»). Déjalo vacío si el logo es decorativo.',
    headerStyle: 'Cabecera',
    headerStyles: {
      clara: 'Clara (fondo y texto de la página)',
      marca: 'Del color de marca (texto sobre la marca)',
    },
    logo: 'Logo',
    logoHint:
      'Se muestra en la cabecera y en las pantallas de acceso, con 40 px de alto. Usa el fichero oficial, sin deformarlo ni recolorearlo. SVG, PNG o WebP; máximo 512 KB.',
    currentLogo: 'Logo actual',
    noLogo: 'No hay logo: se muestra solo el nombre de la aplicación.',
    uploadLogo: 'Subir un logo nuevo',
    deleteLogo: 'Quitar el logo',
    confirmDeleteLogo: '¿Quitar el logo? Se mostrará solo el nombre de la aplicación.',
    logoSaved: 'Logo actualizado.',
    logoError: 'No se pudo subir el logo.',
    colorsTitle: 'Colores',
    colorsHint:
      'Formato hexadecimal (#RRGGBB). Los contrastes se comprueban según WCAG 2.1 AA: 4,5:1 para texto y 3:1 para bordes de controles y foco.',
    groups: {
      brand: 'Marca',
      base: 'Texto, fondos y controles',
      states: 'Estados',
    },
    colors: {
      marca: 'Color de marca',
      'marca-oscuro': 'Marca oscuro (botón al pasar el ratón)',
      'sobre-marca': 'Texto sobre la marca',
      'marca-suave': 'Marca suave (opción activa)',
      enlace: 'Enlaces',
      texto: 'Texto',
      'texto-secundario': 'Texto secundario',
      fondo: 'Fondo',
      'fondo-alt': 'Fondo alternativo',
      'borde-control': 'Borde de campos y botones',
      borde: 'Separadores',
      foco: 'Anillo de foco',
      error: 'Error',
      'error-suave': 'Fondo de error',
      exito: 'Éxito',
      'exito-suave': 'Fondo de éxito',
      aviso: 'Aviso',
      'aviso-suave': 'Fondo de aviso',
      info: 'Información',
      'info-suave': 'Fondo de información',
      'cabecera (texto)': 'Texto de la cabecera',
      'cabecera (fondo)': 'Fondo de la cabecera',
    } as Record<string, string>,
    colorPicker: (name: string) => `Selector de color: ${name}`,
    colorFormat: 'Usa el formato #RRGGBB, por ejemplo #980046.',
    contrastOk: 'Todos los contrastes cumplen WCAG 2.1 AA.',
    contrastWarningsTitle: (n: number) =>
      n === 1
        ? 'Hay 1 combinación con contraste insuficiente (se puede guardar igualmente):'
        : `Hay ${n} combinaciones con contraste insuficiente (se puede guardar igualmente):`,
    contrastWarning: (fg: string, bg: string, ratio: number, required: number) =>
      `${fg} sobre ${bg}: ${ratio.toLocaleString('es-ES')}:1 (mínimo ${required.toLocaleString('es-ES')}:1)`,
    fonts: 'Tipos de letra',
    fontSans: 'Tipo de letra principal',
    fontMono: 'Tipo de letra monoespaciado (código)',
    fontSample: 'Así se verá el texto: Ayuntamiento, expedientes, 12.450,75 €',
    sansFonts: {
      sistema: 'Del sistema',
      trebuchet: 'Trebuchet MS',
      segoe: 'Segoe UI',
      arial: 'Arial',
      verdana: 'Verdana',
      georgia: 'Georgia (con serifa)',
    } as Record<string, string>,
    monoFonts: {
      sistema: 'Del sistema',
      consolas: 'Consolas',
      courier: 'Courier New',
    } as Record<string, string>,
    texts: 'Pie y avisos',
    footerUnit: 'Unidad responsable',
    footerAccessibility: 'Enlace a la declaración de accesibilidad',
    footerSupportText: 'Texto del canal de soporte',
    footerSupportUrl: 'Enlace del canal de soporte',
    urlHint: 'Dirección completa que empiece por https://',
    loginNotice: 'Aviso en las pantallas de acceso',
    loginNoticeHint:
      'Por ejemplo: «Sistema de uso exclusivo para fines municipales. La actividad queda registrada.»',
    aiNotice: 'Aviso de inteligencia artificial',
    aiNoticeHint: 'Se muestra bajo el campo de mensaje. Vacío: se usa el texto por defecto.',
    preview: 'Vista previa',
    previewText: 'Texto principal de la aplicación con un',
    previewLink: 'enlace',
    previewSecondary: 'Texto secundario: ayudas y metadatos.',
    previewPrimary: 'Guardar',
    previewSecondaryButton: 'Cancelar',
    previewDanger: 'Eliminar',
    previewActive: 'Opción activa',
    previewStates: {
      error: 'Error: no se pudo guardar.',
      exito: 'Guardado correctamente.',
      aviso: 'Aviso: revisa los datos.',
      info: 'Información de ayuda.',
    },
    save: 'Guardar apariencia',
    saving: 'Guardando…',
    saved: 'Apariencia guardada.',
    savedWithWarnings: (n: number) =>
      `Apariencia guardada con ${n === 1 ? '1 aviso' : `${n} avisos`} de contraste.`,
    saveError: 'No se pudo guardar la apariencia.',
    invalidColors: (names: string) => `Corrige el formato de estos colores: ${names}.`,
    reset: 'Restablecer valores por defecto',
    confirmReset:
      '¿Restablecer los valores por defecto? Se perderán los colores, tipos de letra y textos configurados (el logo se conserva).',
  },
  common: {
    loading: 'Cargando…',
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
    newConversation: 'Nueva conversación',
    conversation: 'Conversación',
    aiNotice:
      'Las respuestas las genera un sistema de inteligencia artificial y pueden contener errores. Comprueba la información importante en las fuentes citadas.',
    emptyTitle: '¿En qué puedo ayudarte?',
    emptyHint: 'Escribe una pregunta para empezar una conversación.',
    messages: 'Mensajes',
    you: 'Tú',
    assistant: 'Asistente',
    inputLabel: 'Escribe tu mensaje',
    inputPlaceholder: 'Escribe tu mensaje… (Mayús+Intro para nueva línea)',
    send: 'Enviar',
    suggestions: 'Preguntas sugeridas',
    scope: {
      label: 'Buscar en',
      all: 'Todos los documentos',
      some: (categories: number, documents: number) =>
        [
          categories && (categories === 1 ? '1 categoría' : `${categories} categorías`),
          documents && (documents === 1 ? '1 documento' : `${documents} documentos`),
        ]
          .filter(Boolean)
          .join(' y '),
      documentCount: (count: number) => (count === 1 ? '1 documento' : `${count} documentos`),
      categories: 'Categorías',
      documents: 'Documentos',
      categoriesHint: 'Las categorías no tienen límite: se busca en todos sus documentos.',
      choose: 'Elegir en qué documentos buscar',
      filter: 'Filtrar documentos',
      max: (max: number) => `Puedes elegir hasta ${max} documentos.`,
      clear: 'Buscar en todos',
      close: 'Cerrar',
      remove: (name: string) => `Buscar también fuera de ${name}`,
      selected: 'Se busca solo en',
      loading: 'Cargando documentos…',
      loadError: 'No se pudo cargar la lista de documentos.',
      empty: 'No hay documentos que coincidan.',
      none: 'Todavía no hay documentos.',
      saveError: 'No se pudo guardar en qué documentos buscar.',
      searchedIn: 'Buscado solo en',
      detected: '(nombrado en la pregunta)',
    },
    thinking: 'Pensando…',
    loadError: 'No se pudieron cargar los mensajes.',
    sendError: 'No se pudo enviar el mensaje. Inténtalo de nuevo.',
    rateLimited: 'Has enviado muchos mensajes seguidos. Espera un momento antes de continuar.',
    notFound: 'La conversación no existe.',
    sources: 'Fuentes',
    consultedSources: 'Fuentes consultadas',
    retrievalFailed:
      'No se ha podido consultar la documentación: esta respuesta puede no basarse en los documentos.',
    page: (n: number) => `pág. ${n}`,
    slide: (n: number) => `diap. ${n}`,
    deletedDocument: 'documento eliminado',
    openSource: (label: string) => `Abrir fuente: ${label}`,
  },
} as const
