"""arquitectura centralizada: un solo manager, los especialistas son tools.

el usuario solo habla con agente_manager. el manager decide a cual
especialista delegar (as_tool) y redacta la respuesta final con lo que le
devuelva. los especialistas nunca le hablan directo al usuario.

cambio de hdt7: las herramientas de negocio ya no son @function_tool locales.
los especialistas son clientes mcp: le piden la lista de herramientas al
servidor mcp unico (mcp_server/servidor.py) y las ejecutan por streamable http.
la delegacion manager -> especialista (as_tool) sigue siendo orquestacion
interna del sdk, no es una herramienta de negocio, por eso no va al servidor mcp.

este archivo ya no tiene loop de terminal: el sistema lo aloja el servidor
a2a (a2a_server/), y la terminal es un cliente a2a aparte (terminal/).
"""

from agents import Agent
from agents.mcp import MCPServerStreamableHttp, create_static_tool_filter

from shared.config import env
from shared.model import construir_modelo
from shared.prompts import INSTRUCCIONES_AGENDA, INSTRUCCIONES_FAQS, instrucciones_manager

# url del servidor mcp, por defecto el que levanta mcp_server/servidor.py
URL_MCP = env("MCP_URL", "http://127.0.0.1:8001/mcp")

# que herramienta del servidor mcp puede ver cada especialista. el servidor
# expone las 3 juntas, pero cada agente solo debe usar las suyas (igual que
# antes, cuando cada uno tenia su lista tools=[...])
TOOLS_FAQS = ["buscar_en_faqs"]
TOOLS_AGENDA = ["consultar_clima", "agendar_cita"]


def conectar_mcp(cabeceras=None):
    """crea el cliente mcp (streamable http) que usan los especialistas.

    devuelve el objeto sin conectar: quien lo use tiene que abrirlo con
    "async with conectar_mcp(...) as servidor:" para que la conexion se cierre
    bien al terminar, aunque haya un error.

    cabeceras: dict opcional de cabeceras http que se mandan en cada request al
    servidor mcp. el servidor a2a lo usa para mandar sus ids de conversacion y
    tarea, y asi poder relacionar los logs de los dos servidores.
    """
    return MCPServerStreamableHttp(
        params={
            "url": URL_MCP,
            "headers": cabeceras or {},
            # tiempo para cada request http al servidor mcp
            "timeout": 10,
        },
        name="parachute-herramientas",
        # la lista de tools no cambia mientras el servidor corre, se pide una
        # vez por conexion y se reusa en los turnos del manager y especialistas
        cache_tools_list=True,
        # tiempo maximo esperando la respuesta de una herramienta. un poco mas
        # que el timeout del lado del servidor (30 s) para que gane el error
        # explicado del servidor y no un corte del cliente
        client_session_timeout_seconds=40,
        # sin reintentos automaticos: agendar_cita guarda datos y repetirla a
        # ciegas podria duplicar la cita
        max_retry_attempts=0,
    )


def construir_manager(servidor_mcp):
    """arma el sistema multiagente completo usando un cliente mcp ya conectado.

    se construye por cada turno porque el cliente mcp vive solo durante ese
    turno (ver a2a_server/ejecutor.py). armar los Agent es barato: son solo
    objetos de configuracion, no hacen llamadas de red.
    """
    # el modelo (groq) se crea una sola vez por proceso y se reusa
    modelo = construir_modelo()

    agente_faqs = Agent(
        name="agente_faqs",
        instructions=INSTRUCCIONES_FAQS,
        model=modelo,
        # antes: tools=[buscar_en_faqs]. ahora la tool viene del servidor mcp
        mcp_servers=[servidor_mcp],
        mcp_config={"tool_filter": create_static_tool_filter(allowed_tool_names=TOOLS_FAQS)},
    )

    agente_agenda = Agent(
        name="agente_agenda",
        instructions=INSTRUCCIONES_AGENDA,
        model=modelo,
        # antes: tools=[consultar_clima, agendar_cita]
        mcp_servers=[servidor_mcp],
        mcp_config={"tool_filter": create_static_tool_filter(allowed_tool_names=TOOLS_AGENDA)},
    )

    # el manager queda igual que en hdt6: delega con as_tool y no ve las
    # herramientas de negocio directamente
    return Agent(
        name="agente_manager",
        instructions=instrucciones_manager(
            "manager",
            [
                "agente_faqs: responde preguntas frecuentes del evento (usa esta tool "
                "para cualquier pregunta de informacion).",
                "agente_agenda: consulta el clima y agenda citas de salto (usa esta "
                "tool para cualquier pedido de agendar o de saber el clima de una fecha).",
            ],
        ),
        model=modelo,
        tools=[
            agente_faqs.as_tool(
                tool_name="agente_faqs",
                tool_description="responde preguntas frecuentes de Parachute S.A. sobre el evento.",
            ),
            agente_agenda.as_tool(
                tool_name="agente_agenda",
                tool_description="consulta el clima y agenda citas de salto revisando el clima antes.",
            ),
        ],
    )
