# HDT7 Protocolos agénticos: Parachute S.A. con MCP y A2A

CC3116. Se toma el agente de Parachute S.A. de HDT6 (arquitectura
centralizada: un `agente_manager` que delega en `agente_faqs` y
`agente_agenda`) y se separa en tres componentes que se comunican con
protocolos estándar:

* **MCP**: las tres herramientas de negocio que antes eran `@function_tool`
  locales ahora viven en **un solo servidor MCP** con transporte
  **Streamable HTTP**.
* **A2A**: el sistema multiagente completo se expone como **un solo agente
  A2A** ("Agente de Parachute S.A.") con dos skills públicas.
* **Terminal**: la interfaz de terminal de HDT6 ahora es un **cliente A2A**.
  Ya no corre agentes ni herramientas: solo manda mensajes por la red.

**Video de la demostración:** <https://youtu.be/IFrm6QCoFNQ>

## Arquitectura

```
 terminal/cliente_a2a.py                (cliente A2A, no importa agentes)
        │  A2A 1.0 · JSON-RPC sobre HTTP · descubre /.well-known/agent-card.json
        ▼
 a2a_server/servidor.py  :8000          (un solo agente público, 2 skills)
        │  ParachuteExecutor: mensaje A2A → Runner.run(agente_manager, historial)
        ▼
 app/centralizado.py                    (sistema multiagente de HDT6)
   agente_manager ──as_tool──► agente_faqs   ─┐
                  └─as_tool──► agente_agenda ─┤ clientes MCP (MCPServerStreamableHttp)
                                              │  MCP · Streamable HTTP
                                              ▼
 mcp_server/servidor.py  :8001/mcp      (servidor MCP único, 3 herramientas)
        │  llama a la lógica de negocio original de app/shared/
        ▼
 pgvector (FAQs) · Open-Meteo (clima) · app/data/citas.json (citas)
```

La respuesta regresa por el mismo camino. La delegación del manager a los
especialistas (`as_tool`) es orquestación interna del SDK de agentes y **no**
se convirtió en herramienta MCP: el servidor MCP solo tiene operaciones de
negocio.

## Resumen de cambios respecto a HDT6

| Dónde | Qué cambió |
|---|---|
| `mcp_server/servidor.py` (nuevo) | Servidor MCP único (`mcp.server.MCPServer`, SDK oficial 2.2.0) con `buscar_en_faqs`, `consultar_clima` y `agendar_cita`. Valida argumentos con el esquema, corre la lógica bloqueante en un hilo con timeout, convierte fallas en errores MCP explicados y registra cada ejecución. |
| `app/centralizado.py` | Ahora es una fábrica: `conectar_mcp()` crea el cliente MCP (Streamable HTTP, sin reintentos) y `construir_manager()` arma el mismo sistema de HDT6. Cada especialista recibe `mcp_servers=[...]` con un filtro para ver solo sus herramientas. |
| `app/shared/tools.py`, `app/shared/cli.py` | Eliminados: ya no hay herramientas locales ni loop de terminal local, así que no queda ningún camino que salte el servidor MCP. |
| `a2a_server/` (nuevo) | Servidor A2A (`a2a-sdk` 1.2.2): Agent Card, ejecutor que traduce mensajes A2A al sistema multiagente y app Starlette con JSON-RPC. |
| `terminal/cliente_a2a.py` (nuevo) | Terminal como cliente A2A: descubre la card, conversa con varios turnos, `/nuevo`, `Bye`. |
| `app/shared/prompts.py`, `agenda.py`, `formato.py` | Arreglos de los fallos que encontraron los evals de HDT6 (ver "Arreglos del agente"). |
| `app/shared/faqs.py` | Precarga del modelo de embeddings y `connect_timeout` para fallar rápido si Postgres no está. |
| `pruebas/` (nuevo) | Revisión estática y pruebas de integración de MCP y A2A. |
| `infra/docker-compose.yml`, `.env.example`, `requirements.txt` | Compose propio de HDT7 (puerto 5436), variables por componente y versiones fijas. |

Los evals de promptfoo de HDT6 no se trajeron: llamaban a las herramientas en
local, que es justo lo que esta hoja elimina.

## Inventario: herramientas MCP y skills A2A

**Herramienta MCP** = operación concreta que ejecuta un agente por dentro.
**Skill A2A** = capacidad pública que el sistema completo ofrece a otros
clientes. El cliente A2A nunca ve las herramientas MCP.

