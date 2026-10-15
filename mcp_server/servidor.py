"""servidor mcp unico de parachute s.a. (transporte streamable http).

aca viven las 3 herramientas de negocio que antes eran @function_tool locales
de los agentes (app/shared/tools.py):

* buscar_en_faqs   -> la usa agente_faqs
* consultar_clima  -> la usa agente_agenda
* agendar_cita     -> la usa agente_agenda

este archivo no duplica la logica de negocio: solo llama a las funciones
"_raw" que ya existian en app/shared/ (faqs.py, clima.py, agenda.py). lo que
agrega es lo que pide un servidor de verdad: validacion de argumentos, timeout,
errores claros y un log por cada ejecucion de herramienta.

se corre en su propia terminal:  python mcp_server/servidor.py
y queda escuchando en http://127.0.0.1:8001/mcp (configurable en el .env).

ojo: no se usa stdio ni el transporte viejo http+sse. se usa streamable http,
que por dentro puede responder con sse, pero eso es parte normal del protocolo.
"""

import asyncio
import functools
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import Annotated

import uvicorn
from pydantic import Field

# --- acceso a la logica de negocio -------------------------------------------
# la logica vive en app/shared, asi que agrego app/ al path para poder importar
# "shared.*" igual que lo hacia el agente original
_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_RAIZ, "app"))

from mcp.server import MCPServer  # noqa: E402  (servidor de alto nivel del sdk oficial)
from mcp.server.mcpserver import Context  # noqa: E402  (contexto de cada request)
from mcp.server.mcpserver.exceptions import ToolError  # noqa: E402  (error "esperado" de una tool)
from mcp.types import ToolAnnotations  # noqa: E402  (pistas de si la tool lee o escribe)

from shared.agenda import agendar_cita_raw  # noqa: E402
from shared.clima import consultar_clima_raw  # noqa: E402
from shared.config import env  # noqa: E402
from shared.faqs import buscar_en_faqs_raw, precargar_modelo  # noqa: E402

# --- configuracion por variables de entorno -----------------------------------
# localhost por defecto: el servidor solo se ve desde esta misma maquina
HOST = env("MCP_HOST", "127.0.0.1")
PUERTO = int(env("MCP_PORT", "8001"))
RUTA = env("MCP_PATH", "/mcp")

# tiempo maximo que puede tardar una herramienta antes de cortarla con error.
# tiene que ser mayor que el timeout de 15 s que ya usa clima.py con open-meteo
TIMEOUT_TOOL = float(env("MCP_TOOL_TIMEOUT", "30"))

# --- logs ---------------------------------------------------------------------
# un formato corto y legible para que se vea bien en el video
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MCP] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("parachute.mcp")

# cabeceras http que manda el servidor a2a con cada conexion mcp. no son parte
# del protocolo mcp, son un mecanismo simple nuestro para relacionar una llamada
# de herramienta con la conversacion (context) y la tarea (task) de a2a
CABECERA_CONTEXTO = "x-parachute-context"
CABECERA_TAREA = "x-parachute-task"


def _ids_correlacion(ctx):
    """saca de la request http los ids que mando el servidor a2a, si vienen.

    si el cliente no los manda (por ejemplo pruebas/probar_mcp.py), se
    muestra un guion en el log en vez de inventar un id.
    """
    # request_context.request es la request de starlette cuando el transporte
    # es http; en otros transportes no existe, por eso el getattr con default
    peticion = ctx.request_context.request
    cabeceras = getattr(peticion, "headers", None) or {}
    contexto = cabeceras.get(CABECERA_CONTEXTO, "-")
    tarea = cabeceras.get(CABECERA_TAREA, "-")
    # los ids de a2a son uuid largos; con 8 caracteres alcanza para seguirlos
    return contexto[:8], tarea[:8]


