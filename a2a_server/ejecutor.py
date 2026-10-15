"""ejecutor a2a: el puente entre el protocolo a2a y el sistema multiagente.

el sdk de a2a recibe la request json-rpc, arma un RequestContext (mensaje del
usuario, context_id, task_id) y llama a execute(). aca se traduce eso a lo que
entiende el sistema de hdt6 (una lista de mensajes para Runner.run) y la
respuesta del manager se devuelve como una tarea a2a terminada.

flujo de cada mensaje:
1. se crea la tarea a2a (estado submitted) y se pasa a working
2. se abre una conexion mcp propia para este turno (con ids en cabeceras)
3. se corre agente_manager con el historial de esa conversacion
4. la respuesta va como artefacto de la tarea y la tarea pasa a completed
   (o a failed con un mensaje entendible si algo sale mal)
"""

import asyncio
import logging
import time

from a2a.helpers import get_message_text, new_task_from_user_message, new_text_message, new_text_part
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.server.tasks import TaskUpdater
from a2a.types import TaskState
from a2a.utils.errors import UnsupportedOperationError
from agents import Runner
from agents.exceptions import AgentsException, UserError
from openai import APIError

from centralizado import URL_MCP, conectar_mcp, construir_manager

log = logging.getLogger("parachute.a2a")


class MemoriaConversaciones:
    """guarda el historial de cada conversacion a2a, indexado por context_id.

    vive solo en memoria: si se reinicia el servidor a2a se pierden todas las
    conversaciones (es una demo, no hace falta base de datos). cada cliente
    tiene su propio context_id, asi que las conversaciones no se mezclan.
    """

    def __init__(self):
        # context_id -> lista de mensajes en el formato del sdk de agentes
        self._historiales = {}
        # context_id -> lock, para que dos mensajes de la misma conversacion
        # no se procesen a la vez y se pisen el historial
        self._locks = {}

    def lock(self, context_id):
        # setdefault crea el lock la primera vez que aparece la conversacion
        return self._locks.setdefault(context_id, asyncio.Lock())

    def leer(self, context_id):
        # conversacion nueva -> historial vacio
        return self._historiales.get(context_id, [])

    def guardar(self, context_id, historial):
        self._historiales[context_id] = historial


def _mensaje_de_error(e):
    """convierte una excepcion en un texto que le sirva al usuario de la terminal.

    no se muestra el traceback ni datos internos, solo que paso y que hacer.
    """
    if isinstance(e, UserError) and "MCP" in str(e):
        # el sdk de agentes no pudo conectarse al servidor mcp
        return (
            f"El servidor MCP de herramientas no está disponible en {URL_MCP}. "
            "Inícialo con: python mcp_server/servidor.py (y espera a que diga que está listo)."
        )
    if isinstance(e, APIError):
        # error de groq (rate limit, api key, caida del servicio)
        return f"El proveedor del modelo (Groq) devolvió un error: {type(e).__name__}. Intenta de nuevo en un momento."
    if isinstance(e, AgentsException):
        return f"El sistema multiagente no pudo terminar la respuesta ({type(e).__name__})."
    return f"Error inesperado al procesar el mensaje ({type(e).__name__})."


class ParachuteExecutor(AgentExecutor):
    """implementacion de AgentExecutor del sdk de a2a para parachute s.a."""

    def __init__(self):
        self.memoria = MemoriaConversaciones()

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        # 1. tarea a2a: si el cliente no mando una tarea existente, se crea una
        # nueva a partir de su mensaje y se publica en la cola de eventos
        tarea = context.current_task
        if tarea is None:
            tarea = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(tarea)

        # TaskUpdater es el ayudante del sdk para cambiar el estado de la tarea
        actualizador = TaskUpdater(event_queue=event_queue, task_id=tarea.id, context_id=tarea.context_id)
        texto = get_message_text(context.message).strip()

        # ids cortos para los logs (los completos son uuid largos)
        ctx_corto, task_corto = tarea.context_id[:8], tarea.id[:8]
        log.info("solicitud recibida ctx=%s task=%s (%d caracteres)", ctx_corto, task_corto, len(texto))

        # un mensaje vacio no se manda al modelo: se rechaza con explicacion
        if not texto:
            await actualizador.reject(new_text_message("El mensaje no tiene texto.", context_id=tarea.context_id, task_id=tarea.id))
            log.info("task=%s rechazada: mensaje sin texto", task_corto)
            return

        await actualizador.start_work()

        # el lock de la conversacion asegura que los turnos de un mismo
        # context_id se procesen en orden
        async with self.memoria.lock(tarea.context_id):
            inicio = time.perf_counter()
            try:
                respuesta = await self._correr_sistema(tarea.context_id, tarea.id, texto)
            except Exception as e:  # noqa: BLE001  cualquier fallo termina en failed
                ms = (time.perf_counter() - inicio) * 1000
                log.error("task=%s FALLO en %.0f ms: %s: %s", task_corto, ms, type(e).__name__, e)
                await actualizador.failed(
                    new_text_message(_mensaje_de_error(e), context_id=tarea.context_id, task_id=tarea.id)
                )
                return

        # 4. la respuesta del sistema es el resultado (artefacto) de la tarea
        ms = (time.perf_counter() - inicio) * 1000
        await actualizador.add_artifact(parts=[new_text_part(respuesta, media_type="text/plain")], name="respuesta")
        await actualizador.complete()
        log.info("task=%s COMPLETADA en %.0f ms (respuesta de %d caracteres)", task_corto, ms, len(respuesta))

    async def _correr_sistema(self, context_id, task_id, texto):
        """corre un turno del sistema multiagente y actualiza el historial."""
        # historial previo de esta conversacion + el mensaje nuevo
        historial = self.memoria.leer(context_id) + [{"role": "user", "content": texto}]

        # 2. conexion mcp solo para este turno. asi cada conversacion usa su
        # propia sesion mcp (nada compartido entre clientes) y si el servidor
        # mcp se reinicia, el siguiente turno se vuelve a conectar solo.
        # las cabeceras llevan los ids a2a para que el log mcp los muestre
        cabeceras = {"x-parachute-context": context_id, "x-parachute-task": task_id}
        async with conectar_mcp(cabeceras) as servidor_mcp:
            log.info("task=%s conectado a MCP, ejecutando agente_manager", task_id[:8])
            # 3. el mismo sistema de hdt6: manager + especialistas
            manager = construir_manager(servidor_mcp)
            resultado = await Runner.run(manager, historial)

        # se guarda el historial completo (incluye las llamadas a tools) solo
        # si el turno salio bien; si fallo, la pregunta no queda en memoria
        self.memoria.guardar(context_id, resultado.to_input_list())
        return str(resultado.final_output)

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        # no se implementa cancelacion (cada turno dura pocos segundos) y por
        # eso tampoco se anuncia en la agent card
        raise UnsupportedOperationError(message="Este agente no soporta cancelar tareas.")