| Herramienta MCP | La usa | Implementación (sin duplicar) | Argumentos | Modifica datos |
|---|---|---|---|---|
| `buscar_en_faqs` | `agente_faqs` | `shared/faqs.py::buscar_en_faqs_raw` (pgvector) | `consulta` (1–500 caracteres), `k` (1–10, por defecto 4) | No |
| `consultar_clima` | `agente_agenda` | `shared/clima.py::consultar_clima_raw` (Open-Meteo) | `fecha` (AAAA-MM-DD) | No |
| `agendar_cita` | `agente_agenda` | `shared/agenda.py::agendar_cita_raw` | `fecha`, `nombre` (opcional, hasta 80) | Sí: guarda la cita solo si el clima es seguro |

| Skill A2A (`id`) | Qué ofrece | Especialista / herramientas por dentro |
|---|---|---|
| `preguntas_frecuentes` | Preguntas frecuentes del evento desde la base oficial; si no hay dato, remite a soporte | `agente_faqs` → `buscar_en_faqs` |
| `agenda_de_saltos` | Pronóstico de una fecha (hasta 16 días) y agenda de saltos solo con clima seguro | `agente_agenda` → `consultar_clima`, `agendar_cita` |

**Prompts y recursos MCP.** No se expusieron: los prompts del sistema los usan
los agentes internos y no tienen sentido para otro cliente MCP, y el proyecto
no tiene datos de solo lectura que un cliente necesite leer como recurso. Los
datos del negocio se consultan mediante las herramientas.

**Agent Card** (`GET /.well-known/agent-card.json`): nombre "Agente de
Parachute S.A.", interfaz `JSONRPC` en `http://127.0.0.1:8000` con protocolo
`1.0`, entrada y salida `text/plain`, y `streaming`, `pushNotifications` y
`extendedAgentCard` en `false`, porque no están implementados. La cancelación
de tareas tampoco está implementada: responde `UnsupportedOperationError`.

## Requisitos

* Python 3.11 o superior (probado con 3.11.9 en Windows 11).
* Docker Desktop (para pgvector).
* Una API Key gratuita de Groq: <https://console.groq.com/keys>
* Internet (Groq y Open-Meteo).

Versiones fijadas en `requirements.txt`: `openai-agents==0.22.3`,
`mcp==2.2.0`, `a2a-sdk[http-server]==1.2.2`, `uvicorn==0.54.0` y
`httpx==0.28.1`, más las de HDT6 (psycopg, pgvector, sentence-transformers).
Van exactas porque las APIs cambiaron mucho entre versiones: MCP 2.x usa
`MCPServer` en lugar de `FastMCP`, y A2A 1.x usa tipos protobuf y rutas
nuevas.

## Instalación y configuración

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1          # Linux o macOS: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env              # luego editar .env y pegar GROQ_API_KEY
```

Base de conocimientos (una sola vez; el volumen persiste):

```powershell
docker compose -f infra/docker-compose.yml up -d
python infra/cargar.py              # 120 fichas → tabla faqs
```

Variables principales del `.env` (todas con valores por defecto en
localhost):

| Componente | Variables |
|---|---|
| Servidor MCP | `DATABASE_URL`, `EMBEDDING_MODEL`, `UMBRAL_SIMILITUD`, `MCP_HOST`, `MCP_PORT`, `MCP_PATH`, `MCP_TOOL_TIMEOUT`, `CITAS_FILE` |
| Servidor A2A | `GROQ_API_KEY`, `GROQ_BASE_URL`, `GROQ_MODEL`, `MCP_URL`, `A2A_HOST`, `A2A_PORT`, `A2A_PUBLIC_URL` |
| Terminal | `A2A_URL`, `A2A_TIMEOUT` |

La API key de Groq solo la usa el servidor A2A, y las credenciales de Postgres
solo el servidor MCP. La terminal no necesita ningún secreto.

## Ejecución: tres terminales

Abrir tres terminales en la raíz del repo, activar el venv en cada una y
correr en este orden:

```powershell
# terminal 1: servidor MCP (tarda ~1 min en cargar torch y el modelo de embeddings)
python mcp_server/servidor.py
# esperar la línea "StreamableHTTP session manager started"

# terminal 2: servidor A2A
python a2a_server/servidor.py

