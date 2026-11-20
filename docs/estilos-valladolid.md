# Configuración para el Ayuntamiento de Valladolid

Valores que hay que configurar en *Administración → Apariencia* para que la aplicación cumpla
el **Manual de estilos para aplicaciones internas del Ayuntamiento de Valladolid** (en adelante,
«el manual»). La aplicación ya cumple por sí misma los requisitos de estructura y accesibilidad
del manual (WCAG 2.1 AA); aquí solo se recoge lo que depende de la identidad municipal.

Por defecto la aplicación usa un tema genérico (azul y grises). Nada de lo que sigue viene
configurado de serie.

## 1. Identidad

| Campo | Valor | Referencia del manual |
|---|---|---|
| Nombre de la aplicación | El nombre del servicio, con mayúscula solo en la primera palabra (p. ej. «Asistente de normativa interna») | 2.6, 7.4 |
| Organización | `Ayuntamiento de Valladolid` | 4.1 (título «Página – Aplicación – Ayuntamiento de Valladolid») |
| Cabecera | **Del color de marca** | 4.1 (cabecera con fondo #980046) |
| Logo | Escudo oficial, versión en **negativo** (blanco), en SVG | 2.1, 2.3 |
| Texto alternativo del logo | `Ayuntamiento de Valladolid` | 1.1.1 de WCAG |

### Logo

- Descarga el escudo **en SVG** desde la
  [página de identidad corporativa](https://www.valladolid.es/es/temas/hacemos/identidad-corporativa).
  No se redibuja, deforma, recolorea ni se le aplican sombras (2.3).
- Sobre la cabecera granate se usa la **versión en negativo** (2.1). Si el identificador lleva
  texto, la versión en una sola línea es la adecuada para la cabecera (proporción horizontal).
- La aplicación lo muestra a **40 px de alto**, el mínimo que pide el manual para el
  identificador con texto (2.2), con un separador vertical y el nombre de la aplicación a la
  derecha (2.6).
- **No** se usan los logotipos «en tu corazón» ni «VA!» (1, 2.3).
- El escudo **no se incluye** en este repositorio: es un fichero oficial y se sube desde la
  administración en cada instalación.

## 2. Colores

Los valores de la columna «Valor» proceden de la tabla 3.1 del manual, salvo los marcados con
**(propuesto)**: el manual no los define y se han derivado de su paleta, comprobando el
contraste. Con todos ellos la página *Apariencia* indica «Todos los contrastes cumplen WCAG 2.1
AA».

| Campo en la administración | Token | Valor | Contraste |
|---|---|---|---|
| Color de marca | `--color-marca` | `#980046` | 8,67:1 sobre blanco (y del blanco sobre él) |
| Marca oscuro (botón al pasar el ratón) | `--color-marca-oscuro` | `#4E1036` | 14,47:1 |
| Texto sobre la marca | `--color-sobre-marca` | `#FFFFFF` | 8,67:1 sobre #980046 |
| Marca suave (opción activa) | `--color-marca-suave` | `#FBEFF4` | solo fondo |
| Enlaces | `--color-enlace` | `#7A0038` | 11,14:1 |
| Texto | `--color-texto` | `#1F1A1C` | 17,17:1 |
| Texto secundario | `--color-texto-secundario` | `#5C5458` | 7,33:1 |
| Fondo | `--color-fondo` | `#FFFFFF` | — |
| Fondo alternativo | `--color-fondo-alt` | `#F6F3F4` | solo fondo |
| Borde de campos y botones | `--color-borde-control` | `#8A8286` | 3,74:1 (mínimo 3:1) |
| Separadores | `--color-borde` | `#E4DDE0` **(propuesto)** | decorativo |
| Anillo de foco | `--color-foco` | `#005FCC` | 5,98:1 |
| Error | `--color-error` | `#B3261E` | 6,54:1 |
| Fondo de error | `--color-error-suave` | `#FCEFEE` **(propuesto)** | error sobre él: 5,83:1 |
| Éxito | `--color-exito` | `#1E6B3A` | 6,52:1 |
| Fondo de éxito | `--color-exito-suave` | `#EDF6F0` **(propuesto)** | éxito sobre él: 5,91:1 |
| Aviso | `--color-aviso` | `#8A5A00` | 5,93:1 |
| Fondo de aviso | `--color-aviso-suave` | `#FBF4E6` **(propuesto)** | aviso sobre él: 5,41:1 |
| Información | `--color-info` | `#0B5C8A` | 7,21:1 |
| Fondo de información | `--color-info-suave` | `#EAF3F9` **(propuesto)** | información sobre él: 6,42:1 |

Notas:

- **Foco en la cabecera.** El azul de foco sobre el granate solo alcanza 1,45:1. Por eso, sobre
  la cabecera, la aplicación dibuja el anillo de foco con el color del texto de la cabecera
  (blanco, 8,67:1). No hay que configurar nada.
- **Colores del escudo.** Los colores del escudo (#9D0015, #CB0015, #CC3300, #FFCC00,
  #FFFF00, #006633, #66CCFF, #666666) se reservan al escudo y **no** se usan en la interfaz
  (3, 3.2). En particular, los rojos del escudo no deben usarse para botones, porque se
  confundirían con los errores.
- **Modo oscuro.** La aplicación no lo ofrece, así que no hay que definir los valores oscuros
  del manual (3.1).

## 3. Tipos de letra

| Campo | Valor | Referencia |
|---|---|---|
| Tipo de letra principal | **Trebuchet MS** (`"Trebuchet MS", "Segoe UI", Arial, sans-serif`) | 2.5, 3.3 |
| Tipo de letra monoespaciado | **Consolas** (`Consolas, "Courier New", monospace`) | 3.3 |

Trebuchet MS está instalada en los puestos Windows municipales, así que no se descargan fuentes
web. Shelley Allegro BT no se usa en aplicaciones (2.5).

## 4. Pie y avisos

| Campo | Valor | Referencia |
|---|---|---|
| Unidad responsable | La unidad que gestiona el servicio, tal como figura en el organigrama (p. ej. «Servicio de Informática») | 4.1, 7.4 |
| Enlace a la declaración de accesibilidad | URL de la declaración de accesibilidad de la aplicación, publicada según el modelo de la Decisión (UE) 2018/1523 | 4.1, 5.1 |
| Texto y enlace del canal de soporte | El canal por el que el personal notifica incidencias y barreras de accesibilidad | 4.1, 5.1 (mecanismo de comunicación) |
| Aviso en las pantallas de acceso | `Sistema de uso exclusivo para fines municipales. La actividad queda registrada.` | 6.1 (aviso de uso) |
| Aviso de inteligencia artificial | Puede dejarse vacío (texto por defecto) o adaptarse, pero debe informar de que las respuestas las genera un sistema de IA | 6 (Reglamento (UE) 2024/1689) |

## 5. Aplicarlo con la API

Para configurar varias instalaciones igual, se puede enviar la configuración con
`PUT /api/v1/admin/appearance` (con una sesión de administración). Sustituye los valores entre
`<…>`; el logo se sube aparte con `PUT /api/v1/admin/appearance/logo`.

```json
{
  "app_name": "<Nombre de la aplicación>",
  "organization": "Ayuntamiento de Valladolid",
  "header_style": "marca",
  "logo_alt": "Ayuntamiento de Valladolid",
  "font_sans": "trebuchet",
  "font_mono": "consolas",
  "colors": {
    "marca": "#980046",
    "marca-oscuro": "#4E1036",
    "sobre-marca": "#FFFFFF",
    "marca-suave": "#FBEFF4",
    "enlace": "#7A0038",
    "texto": "#1F1A1C",
    "texto-secundario": "#5C5458",
    "fondo": "#FFFFFF",
    "fondo-alt": "#F6F3F4",
    "borde-control": "#8A8286",
    "borde": "#E4DDE0",
    "foco": "#005FCC",
    "error": "#B3261E",
    "error-suave": "#FCEFEE",
    "exito": "#1E6B3A",
    "exito-suave": "#EDF6F0",
    "aviso": "#8A5A00",
    "aviso-suave": "#FBF4E6",
    "info": "#0B5C8A",
    "info-suave": "#EAF3F9"
  },
  "footer": {
    "unit": "<Unidad responsable>",
    "accessibility_url": "<https://… declaración de accesibilidad>",
    "support_text": "<Canal de soporte>",
    "support_url": "<https://… soporte>"
  },
  "login_notice": "Sistema de uso exclusivo para fines municipales. La actividad queda registrada.",
  "ai_notice": ""
}
```

La respuesta incluye `warnings`, que debe ser una lista vacía.

## 6. Lista de comprobación (Anexo A del manual)

Qué cubre la aplicación y qué depende de cada instalación:

| Punto | Estado |
|---|---|
| Escudo SVG oficial, versión y zona de protección | **Instalación**: subir el escudo oficial (sección 1) |
| Tokens de color municipales; ningún color del escudo en la interfaz | **Instalación**: valores de la sección 2. La aplicación solo usa tokens |
| Trebuchet MS como tipografía principal | **Instalación**: sección 3 |
| axe-core sin incidencias graves ni críticas | **Aplicación**: lo comprueban los E2E en cada cambio |
| Todo el flujo con teclado y foco visible | **Aplicación** |
| 200 % de zoom y 320 px sin desplazamiento horizontal | **Aplicación**: los E2E lo comprueban a 320 px |
| Contrastes verificados | **Aplicación** (tema por defecto) e **instalación** (la página *Apariencia* avisa) |
| Probado con NVDA en los flujos principales | **Instalación**: prueba manual antes de producción |
| Formularios con etiquetas visibles y errores en texto | **Aplicación** |
| Documentos descargables etiquetados | **Instalación**: los documentos que se suben al RAG deberían ser accesibles |
| Declaración de accesibilidad publicada y enlazada desde el pie | **Instalación**: publicarla y configurar el enlace (sección 4) |
| Categorización ENS y medidas aplicadas | **Instalación** |
| Registro de actividades de tratamiento; evaluación de impacto | **Instalación** |
| Acceso con identidad corporativa y registro de actividad | **Instalación**: `CHATBOT_AUTH_MODE=ldap` (ver [despliegue](despliegue.md#usuarios-con-ldap)); los accesos quedan en el log |
| Sin recursos externos de terceros | **Aplicación**: fuentes, iconos y bibliotecas se sirven desde el propio servidor |
| Textos en lenguaje claro e inclusivo; formatos `es-ES` | **Aplicación** (textos de la interfaz) e **instalación** (textos configurados) |
| Ayuda y canal de soporte accesibles | **Instalación**: enlace de soporte en el pie (sección 4) |