def con_log(nombre_tool, resumir_args):
    """decorador que mide y registra cada ejecucion de una herramienta.

    nombre_tool: el nombre publico de la tool en mcp.
    resumir_args: funcion que recibe los kwargs y devuelve un texto corto para
        el log. sirve para no imprimir datos completos del usuario (por ejemplo
        de la consulta de faqs solo se muestra cuantos caracteres tiene).
    """

    def decorador(funcion):
        # functools.wraps conserva el nombre, la firma y el docstring de la
        # funcion original: el sdk de mcp los usa para armar el esquema de
        # entrada y la descripcion de la herramienta
        @functools.wraps(funcion)
        async def envoltura(*args, ctx: Context, **kwargs):
            contexto, tarea = _ids_correlacion(ctx)
            inicio = time.perf_counter()
            log.info(
                "tool=%s INICIO req=%s ctx=%s task=%s args=(%s)",
                nombre_tool, ctx.request_id, contexto, tarea, resumir_args(kwargs),
            )
            try:
                resultado = await funcion(*args, ctx=ctx, **kwargs)
            except Exception as e:
                # se registra el error con su duracion y se vuelve a lanzar
                # para que el sdk lo devuelva al cliente como resultado isError
                ms = (time.perf_counter() - inicio) * 1000
                log.error("tool=%s ERROR en %.0f ms: %s", nombre_tool, ms, e)
                raise
            ms = (time.perf_counter() - inicio) * 1000
            # del resultado solo se registra el principio en una linea (ahi
            # va el codigo como CITA_CONFIRMADA o SIN_RESULTADOS), no todo
            log.info("tool=%s OK en %.0f ms -> %s", nombre_tool, ms, " ".join(resultado.split())[:70])
            return resultado

        return envoltura

    return decorador


async def _correr_con_timeout(funcion, *args):
    """corre una funcion bloqueante de shared/ en un hilo, con limite de tiempo.

    las funciones _raw usan requests y psycopg, que son bloqueantes; si se
    llamaran directo congelarian el loop async del servidor y ninguna otra
    request se atenderia mientras tanto. asyncio.to_thread las manda a un hilo.
    """
    try:
        return await asyncio.wait_for(asyncio.to_thread(funcion, *args), timeout=TIMEOUT_TOOL)
    except asyncio.TimeoutError:
        # ojo: el hilo no se puede matar, solo se deja de esperar. por eso la
        # tool de agendar avisa que no hay que reintentar a ciegas
        raise ToolError(f"la herramienta tardo mas de {TIMEOUT_TOOL:.0f} s y se corto la espera")


# --- ciclo de vida del servidor ------------------------------------------------


@asynccontextmanager
async def ciclo_de_vida(_servidor):
    """se ejecuta una vez al arrancar el servidor (antes del yield) y una vez
    al apagarlo (despues del yield)."""
    log.info("servidor MCP 'parachute-herramientas' iniciando en http://%s:%s%s", HOST, PUERTO, RUTA)
    log.info("herramientas: buscar_en_faqs, consultar_clima, agendar_cita | timeout por tool: %.0f s", TIMEOUT_TOOL)
    # el modelo de embeddings tarda varios segundos en cargar; se precarga aca
    # para que la primera pregunta de faqs no pague ese costo. si falla no se
    # tumba el servidor: clima y agenda siguen funcionando
    try:
        await asyncio.to_thread(precargar_modelo)
        log.info("modelo de embeddings precargado")
    except Exception as e:  # noqa: BLE001
        log.warning("no se pudo precargar el modelo de embeddings: %s", e)
    yield {}
    log.info("servidor MCP detenido")


# --- definicion del servidor y sus herramientas ----------------------------------

servidor = MCPServer(
    "parachute-herramientas",
    instructions=(
        "herramientas de negocio de parachute s.a.: preguntas frecuentes del evento, "
        "clima de la zona de salto y agenda de citas de salto."
    ),
    lifespan=ciclo_de_vida,
)


