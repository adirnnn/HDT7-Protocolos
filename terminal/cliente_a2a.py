"""terminal de parachute s.a. como cliente a2a.

reemplaza al loop de terminal de hdt6 (app/shared/cli.py), que corria el
Runner y los agentes en este mismo proceso. ahora la terminal no sabe nada de
agentes ni de herramientas: solo habla a2a por la red con el servidor
a2a_server/servidor.py, que es quien aloja el sistema multiagente.

por eso este archivo no importa nada de app/ ni del sdk de agentes, solo el
cliente del sdk oficial de a2a.

se corre en su propia terminal:  python terminal/cliente_a2a.py
comandos: "/nuevo" empieza una conversacion nueva, "Bye" (o Ctrl+C) sale.
"""

import asyncio
import os
import sys

import httpx
from a2a.client import A2ACardResolver, A2AClientError, A2AClientTimeoutError, ClientConfig, create_client
from a2a.helpers import get_message_text, new_text_message
from a2a.types import Role, SendMessageRequest, TaskState
from dotenv import load_dotenv

# --- configuracion ---------------------------------------------------------------
# se lee el mismo .env de la raiz del repo, pero sin importar shared/config.py
# (la terminal no debe depender del codigo del sistema multiagente)
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

# url base del servidor a2a; de aca se descarga la agent card
URL_A2A = os.environ.get("A2A_URL", "http://127.0.0.1:8000")
# segundos maximos esperando la respuesta de un turno. es largo a proposito:
# con el plan gratis de groq un turno puede esperar ~40 s por el limite de
# tokens por minuto (ver README)
TIMEOUT = float(os.environ.get("A2A_TIMEOUT", "180"))

SALIDAS = {"bye", "adios", "adiós", "salir"}
NUEVA = "/nuevo"


def _despedida():
    print("\nAgente> Gracias por tu interes en Parachute S.A. Hasta pronto.\n")


def _configurar_consola():
    # fuerzo utf-8 para que los acentos se vean bien en la consola de windows
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass


async def descubrir_agente(http):
    """descarga la agent card del servidor a2a (/.well-known/agent-card.json).

    devuelve la card o None si no se pudo, despues de explicar que hacer.
    """
    try:
        resolvedor = A2ACardResolver(httpx_client=http, base_url=URL_A2A)
        return await resolvedor.get_agent_card()
    except (httpx.HTTPError, A2AClientError) as e:
        print(f"\nNo se pudo descargar la agent card de {URL_A2A} ({type(e).__name__}).")
        print("Revisa que el servidor A2A este corriendo:  python a2a_server/servidor.py")
        print("(y antes el servidor MCP:  python mcp_server/servidor.py)\n")
        return None


def mostrar_bienvenida(tarjeta):
    """imprime la identidad y las skills que anuncia la agent card."""
    print("=" * 64)
    print(f" {tarjeta.name} (v{tarjeta.version})  |  cliente A2A")
    print(f" servidor: {URL_A2A}")
    print(" skills:")
    for skill in tarjeta.skills:
        print(f"   - {skill.name}: ej. \"{skill.examples[0]}\"" if skill.examples else f"   - {skill.name}")
    print(f'  "{NUEVA}" = conversacion nueva  |  "Bye" o Ctrl+C = salir')
    print("=" * 64 + "\n")


