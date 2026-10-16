"""prueba de integracion completa: cliente a2a -> servidor a2a -> agentes -> mcp.

usa las mismas funciones de la terminal (terminal/cliente_a2a.py) para mandar
mensajes reales por la red, asi tambien se prueba el codigo del cliente.
no corre agentes ni herramientas en este proceso: todo pasa por los servidores.

modo normal (los 2 servidores corriendo y docker con la base cargada):
    python pruebas/probar_a2a.py
modo sin mcp (solo el servidor a2a corriendo, el mcp apagado a proposito):
    python pruebas/probar_a2a.py --sin-mcp

entre turnos hay una pausa (PAUSA_S, 20 s por defecto) para no chocar con el
limite de tokens por minuto del plan gratis de groq. ningun caso guarda citas.

ojo: las respuestas las escribe un modelo, asi que los casos marcados como
"(modelo)" revisan comportamiento del llm y pueden variar entre corridas.
"""

import asyncio
import os
import re
import sys
from datetime import date, timedelta

# se importan las funciones de la terminal para reusar su cliente a2a
_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_RAIZ, "terminal"))

import httpx  # noqa: E402
from a2a.client import ClientConfig, create_client  # noqa: E402

from cliente_a2a import TIMEOUT, URL_A2A, descubrir_agente, enviar, leer_respuesta  # noqa: E402

PAUSA = float(os.environ.get("PAUSA_S", "20"))
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]

resultados = []


def revisar(nombre, condicion, detalle=""):
    resultados.append((nombre, bool(condicion)))
    print(f"[{'PASA' if condicion else 'FALLA'}] {nombre}" + (f"\n        -> {detalle[:220]}" if detalle else ""))


def menciona_fecha(texto, fecha):
    """true si el texto menciona la fecha en iso (2026-10-18) o en palabras (18 de octubre)."""
    en_palabras = f"{fecha.day} de {MESES[fecha.month - 1]}"
    return fecha.isoformat() in texto or en_palabras in texto.lower()


async def turno(cliente, texto, context_id=None):
    """manda un mensaje y devuelve (texto, context_id, es_error, task_id)."""
    respuesta = await enviar(cliente, texto, context_id)
    await asyncio.sleep(PAUSA)
    return leer_respuesta(respuesta)


async def pruebas_card(tarjeta):
    # descubrimiento: identidad, skills y capacidades declaradas
    revisar("agent card: identidad", tarjeta.name == "Agente de Parachute S.A.", tarjeta.name)
    ids = {s.id for s in tarjeta.skills}
    revisar("agent card: skills", ids == {"preguntas_frecuentes", "agenda_de_saltos"}, ", ".join(sorted(ids)))
    revisar("agent card: sin streaming ni push (no implementados)",
            not tarjeta.capabilities.streaming and not tarjeta.capabilities.push_notifications)
    interfaz = tarjeta.supported_interfaces[0]
    revisar("agent card: interfaz json-rpc", interfaz.protocol_binding == "JSONRPC", interfaz.url)


async def pruebas_normales(cliente):
    # 1. skill de faqs: el manager delega en agente_faqs -> mcp buscar_en_faqs
    texto, _, error, _ = await turno(cliente, "¿Cuál es el peso máximo para saltar?")
    revisar("faqs: peso maximo (dato del corpus: 100 kg)", not error and "100" in texto, texto)

    # 2. ficha plantilla (fallo de hdt6): no debe afirmar que hay parqueo
    texto, _, error, _ = await turno(cliente, "¿Hay parqueo disponible en el lugar del evento?")
    afirma = re.search(r"(?<!no )hay parqueo|s[ií],? (hay|cuenta)", texto.lower())
    revisar("faqs: ficha plantilla sin inventar (modelo)", not error and not afirma and "soporte" in texto.lower(), texto)

    # 3. skill de agenda: delega en agente_agenda -> mcp consultar_clima (open-meteo real)
    fecha = date.today() + timedelta(days=3)
    texto, ctx, error, _ = await turno(
        cliente, f"¿Cómo estará el clima el {fecha.isoformat()} para saltar? Solo quiero saber."
    )
    revisar("agenda: consulta de clima", not error and "km/h" in texto, texto)

    # 4. continuidad: mismo context_id, el agente recuerda el turno anterior
    pregunta = "¿De qué fecha te acabo de preguntar el clima? Responde solo la fecha."
    texto, ctx2, error, _ = await turno(cliente, pregunta, ctx)
    revisar("contexto: recuerda la fecha en la misma conversacion (modelo)",
            not error and ctx2 == ctx and menciona_fecha(texto, fecha), texto)

    # 5. aislamiento: la misma pregunta en una conversacion nueva no sabe la fecha
    texto, ctx3, error, _ = await turno(cliente, pregunta, None)
    revisar("contexto: otra conversacion no la conoce", ctx3 != ctx and not menciona_fecha(texto, fecha), texto)

    # 6. entrada invalida: mensaje solo con espacios -> tarea rechazada
    texto, _, error, _ = await turno(cliente, "   ")
    revisar("entrada invalida: mensaje vacio rechazado", error, texto)


async def pruebas_sin_mcp(cliente):
    # el servidor a2a responde, pero no puede usar herramientas
    texto, _, error, _ = await turno(cliente, "¿Cuál es el peso máximo para saltar?")
    revisar("mcp apagado: tarea fallida con mensaje accionable", error and "mcp_server/servidor.py" in texto, texto)


async def main(sin_mcp):
    print(f"servidor A2A: {URL_A2A} | pausa entre turnos: {PAUSA:.0f} s\n")
    async with httpx.AsyncClient(timeout=TIMEOUT) as http:
        tarjeta = await descubrir_agente(http)
        if tarjeta is None:
            return False
        cliente = await create_client(agent=tarjeta, client_config=ClientConfig(streaming=False, httpx_client=http))
        try:
            await pruebas_card(tarjeta)
            await (pruebas_sin_mcp(cliente) if sin_mcp else pruebas_normales(cliente))
        finally:
            await cliente.close()

    pasaron = sum(1 for _, ok in resultados if ok)
    print(f"\n{pasaron}/{len(resultados)} comprobaciones pasaron")
    return pasaron == len(resultados)


if __name__ == "__main__":
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    sys.exit(0 if asyncio.run(main("--sin-mcp" in sys.argv)) else 1)
