"""agent card de parachute s.a.: la "tarjeta de presentacion" publica del agente a2a.

cualquier cliente a2a la descarga de /.well-known/agent-card.json para saber
quien es el agente, donde contestar (url + protocolo), que tipo de contenido
acepta y que skills ofrece.

diferencia importante para la hoja:
* herramienta mcp = operacion concreta que ejecuta un agente por dentro
  (buscar_en_faqs, consultar_clima, agendar_cita). el cliente a2a no las ve.
* skill a2a = capacidad publica que el sistema completo le ofrece a otros
  clientes. aca hay 2, una por cada especialista del sistema multiagente.
"""

from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

# skill 1: lo que hace agente_faqs (usa por dentro la tool mcp buscar_en_faqs)
SKILL_FAQS = AgentSkill(
    id="preguntas_frecuentes",
    name="Preguntas frecuentes del evento",
    description=(
        "Responde preguntas frecuentes del evento de paracaidismo de Parachute S.A. "
        "en Guatemala 2026 (requisitos, restricciones, precios, horarios), usando "
        "solo la base de conocimientos oficial. Si no hay dato, remite a soporte."
    ),
    tags=["faqs", "evento", "paracaidismo"],
    # ejemplos sacados de los casos de prueba que ya tenia el proyecto (hdt6)
    examples=[
        "¿Cuál es el peso máximo para saltar?",
        "¿Cuál es la edad mínima para saltar?",
    ],
    input_modes=["text/plain"],
    output_modes=["text/plain"],
)

# skill 2: lo que hace agente_agenda (usa consultar_clima y agendar_cita)
SKILL_AGENDA = AgentSkill(
    id="agenda_de_saltos",
    name="Clima y agenda de saltos",
    description=(
        "Consulta el pronóstico en la zona de salto para una fecha (hasta 16 días "
        "adelante) y agenda citas de salto solo si el clima es seguro (viento, "
        "ráfagas, lluvia y nubes)."
    ),
    tags=["agenda", "clima", "citas"],
    examples=[
        "¿Cómo estará el clima el 2026-10-24 para saltar?",
        "Quiero agendar una cita para el 2026-10-30 a nombre de Ana",
    ],
    input_modes=["text/plain"],
    output_modes=["text/plain"],
)


def construir_tarjeta(url_publica):
    """arma la agent card con la url donde los clientes pueden encontrar al agente.

    url_publica: la url base del servidor a2a, por ejemplo http://127.0.0.1:8000
    """
    return AgentCard(
        # identidad: una sola cara publica, aunque por dentro sean 3 agentes
        name="Agente de Parachute S.A.",
        description=(
            "Asistente de Parachute S.A. para su evento de paracaidismo en Guatemala "
            "2026: responde preguntas frecuentes y agenda saltos revisando el clima. "
            "Internamente es un sistema multiagente (manager + especialistas)."
        ),
        version="1.0.0",
        # solo texto plano de entrada y salida (la respuesta se lee en terminal)
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        # solo se declara lo que de verdad esta implementado: no hay streaming
        # ni notificaciones push, y la card no tiene version extendida
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
            extended_agent_card=False,
        ),
        # donde contestar y con que protocolo: json-rpc sobre http (a2a 1.0)
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                url=url_publica,
                protocol_version="1.0",
            )
        ],
        skills=[SKILL_FAQS, SKILL_AGENDA],
    )
