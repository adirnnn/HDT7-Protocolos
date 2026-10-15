"""busqueda de faqs contra la misma tabla pgvector que llena infra/cargar.py."""

import psycopg
from pgvector.psycopg import register_vector

from .config import env, requerido

TABLA = "faqs"
_MODELO = None
_CONEXION = None


def _conectar():
    global _CONEXION
    if _CONEXION is None or _CONEXION.closed:
        # connect_timeout: si postgres no esta levantado se falla en 5 s con un
        # error claro, en vez de quedarse esperando hasta el timeout de la tool
        conn = psycopg.connect(requerido("DATABASE_URL"), connect_timeout=5)
        register_vector(conn)
        conn.autocommit = True  # aca solo hacemos SELECT
        _CONEXION = conn
    return _CONEXION


def _modelo():
    # mismo modelo que usa infra/cargar.py para llenar la tabla, tiene que
    # coincidir o los vectores no son comparables
    global _MODELO
    if _MODELO is None:
        from sentence_transformers import SentenceTransformer

        nombre = env("EMBEDDING_MODEL", "paraphrase-multilingual-MiniLM-L12-v2")
        _MODELO = SentenceTransformer(nombre)
    return _MODELO


def precargar_modelo():
    # carga el modelo de embeddings de una vez (tarda unos segundos). la usa el
    # servidor mcp al arrancar para que la primera busqueda no sea lenta
    _modelo()


def _vector_literal(v):
    return "[" + ",".join(repr(float(x)) for x in v) + "]"


def buscar_en_faqs_raw(consulta, k=4):
    # busqueda semantica en la tabla faqs, devuelve texto listo para el llm
    umbral = float(env("UMBRAL_SIMILITUD", "0.35"))
    k = max(1, min(int(k or 4), 10))
    # show_progress_bar=False para que la barra de progreso no ensucie los logs
    vector = _modelo().encode([consulta], normalize_embeddings=True, show_progress_bar=False)[0]
    vector = _vector_literal(vector)

    conn = _conectar()
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT id, categoria, pregunta, respuesta,
                   1 - (embedding <=> %s::vector) AS score
            FROM {TABLA}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (vector, vector, k),
        )
        filas = cur.fetchall()

    utiles = [f for f in filas if f[4] >= umbral]
    if not utiles:
        return "SIN_RESULTADOS"

    partes = []
    for id_, categoria, pregunta, respuesta, score in utiles:
        partes.append(
            f"[{id_}] categoria: {categoria} | score: {score:.2f}\n"
            f"PREGUNTA: {pregunta}\n"
            f"RESPUESTA: {respuesta}"
        )
    return "\n\n".join(partes)
