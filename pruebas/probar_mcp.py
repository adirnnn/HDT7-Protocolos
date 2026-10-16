"""prueba de integracion del servidor mcp (sin agentes ni a2a de por medio).

se conecta al servidor mcp real por streamable http con el cliente oficial
del sdk de mcp, lista las herramientas y ejecuta casos representativos. sirve
para demostrar que el servidor funciona solo, como componente independiente.

requisitos: el servidor mcp corriendo (python mcp_server/servidor.py) y, para
el caso de faqs, docker con la base cargada (python infra/cargar.py).

se corre con:  python pruebas/probar_mcp.py
ninguna llamada modifica datos: agendar_cita se prueba con una fecha pasada,
que se rechaza antes de guardar nada.
"""

import asyncio
import os
import sys
from datetime import date, timedelta

from dotenv import load_dotenv
from mcp.client import ClientSession
from mcp.client.streamable_http import streamable_http_client

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
URL_MCP = os.environ.get("MCP_URL", "http://127.0.0.1:8001/mcp")

# las 3 herramientas que tiene que publicar el servidor, ni una mas
HERRAMIENTAS_ESPERADAS = {"buscar_en_faqs", "consultar_clima", "agendar_cita"}

resultados = []  # lista de (nombre de la prueba, paso o no, detalle)


def revisar(nombre, condicion, detalle=""):
    """registra el resultado de una comprobacion y lo imprime."""
    resultados.append((nombre, bool(condicion)))
    print(f"[{'PASA' if condicion else 'FALLA'}] {nombre}" + (f"  -> {detalle}" if detalle else ""))


def texto(resultado):
    # el resultado de una tool mcp es una lista de bloques; aca todos son texto
    return " ".join(b.text for b in resultado.content if getattr(b, "text", None))


async def main():
    print(f"conectando a {URL_MCP} por streamable http...\n")
    # streamable_http_client abre el transporte http; ClientSession habla el
    # protocolo mcp encima (initialize, tools/list, tools/call)
    async with streamable_http_client(URL_MCP) as flujos:
        lectura, escritura = flujos[0], flujos[1]
        async with ClientSession(lectura, escritura) as sesion:
            await sesion.initialize()

            # 1. descubrimiento de herramientas
            lista = await sesion.list_tools()
            nombres = {t.name for t in lista.tools}
            revisar("lista de herramientas", nombres == HERRAMIENTAS_ESPERADAS, ", ".join(sorted(nombres)))
            for t in lista.tools:
                # cada herramienta tiene que traer descripcion y esquema de entrada
                revisar(f"esquema de {t.name}", t.description and t.input_schema.get("properties"))

            # 2. clima de una fecha valida (open-meteo real)
            fecha = (date.today() + timedelta(days=2)).isoformat()
            r = await sesion.call_tool("consultar_clima", {"fecha": fecha})
            revisar("consultar_clima fecha valida", not r.is_error and "Pronostico" in texto(r), texto(r)[:90])

            # 3. fecha que no se entiende: no es un error del servidor, es un
            # mensaje para el usuario (igual que la tool original)
            r = await sesion.call_tool("consultar_clima", {"fecha": "ayer"})
            revisar("consultar_clima fecha invalida", not r.is_error and "Fecha no valida" in texto(r), texto(r)[:90])

            # 4. argumento fuera del esquema (k maximo es 10): lo rechaza el sdk
            r = await sesion.call_tool("buscar_en_faqs", {"consulta": "peso maximo", "k": 50})
            revisar("buscar_en_faqs k invalido -> isError", r.is_error, texto(r)[:90])

            # 5. argumento obligatorio que falta
            r = await sesion.call_tool("agendar_cita", {})
            revisar("agendar_cita sin fecha -> isError", r.is_error, texto(r)[:90])

            # 6. busqueda real en pgvector (necesita docker)
            r = await sesion.call_tool("buscar_en_faqs", {"consulta": "¿Cuál es el peso máximo para saltar?"})
            revisar("buscar_en_faqs en pgvector", not r.is_error and "FAQ-" in texto(r), texto(r)[:90])

            # 7. agendar con fecha pasada: se rechaza sin guardar nada
            r = await sesion.call_tool("agendar_cita", {"fecha": "2026-01-01", "nombre": "Prueba HDT7"})
            revisar("agendar_cita fecha pasada no agenda", not r.is_error and "No se pudo agendar" in texto(r), texto(r)[:90])

    pasaron = sum(1 for _, ok in resultados if ok)
    print(f"\n{pasaron}/{len(resultados)} comprobaciones pasaron")
    return pasaron == len(resultados)


if __name__ == "__main__":
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    try:
        ok = asyncio.run(main())
    except Exception as e:  # noqa: BLE001
        print(f"no se pudo hablar con el servidor MCP en {URL_MCP}: {type(e).__name__}: {e}")
        print("inicialo con:  python mcp_server/servidor.py")
        ok = False
    sys.exit(0 if ok else 1)
