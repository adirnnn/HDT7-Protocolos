"""servidor a2a de parachute s.a.: expone todo el sistema multiagente como un solo agente.

se corre en su propia terminal:  python a2a_server/servidor.py
y queda escuchando en http://127.0.0.1:8000 (configurable en el .env) con:

* GET  /.well-known/agent-card.json  -> la agent card (descubrimiento)
* POST /                             -> endpoint json-rpc de a2a (SendMessage, GetTask, ...)

necesita que el servidor mcp (mcp_server/servidor.py) este corriendo, porque
los especialistas sacan de ahi sus herramientas.
"""

import logging
import os
import sys
import time

import uvicorn

# --- acceso al sistema multiagente ------------------------------------------------
# el sistema vive en app/ (centralizado.py y shared/), asi que se agrega al path
_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_RAIZ, "app"))

from a2a.server.request_handlers import DefaultRequestHandler  # noqa: E402
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes  # noqa: E402
from a2a.server.tasks import InMemoryTaskStore  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.middleware import Middleware  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402

from centralizado import URL_MCP  # noqa: E402
from ejecutor import ParachuteExecutor  # noqa: E402
from shared.config import env  # noqa: E402
from tarjeta import construir_tarjeta  # noqa: E402

# --- configuracion por variables de entorno ------------------------------------------
HOST = env("A2A_HOST", "127.0.0.1")
PUERTO = int(env("A2A_PORT", "8000"))
# la url que se publica en la agent card. se usa 127.0.0.1 y no "localhost"
# porque en windows localhost puede intentar primero ipv6 y tardar en conectar
URL_PUBLICA = env("A2A_PUBLIC_URL", f"http://127.0.0.1:{PUERTO}")

# --- logs ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [A2A] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
# los logs internos del sdk de agentes y de httpx se dejan en warning para
# que en la consola solo se vean los eventos de nuestro servidor
for ruidoso in ("httpx", "httpx2", "openai", "openai.agents", "mcp"):
    logging.getLogger(ruidoso).setLevel(logging.WARNING)
log = logging.getLogger("parachute.a2a")


class LogHttp(BaseHTTPMiddleware):
    """middleware que registra cada request http que llega (metodo, ruta, estado).

    sirve para ver en el log cuando un cliente descarga la agent card y cuando
    manda un mensaje json-rpc, aunque todavia no haya llegado al ejecutor.
    """

    async def dispatch(self, request, call_next):
        inicio = time.perf_counter()
        respuesta = await call_next(request)
        ms = (time.perf_counter() - inicio) * 1000
        log.info("HTTP %s %s -> %s (%.0f ms)", request.method, request.url.path, respuesta.status_code, ms)
        return respuesta


def crear_app():
    """arma la app starlette con las rutas a2a, igual que el tutorial oficial del sdk."""
    tarjeta = construir_tarjeta(URL_PUBLICA)

    # el request handler del sdk recibe las requests a2a, maneja las tareas en
    # el task store y llama a nuestro ejecutor. las tareas viven en memoria:
    # al reiniciar el servidor se pierden (igual que las conversaciones)
    manejador = DefaultRequestHandler(
        agent_executor=ParachuteExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=tarjeta,
    )

    rutas = []
    # GET /.well-known/agent-card.json (ruta de descubrimiento de a2a 1.0)
    rutas.extend(create_agent_card_routes(tarjeta))
    # POST / -> json-rpc de a2a
    rutas.extend(create_jsonrpc_routes(manejador, "/"))

    return Starlette(routes=rutas, middleware=[Middleware(LogHttp)]), tarjeta


def main():
    app, tarjeta = crear_app()
    log.info("servidor A2A '%s' iniciando en %s", tarjeta.name, URL_PUBLICA)
    log.info("agent card: %s/.well-known/agent-card.json", URL_PUBLICA)
    log.info("skills: %s", ", ".join(s.id for s in tarjeta.skills))
    log.info("servidor MCP de herramientas: %s", URL_MCP)
    # log_level warning en uvicorn: los accesos ya los registra LogHttp
    uvicorn.run(app, host=HOST, port=PUERTO, log_level="warning")


if __name__ == "__main__":
    main()