@servidor.tool(
    name="buscar_en_faqs",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
@con_log("buscar_en_faqs", lambda a: f"consulta={len(a['consulta'])} caracteres, k={a.get('k', 4)}")
async def buscar_en_faqs(
    consulta: Annotated[str, Field(min_length=1, max_length=500, description="la pregunta o el tema a buscar, en lenguaje natural")],
    *,
    ctx: Context,
    k: Annotated[int, Field(ge=1, le=10, description="cuantas fichas traer (1 a 10)")] = 4,
) -> str:
    """Busca en la base de conocimientos de FAQs de Parachute S.A. por similitud
    semantica y devuelve las fichas mas parecidas a la consulta. Devuelve
    SIN_RESULTADOS si ninguna ficha supera el umbral de similitud."""
    try:
        return await _correr_con_timeout(buscar_en_faqs_raw, consulta, k)
    except ToolError:
        raise
    except Exception as e:
        # el error tipico es que postgres no este levantado; se explica que hacer
        raise ToolError(
            f"no se pudo consultar la base de faqs ({type(e).__name__}). revisa que docker "
            "este corriendo (docker compose -f infra/docker-compose.yml up -d) y que se haya "
            "cargado el corpus (python infra/cargar.py)"
        ) from e


@servidor.tool(
    name="consultar_clima",
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=True),
)
@con_log("consultar_clima", lambda a: f"fecha={a['fecha']}")
async def consultar_clima(
    fecha: Annotated[str, Field(min_length=1, max_length=20, description="fecha en formato AAAA-MM-DD, entre hoy y 16 dias adelante")],
    *,
    ctx: Context,
) -> str:
    """Consulta el pronostico del clima en la zona de salto para una fecha, sin
    agendar nada. Sirve para que el usuario decida si quiere reservar. Si la
    fecha no sirve devuelve FECHA_INVALIDA con el motivo."""
    try:
        datos = await _correr_con_timeout(consultar_clima_raw, fecha)
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(f"no se pudo consultar open-meteo ({type(e).__name__}), intenta de nuevo en un momento") from e

    # un error de fecha (formato, ya paso, mas de 16 dias) no es una falla del
    # servidor: se devuelve como texto, igual que lo hacia la tool original,
    # para que el agente se lo explique al usuario
    if "error" in datos:
        return f"FECHA_INVALIDA: {datos['error']}"

    # mismo formato de texto que tenia la tool local en app/shared/tools.py
    ev = datos["evaluacion"]
    problemas = "; ".join(ev["problemas"]) if ev["problemas"] else "ninguno"
    return (
        f"fecha={datos['fecha']} veredicto={ev['veredicto']} "
        f"temperatura={datos['temperatura']}C viento={datos['viento']}km/h "
        f"rafagas={datos['rafaga']}km/h precipitacion={datos['precipitacion']}mm "
        f"nubes={datos['nubes']}% problemas={problemas}"
    )


@servidor.tool(
    name="agendar_cita",
    # modifica datos (guarda la cita) y no es idempotente: llamarla dos veces
    # guarda dos citas, por eso nadie debe reintentarla automaticamente
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
)
@con_log("agendar_cita", lambda a: f"fecha={a['fecha']}, nombre={'si' if a.get('nombre') else 'no'}")
async def agendar_cita(
    fecha: Annotated[str, Field(min_length=1, max_length=20, description="fecha deseada para la cita, en formato AAAA-MM-DD")],
    *,
    ctx: Context,
    nombre: Annotated[str | None, Field(max_length=80, description="nombre de quien reserva, opcional")] = None,
) -> str:
    """Revisa el clima de la fecha pedida y, si las condiciones son seguras
    para saltar, agenda la cita. Si no son seguras, no agenda nada y explica
    por que. Devuelve CITA_CONFIRMADA o NO_SE_PUDO_AGENDAR con el detalle."""
    try:
        return await _correr_con_timeout(agendar_cita_raw, fecha, nombre)
    except ToolError as e:
        # timeout: la cita pudo haberse guardado igual en el hilo, se avisa
        raise ToolError(f"{e}. no reintentes sin revisar si la cita quedo guardada") from e
    except Exception as e:
        raise ToolError(f"no se pudo agendar ({type(e).__name__}), no se guardo ninguna cita") from e


def main():
    # streamable_http_app arma una app starlette con el endpoint mcp en RUTA.
    # host se usa para la proteccion contra dns rebinding que trae el sdk
    app = servidor.streamable_http_app(streamable_http_path=RUTA, host=HOST)
    # log_level warning en uvicorn para que solo se vean nuestros logs (y errores)
    uvicorn.run(app, host=HOST, port=PUERTO, log_level="warning")


if __name__ == "__main__":
    main()