# terminal 3: cliente A2A
python terminal/cliente_a2a.py
```

En la terminal: escribir preguntas, `/nuevo` para empezar otra conversación y
`Bye` (o `adiós`, `salir`, Ctrl+C) para salir. Para no ensuciar las citas
reales durante una prueba, iniciar el servidor MCP con
`$env:CITAS_FILE="app/data/citas_prueba.json"` antes de correrlo.

## Logs y correlación

**MCP**: inicio del servidor y, por cada ejecución de herramienta, una línea
`INICIO` y otra `OK` o `ERROR` con la duración:

```
17:06:25 [MCP] INFO tool=consultar_clima INICIO req=3 ctx=420ff9ce task=22d0cae7 args=(fecha=2026-10-30)
17:06:26 [MCP] INFO tool=consultar_clima OK en 725 ms -> Pronostico para el 2026-10-30 al mediodia: condiciones marginales, sol
```

**A2A**: inicio, cada request HTTP (descarga de la card, POST JSON-RPC),
solicitud recibida, ejecución del sistema y fin o error. Estas son las líneas
del mismo turno:

```
17:06:24 [A2A] INFO solicitud recibida ctx=420ff9ce task=22d0cae7 (51 caracteres)
17:06:24 [A2A] INFO task=22d0cae7 conectado a MCP, ejecutando agente_manager
17:06:28 [A2A] INFO task=22d0cae7 COMPLETADA en 4297 ms (respuesta de 305 caracteres)
17:06:28 [A2A] INFO HTTP POST / -> 200 (4303 ms)
```

**Terminal**: después de cada respuesta muestra `(ctx=420ff9ce task=22d0cae7)`.

Con esos ids se relacionan los tres logs. `ctx` es el `contextId` de A2A
(la conversación) y `task` es el `id` de la tarea A2A; ambos los asigna el
SDK de A2A. Para que lleguen al log MCP, el servidor A2A los manda en dos
cabeceras HTTP propias, `X-Parachute-Context` y `X-Parachute-Task`, en la
conexión MCP de ese turno. Ese es un mecanismo simple de este proyecto, **no
un campo del protocolo MCP**. `req` es el id de request JSON-RPC de MCP, y
como cada turno abre su propia sesión MCP suele ser `3`.

Qué no se registra: la API key, el texto completo de la pregunta (solo su
largo), el texto completo de la respuesta (solo el comienzo, en MCP) ni el
razonamiento del modelo. Si una herramienta recibe un nombre, el log solo
dice `nombre=si` o `nombre=no`.

## Conversaciones, tareas y memoria

* Cada mensaje del usuario es una **tarea A2A** que pasa por `submitted`, luego
  `working`, y termina en `completed` (la respuesta va como artefacto
  `respuesta`), en `failed` (el motivo va en el mensaje de estado) o en
  `rejected` si el mensaje está vacío.
* La terminal manda el `contextId` que le devolvió el servidor. El servidor
  guarda el historial del sistema multiagente por `contextId` (con un lock por
  conversación), así cada cliente y cada `/nuevo` tienen su propia memoria.
* **Todo esto vive en memoria** (`MemoriaConversaciones` e
  `InMemoryTaskStore`): si se reinicia el servidor A2A se pierden las
  conversaciones y las tareas. Es suficiente para la demo, por eso no se
  agregó base de datos. Las citas sí persisten en `app/data/citas.json`.
* Cada turno abre su **propia conexión MCP** y la cierra al terminar. No hay
  sesiones MCP compartidas entre clientes, y si el servidor MCP se reinicia, el
  siguiente turno se reconecta solo.

## Errores y efectos secundarios

* Argumentos inválidos para el esquema (por ejemplo `k=50` o una fecha
  faltante) se rechazan en el servidor MCP como resultado `isError`. Una fecha
  que no se entiende, pasada o a más de 16 días se devuelve como texto
  explicativo para el usuario, igual que en HDT6.
* Postgres apagado: error MCP que dice que hay que levantar Docker y cargar el
  corpus. Open-Meteo caído: error que pide intentar de nuevo.
* Servidor MCP apagado: la tarea A2A termina en `failed` con *"El servidor MCP
  de herramientas no está disponible en … Inícialo con: python
  mcp_server/servidor.py"*.
* Servidor A2A apagado: la terminal lo dice al arrancar o en el turno en que
  falle, y sigue viva.
* **Sin reintentos automáticos**: `max_retry_attempts=0` en el cliente MCP,
  porque `agendar_cita` guarda datos y repetirla podría duplicar una cita.
  Está marcada como `idempotentHint=False`. Si se pasa el timeout, el mensaje
  pide no reintentar sin revisar si la cita quedó guardada, porque el hilo no
  se puede interrumpir.

## Arreglos del agente (fallos que detectaron los evals de HDT6)

| Fallo en HDT6 | Arreglo |
|---|---|
| Elegía mal la herramienta: agendaba cuando solo se pedía el clima, y al revés | Reglas explícitas en `INSTRUCCIONES_AGENDA`, descripciones MCP que dicen cuándo usar cada tool y un manager que pasa el pedido sin cambiar la intención |
| Pedía el nombre en vez de agendar | La instrucción dice que el nombre es opcional y no se pregunta |
| Mostraba códigos internos (`CITA_CONFIRMADA`, `NO_SE_PUDO_AGENDAR`) | Las herramientas devuelven frases en español: no queda ningún código que se pueda filtrar |
| Usaba markdown aunque se pedía texto plano | Instrucción reforzada y `shared/formato.py::a_texto_plano` como red de seguridad: solo quita formato, no cambia palabras |
| Inventaba datos en fichas plantilla ("hay parqueo") | Regla para las fichas que solo repiten la pregunta: no afirmar ni negar y remitir a soporte |
| (nuevo en HDT7) Inventaba procesos, como "revise su correo de confirmación" | Si el pedido no le toca a ningún especialista (consultar o cancelar citas), lo dice y remite a soporte |

## Pruebas realizadas

Con Docker, la base cargada, Groq y Open-Meteo reales (15 y 16 de octubre de
2026):

```powershell
python pruebas/revisar_estructura.py      # sin servidores
python pruebas/probar_mcp.py              # con el servidor MCP
python pruebas/probar_a2a.py              # con MCP y A2A (~3 min por las pausas)
python pruebas/probar_a2a.py --sin-mcp    # con A2A y el MCP apagado a propósito
```

| Prueba | Resultado |
|---|---|
| `revisar_estructura.py`: la terminal no importa agentes, `app/` ni MCP; no hay `@function_tool` en `app/`; el MCP registra las 3 tools | 3/3 |
| `probar_mcp.py`: cliente MCP oficial por Streamable HTTP, listado y esquemas, clima válido e inválido, `k=50` e `agendar_cita` sin fecha → `isError`, búsqueda en pgvector (FAQ-021, similitud 0.98), agendar con fecha pasada sin guardar | 10/10 |
| `probar_a2a.py`: card y skills, delegación a FAQs (100 kg) y a agenda (Open-Meteo), ficha plantilla de parqueo sin inventar, continuidad en la misma conversación, aislamiento en otra, mensaje vacío rechazado | 10/10 |
| `probar_a2a.py --sin-mcp`: tarea `failed` con mensaje accionable | 5/5 |
| Manual con la terminal: clima → "agéndame para esa fecha a nombre de Prueba HDT7" → cita confirmada y guardada en un `CITAS_FILE` de prueba; `/nuevo` aislado; servidor A2A caído a mitad de sesión; `Bye` | OK |
| Manual con los 8 casos de agenda que fallaban en HDT6 (solo clima, agendar sin nombre, fecha en palabras, fecha pasada, clima no seguro) | Herramienta correcta en todos; solo se guardó la cita que se pidió |

Las pruebas automáticas no guardan citas a propósito; la escritura real se
verificó a mano con `CITAS_FILE`. No se probó Ctrl+C de forma automática
(no se puede mandar por un pipe); usa el mismo manejo que la terminal de
HDT6.

## Limitaciones

* **El modelo no es determinista.** Los casos marcados "(modelo)" se
  corrieron una o dos veces: son una muestra, no una tasa. Los evals con
  `--repeat` de HDT6 serían la forma de medirlo.
* **Límite de Groq gratis** (8000 tokens por minuto): con turnos muy seguidos,
  el cliente de OpenAI espera en silencio y un turno puede tardar ~40 s. Por
  eso la terminal tiene `A2A_TIMEOUT=180` y las pruebas pausan 20 s.
* **Memoria solo en RAM** en el servidor A2A (ver arriba).
* **Sin autenticación**: todo escucha en `127.0.0.1` para la demo.
* El servidor MCP tarda ~1 minuto en arrancar (torch y modelo de
  embeddings).
* Los pronósticos cambian cada día: los ejemplos con fechas concretas hay que
  ajustarlos a los próximos 16 días.

## Solución de errores frecuentes

| Síntoma | Causa y solución |
|---|---|
| Terminal: "No se pudo descargar la agent card" | El servidor A2A no está corriendo: `python a2a_server/servidor.py` |
| Respuesta `[error] El servidor MCP … no está disponible` | Falta el servidor MCP, o todavía está cargando: esperar "StreamableHTTP session manager started" |
| FAQs: "no se pudo consultar la base de faqs" | Docker apagado o base vacía: `docker compose -f infra/docker-compose.yml up -d` y `python infra/cargar.py` |
| `[error] el servidor no respondió en 180 s` o turnos lentos | Límite de tokens de Groq: esperar un minuto entre preguntas |
| Error del proveedor del modelo (Groq) | Revisar `GROQ_API_KEY` en `.env` |
| `address already in use` | Otro proceso usa 8000 u 8001: cerrarlo o cambiar `A2A_PORT`/`MCP_PORT` (y `MCP_URL`/`A2A_URL`) |

## Requisitos de la hoja → implementación → evidencia

| Requisito | Implementación | Evidencia |
|---|---|---|
| Herramientas de los agentes en un solo servidor MCP | `mcp_server/servidor.py` con las 3 tools; `app/shared/tools.py` eliminado | `revisar_estructura.py` y `probar_mcp.py` (lista exacta de tools) |
| Transporte Streamable HTTP | `streamable_http_app()` en el servidor y `MCPServerStreamableHttp` en los agentes | `probar_mcp.py` conecta con `streamable_http_client`; log "StreamableHTTP session manager started" |
| Logs MCP por cada ejecución | Decorador `con_log`: INICIO, OK o ERROR y duración | Log de la terminal 1 en las pruebas y en el video |
| Sistema multiagente como un solo agente A2A con skills | `a2a_server/tarjeta.py`: una card, 2 skills | `probar_a2a.py` (card y skills) |
| Servidor A2A que envuelve el sistema | `a2a_server/ejecutor.py` + `servidor.py` (SDK oficial 1.2.2, JSON-RPC) | `probar_a2a.py` y logs A2A |
| Terminal como cliente A2A | `terminal/cliente_a2a.py` (`A2ACardResolver` + `create_client`) | Prueba manual de la terminal; `revisar_estructura.py` (sin imports de agentes) |
| Repo y video | Este repo | Video: <https://youtu.be/IFrm6QCoFNQ> |

## Demostración y video

Video: <https://youtu.be/IFrm6QCoFNQ>

Para reproducir la demostración:

1. Encender Docker Desktop y correr `docker compose -f infra/docker-compose.yml up -d`
   (y `python infra/cargar.py` si la base nunca se cargó).
2. Abrir tres terminales con el venv activo y levantar los componentes en el
   orden de "Ejecución: tres terminales". Antes de levantar el servidor MCP,
   se puede usar `$env:CITAS_FILE="app/data/citas_demo.json"` para que la cita
   de la demo no se mezcle con otras.
3. En la terminal del cliente, con unos 30 s entre preguntas por el límite de
   Groq:
   * `¿Cuál es el peso máximo para saltar?` → `buscar_en_faqs` en el log MCP.
   * `¿Cómo estará el clima el AAAA-MM-DD para saltar? Solo quiero saber.`
     (una fecha de los próximos 16 días) → `consultar_clima`, sin agendar.
   * `Perfecto, agéndame para esa fecha a nombre de <nombre>` → usa la fecha
     del turno anterior y ejecuta `agendar_cita`. Solo confirma si el clima de
     esa fecha es ideal o marginal.
   * `/nuevo` y `¿Para qué fecha te pregunté el clima?` → otra conversación,
     que no conoce la fecha.
   * `Bye`.
4. Comparar los `ctx`/`task` que muestra la terminal con los de los logs A2A
   y MCP.

Abrir `http://127.0.0.1:8001/mcp` en el navegador devuelve
`Bad Request: Missing session ID`. Es normal: en Streamable HTTP el cliente
primero hace `initialize` por POST y después manda el id de sesión en la
cabecera `Mcp-Session-Id`. Lo que sí se puede abrir en el navegador es la
Agent Card: <http://127.0.0.1:8000/.well-known/agent-card.json>.

## Referencias consultadas

* MCP, construir un servidor: <https://modelcontextprotocol.io/docs/2026-07-28/develop/build-server>.
  Usa `from mcp.server import MCPServer`, igual que aquí; del tutorial solo se
  tomó la forma de declarar tools, **no** su transporte `stdio`.
* OpenAI Agents SDK, MCP: <https://openai.github.io/openai-agents-python/mcp/>.
  La API de `MCPServerStreamableHttp` (params, `max_retry_attempts`,
  `tool_filter`) se verificó en el código de la versión instalada, 0.22.3.
* A2A: <https://a2a-protocol.org/latest/> y los tutoriales de Python (4:
  ejecutor, 5: servidor, 6: cliente). Las APIs (`DefaultRequestHandler`,
  `create_agent_card_routes`, `create_jsonrpc_routes`, `TaskUpdater`,
  `create_client`) se verificaron en `a2a-sdk` 1.2.2.

Todas las URL de la hoja funcionaron; no hizo falta reemplazar ninguna.
