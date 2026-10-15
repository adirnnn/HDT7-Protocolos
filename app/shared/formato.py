"""limpieza final de la respuesta para que salga en texto plano.

los evals de hdt6 mostraron que el modelo a veces usa markdown (listas con
guiones, negritas) aunque las instrucciones digan texto plano. las
instrucciones se reforzaron, pero como el modelo no es determinista se agrega
esta limpieza como red de seguridad: es codigo normal, siempre hace lo mismo.

solo quita formato, no cambia palabras ni datos de la respuesta.
"""

import re

# caracteres "raros" que el modelo usa a veces y que algunas consolas de
# windows (cp1252) no pueden imprimir: espacio angosto, guion que no corta, etc.
_REEMPLAZOS = {
    " ": " ",  # espacio angosto sin corte (el modelo lo pone antes de km/h)
    " ": " ",  # espacio sin corte
    "‑": "-",  # guion que no corta (aparece en fechas como 2026-10-24)
    "–": "-",  # guion medio
    "—": "-",  # guion largo
}

# patrones de markdown que se quitan
_NEGRITA = re.compile(r"(\*\*|__)(.+?)\1")  # **texto** o __texto__ -> texto
_ENCABEZADO = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)  # "## titulo" -> "titulo"
_VINETA = re.compile(r"^\s*[-*•]\s+", re.MULTILINE)  # "- item" o "* item" -> "item"
_ESPACIOS = re.compile(r"[ \t]+")  # varios espacios seguidos -> uno


def a_texto_plano(texto):
    """devuelve el texto sin markdown, en un solo bloque de parrafos."""
    for raro, normal in _REEMPLAZOS.items():
        texto = texto.replace(raro, normal)
    texto = _NEGRITA.sub(r"\2", texto)
    texto = _ENCABEZADO.sub("", texto)
    texto = _VINETA.sub("", texto)

    # se arman parrafos: las lineas seguidas se unen con espacio (antes eran
    # items de una lista) y una linea vacia separa parrafos
    parrafos = []
    for bloque in re.split(r"\n\s*\n", texto.strip()):
        lineas = [l.strip() for l in bloque.splitlines() if l.strip()]
        # si una linea no termina en puntuacion, se le agrega un punto para que
        # al unirla con la siguiente no quede una frase pegada a otra
        unidas = " ".join(l if l[-1] in ".:;!?," else l + "." for l in lineas)
        parrafos.append(_ESPACIOS.sub(" ", unidas))
    return "\n\n".join(parrafos)
