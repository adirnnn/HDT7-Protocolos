"""revision estatica (sin servidores): que cada componente respete sus limites.

lee el codigo con ast (el arbol sintactico de python) sin ejecutarlo y revisa:
* la terminal no importa el sdk de agentes, ni app/, ni el sdk de mcp: solo
  puede hablar a2a, asi que no puede correr agentes ni herramientas directo.
* en app/ ya no queda ninguna @function_tool local: las herramientas de negocio
  solo existen en el servidor mcp (no hay camino alternativo que lo salte).
* el servidor mcp publica exactamente las 3 herramientas del inventario.

se corre con:  python pruebas/revisar_estructura.py
"""

import ast
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# modulos que la terminal NO puede importar
PROHIBIDOS_TERMINAL = {"agents", "mcp", "centralizado", "shared", "openai"}

resultados = []


def revisar(nombre, condicion, detalle=""):
    resultados.append(bool(condicion))
    print(f"[{'PASA' if condicion else 'FALLA'}] {nombre}" + (f"  -> {detalle}" if detalle else ""))


def arbol(ruta):
    with open(os.path.join(RAIZ, ruta), encoding="utf-8") as f:
        return ast.parse(f.read())


def modulos_importados(tree):
    """devuelve el primer nombre de cada import (ej. "agents" de "agents.mcp")."""
    nombres = set()
    for nodo in ast.walk(tree):
        if isinstance(nodo, ast.Import):
            nombres.update(a.name.split(".")[0] for a in nodo.names)
        elif isinstance(nodo, ast.ImportFrom) and nodo.module:
            nombres.add(nodo.module.split(".")[0])
    return nombres


def decoradores(tree):
    """devuelve los textos de todos los decoradores (ej. "servidor.tool")."""
    textos = []
    for nodo in ast.walk(tree):
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
            textos.extend(ast.unparse(d) for d in nodo.decorator_list)
    return textos


def main():
    # 1. la terminal solo es cliente a2a
    importados = modulos_importados(arbol("terminal/cliente_a2a.py"))
    malos = importados & PROHIBIDOS_TERMINAL
    revisar("terminal no importa agentes, app/ ni mcp", not malos, ", ".join(sorted(importados)))

    # 2. no quedan tools locales en app/
    con_function_tool = []
    for carpeta, _, archivos in os.walk(os.path.join(RAIZ, "app")):
        for nombre in archivos:
            if nombre.endswith(".py"):
                ruta = os.path.relpath(os.path.join(carpeta, nombre), RAIZ)
                if any("function_tool" in d for d in decoradores(arbol(ruta))):
                    con_function_tool.append(ruta)
    revisar("app/ sin @function_tool locales", not con_function_tool, ", ".join(con_function_tool))

    # 3. el servidor mcp registra las 3 herramientas del inventario
    tree = arbol("mcp_server/servidor.py")
    registradas = set()
    for nodo in ast.walk(tree):
        if isinstance(nodo, ast.AsyncFunctionDef):
            for d in nodo.decorator_list:
                if isinstance(d, ast.Call) and ast.unparse(d.func) == "servidor.tool":
                    registradas.add(nodo.name)
    esperadas = {"buscar_en_faqs", "consultar_clima", "agendar_cita"}
    revisar("servidor mcp registra las 3 tools", registradas == esperadas, ", ".join(sorted(registradas)))

    print(f"\n{sum(resultados)}/{len(resultados)} comprobaciones pasaron")
    return all(resultados)


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