def leer_respuesta(respuesta):
    """saca el texto y el context_id de una respuesta a2a.

    el servidor puede contestar de varias formas y aca se manejan todas:
    * respuesta.task: una tarea. si termino bien, el texto viene en sus
      artefactos; si fallo o fue rechazada, el motivo viene en el mensaje de
      estado (status.message).
    * respuesta.message: un mensaje directo del agente, sin tarea.
    * status_update / artifact_update: solo llegan en modo streaming, que este
      agente no anuncia; si llegan igual se muestra lo que traigan.

    devuelve (texto, context_id, es_error, id_tarea).
    """
    if respuesta.HasField("task"):
        tarea = respuesta.task
        estado = tarea.status.state
        # texto de todos los artefactos (normalmente uno: "respuesta")
        partes = [p.text for artefacto in tarea.artifacts for p in artefacto.parts if p.text]
        texto = "\n".join(partes)
        if not texto and tarea.status.HasField("message"):
            texto = get_message_text(tarea.status.message)
        es_error = estado in (TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_REJECTED)
        if estado not in (TaskState.TASK_STATE_COMPLETED, TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_REJECTED):
            # cualquier otro estado (por ejemplo working) no es una respuesta final
            texto = texto or f"La tarea quedo en estado {TaskState.Name(estado)}."
        return texto, tarea.context_id, es_error, tarea.id

    if respuesta.HasField("message"):
        mensaje = respuesta.message
        return get_message_text(mensaje), mensaje.context_id or None, False, None

    if respuesta.HasField("status_update"):
        evento = respuesta.status_update
        texto = get_message_text(evento.status.message) if evento.status.HasField("message") else ""
        return texto, evento.context_id, evento.status.state == TaskState.TASK_STATE_FAILED, evento.task_id

    if respuesta.HasField("artifact_update"):
        evento = respuesta.artifact_update
        texto = "\n".join(p.text for p in evento.artifact.parts if p.text)
        return texto, evento.context_id, False, evento.task_id

    return "(respuesta vacia del servidor)", None, True, None


async def enviar(cliente, texto, context_id):
    """manda un mensaje del usuario por a2a y devuelve la ultima respuesta.

    context_id: None en el primer mensaje de una conversacion (el servidor
    crea uno nuevo); despues se manda el que devolvio el servidor, para que
    recuerde la conversacion.
    """
    mensaje = new_text_message(texto, context_id=context_id, role=Role.ROLE_USER)
    ultima = None
    # send_message es un generador async: sin streaming llega una sola
    # respuesta, pero se recorre completo por si llegan varias
    async for respuesta in cliente.send_message(SendMessageRequest(message=mensaje)):
        ultima = respuesta
    return ultima


async def _loop():
    _configurar_consola()

    # un solo cliente http para todo el programa, con el timeout largo
    async with httpx.AsyncClient(timeout=TIMEOUT) as http:
        tarjeta = await descubrir_agente(http)
        if tarjeta is None:
            return

        # el cliente a2a se arma a partir de la card: de ahi saca la url y el
        # protocolo (json-rpc). sin streaming porque el servidor no lo anuncia
        cliente = await create_client(
            agent=tarjeta,
            client_config=ClientConfig(streaming=False, httpx_client=http),
        )
        mostrar_bienvenida(tarjeta)

        # la memoria de la conversacion vive en el servidor; aca solo se
        # guarda el context_id para que el servidor sepa cual es
        context_id = None
        try:
            while True:
                try:
                    pregunta = input("Tu> ").strip()
                except EOFError:
                    break

                if not pregunta:
                    continue
                if pregunta.lower() in SALIDAS:
                    break
                if pregunta.lower() == NUEVA:
                    context_id = None
                    print("\n(conversacion nueva: el agente ya no recuerda lo anterior)\n")
                    continue

                print("  ...esperando al agente")
                try:
                    respuesta = await enviar(cliente, pregunta, context_id)
                except A2AClientTimeoutError:
                    print(f"\nAgente> [error] el servidor no respondio en {TIMEOUT:.0f} s. Intenta de nuevo.\n")
                    continue
                except A2AClientError as e:
                    # servidor apagado, error http o respuesta invalida
                    print(f"\nAgente> [error] no se pudo hablar con el servidor A2A: {e}")
                    print("        revisa que siga corriendo:  python a2a_server/servidor.py\n")
                    continue

                if respuesta is None:
                    print("\nAgente> [error] el servidor no devolvio ninguna respuesta.\n")
                    continue

                texto, nuevo_context, es_error, id_tarea = leer_respuesta(respuesta)
                # el servidor puede asignar el context_id en el primer mensaje
                context_id = nuevo_context or context_id
                etiqueta = "[error] " if es_error else ""
                print(f"\nAgente> {etiqueta}{texto}")
                # ids cortos para buscar este turno en los logs a2a y mcp
                if context_id:
                    print(f"        (ctx={context_id[:8]} task={(id_tarea or '-')[:8]})\n")
        finally:
            await cliente.close()

    _despedida()


def main():
    try:
        asyncio.run(_loop())
    except KeyboardInterrupt:
        _despedida()


if __name__ == "__main__":
    main()
