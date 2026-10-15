"""calendarizacion de citas: revisa el clima antes de confirmar y guarda en un json."""

import json
import os
import uuid
from datetime import datetime, timezone

from .clima import consultar_clima_raw
from .config import env

# archivo donde se guardan las citas. por defecto app/data/citas.json (esta en
# el .gitignore). con la variable CITAS_FILE se puede apuntar a otro archivo,
# por ejemplo uno temporal para que las pruebas no ensucien las citas reales
_RUTA_DEFECTO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "citas.json")
_RUTA_STORE = env("CITAS_FILE") or _RUTA_DEFECTO


def _leer_citas():
    if not os.path.exists(_RUTA_STORE):
        return []
    with open(_RUTA_STORE, encoding="utf-8") as f:
        return json.load(f)


def _guardar_cita(cita):
    # crea la carpeta si no existe (el "or ." cubre un CITAS_FILE sin carpeta)
    os.makedirs(os.path.dirname(_RUTA_STORE) or ".", exist_ok=True)
    citas = _leer_citas()
    citas.append(cita)
    with open(_RUTA_STORE, "w", encoding="utf-8") as f:
        json.dump(citas, f, ensure_ascii=False, indent=2)


def _describir_clima(datos):
    return (
        f"temperatura {datos['temperatura']} C, viento {datos['viento']} km/h, "
        f"rafagas {datos['rafaga']} km/h, precipitacion {datos['precipitacion']} mm, "
        f"nubes {datos['nubes']}%"
    )


def agendar_cita_raw(fecha_str, nombre=None):
    """revisa el clima de la fecha pedida y, si las condiciones lo permiten,
    guarda la cita. devuelve texto listo para que el llm se lo explique al
    usuario.

    cambio de hdt7: antes el texto empezaba con codigos internos
    (CITA_CONFIRMADA, NO_SE_PUDO_AGENDAR) y los evals de hdt6 mostraron que el
    modelo se los repetia al usuario. ahora la tool ya devuelve frases normales
    en espanol, asi no hay codigo que se pueda "filtrar" a la respuesta."""
    datos = consultar_clima_raw(fecha_str)

    if "error" in datos:
        # fecha mal escrita, pasada o a mas de 16 dias: no se agenda nada
        return f"No se pudo agendar la cita: {datos['error']}. No se guardo ninguna cita."

    evaluacion = datos["evaluacion"]
    resumen = _describir_clima(datos)

    if evaluacion["veredicto"] == "NO_SEGURO":
        razones = "; ".join(evaluacion["problemas"])
        # clima peligroso: se explica el motivo y no se guarda nada
        return (
            f"No se pudo agendar la cita: el {datos['fecha']} el clima no es seguro para saltar "
            f"({razones}). Datos: {resumen}. No se guardo ninguna cita; sugiere elegir otra fecha."
        )

    cita = {
        "id": uuid.uuid4().hex[:8],
        "fecha": datos["fecha"],
        "nombre": nombre or None,
        "veredicto": evaluacion["veredicto"],
        "clima": resumen,
        "creada": datetime.now(timezone.utc).isoformat(),
    }
    _guardar_cita(cita)

    if evaluacion["veredicto"] == "MARGINAL":
        aviso = (
            "condiciones marginales, solo recomendable con instructor tandem experimentado"
        )
    else:
        aviso = "condiciones ideales para saltar"

    # cita guardada: se da la fecha, el codigo de reserva y el aviso del clima
    return (
        f"Cita confirmada y guardada para el {datos['fecha']}, codigo de reserva {cita['id']}. "
        f"Clima: {aviso}. Datos del clima: {resumen}."
    )
