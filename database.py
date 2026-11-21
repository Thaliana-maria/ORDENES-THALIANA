import logging
import os
from functools import lru_cache

from dotenv import load_dotenv
from fastapi import HTTPException
from supabase import Client, create_client

load_dotenv()
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    url = (os.getenv("SUPABASE_URL") or "").strip()
    key = (os.getenv("SUPABASE_KEY") or "").strip()
    if not url or not key:
        raise HTTPException(
            status_code=503,
            detail="El servicio no está configurado: faltan SUPABASE_URL o SUPABASE_KEY.",
        )
    try:
        return create_client(url, key)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=503,
            detail="La configuración de Supabase no es válida.",
        ) from exc


def ejecutar(consulta, accion: str) -> list[dict]:
    try:
        resultado = consulta.execute()
    except Exception as exc:
        code = getattr(exc, "code", None)
        if code in {"23505", "23P01", "23514", "P0001"}:
            raise HTTPException(
                status_code=409,
                detail=f"No se pudo completar {accion} por un conflicto con los datos existentes.",
            ) from exc
        logger.exception("Falló la operación de base de datos: %s", accion)
        raise HTTPException(
            status_code=502,
            detail=f"No fue posible completar {accion} en la base de datos.",
        ) from exc
    if resultado.data is None:
        return []
    if isinstance(resultado.data, list):
        return resultado.data
    return [resultado.data]
