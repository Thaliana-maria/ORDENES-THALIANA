import logging
import os
from urllib.parse import urljoin
from typing import Any

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)


INVENTARIO_URL = os.getenv(
    "URL_INVENTARIO",
    "https://marketplace-equipo4.onrender.com",
).rstrip("/") + "/"
INVENTARIO_RESERVE_PATH = os.getenv(
    "INVENTARIO_RESERVE_PATH",
    "/api/inventario/reservar",
)
INVENTARIO_CONFIRM_PATH = os.getenv(
    "INVENTARIO_CONFIRM_PATH",
    "/api/inventario/confirmar",
)
INVENTARIO_ADJUSTMENT_PATH = os.getenv(
    "INVENTARIO_ADJUSTMENT_PATH",
    "/api/inventario/productos/{producto_id}/movimientos",
)
NOTIFICACIONES_URL = os.getenv(
    "URL_NOTIFICACIONES",
    "https://marketplace-usc-notificaciones.onrender.com",
).rstrip("/") + "/"
CONTABILIDAD_URL = os.getenv(
    "URL_CONTABILIDAD",
    "https://marketplace-usc-contabilidad.onrender.com",
).rstrip("/") + "/"
SEGUIMIENTO_URL = os.getenv(
    "URL_SEGUIMIENTO",
    "https://marketplace-usc-seguimiento.onrender.com",
).rstrip("/") + "/"


class IntegracionError(Exception):
    pass


def _post(url: str, payload: dict[str, Any]) -> None:
    try:
        response = httpx.post(url, json=payload, timeout=8.0)
    except httpx.TimeoutException as exc:
        raise IntegracionError("El servicio externo agotó el tiempo de espera.") from exc
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        raise IntegracionError("No fue posible conectar con el servicio externo.") from exc

    if not response.is_success:
        raise IntegracionError(
            f"El servicio externo respondió HTTP {response.status_code}."
        )


def reservar_inventario(payload: dict[str, Any]) -> None:
    url = urljoin(INVENTARIO_URL, INVENTARIO_RESERVE_PATH.lstrip("/"))
    reservation_payload = {
        "referencia_id": payload["numero_orden"],
        "items": payload["items"],
    }
    try:
        _post(url, reservation_payload)
    except IntegracionError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"No fue posible reservar el inventario; la orden no se creó. {exc}",
        ) from exc


def confirmar_inventario(referencia_id: str) -> None:
    path = INVENTARIO_CONFIRM_PATH.rstrip("/") + f"/{referencia_id}"
    url = urljoin(INVENTARIO_URL, path.lstrip("/"))
    try:
        response = httpx.post(url, timeout=8.0)
        if not response.is_success:
            raise IntegracionError(
                f"El servicio externo respondió HTTP {response.status_code}."
            )
    except httpx.TimeoutException as exc:
        raise IntegracionError("El servicio externo agotó el tiempo de espera.") from exc
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        raise IntegracionError("No fue posible conectar con el servicio externo.") from exc


def consultar_producto(producto_id: str) -> dict[str, Any]:
    url = urljoin(INVENTARIO_URL, f"api/productos/{producto_id}")
    try:
        response = httpx.get(url, timeout=8.0)
    except httpx.TimeoutException as exc:
        raise IntegracionError("El servicio externo agotó el tiempo de espera.") from exc
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        raise IntegracionError("No fue posible conectar con el servicio externo.") from exc
    if not response.is_success:
        raise IntegracionError(
            f"El servicio externo respondió HTTP {response.status_code}."
        )
    data = response.json()
    if not isinstance(data, dict):
        raise IntegracionError("El servicio devolvió un formato de producto inválido.")
    return data


def ajustar_inventario(producto_id: str, cantidad: int, referencia: str) -> None:
    path = INVENTARIO_ADJUSTMENT_PATH.replace("{producto_id}", producto_id)
    url = urljoin(INVENTARIO_URL, path.lstrip("/"))
    payload = {
        "tipo": "AJUSTE",
        "cantidad": cantidad,
        "nota": f"Compensación por cancelación de orden {referencia}",
    }
    _post(url, payload)


def notificar(url: str, payload: dict[str, Any], integracion: str) -> str | None:
    try:
        _post(url, payload)
    except IntegracionError as exc:
        logger.error("Falló la integración con %s: %s", integracion, exc)
        return f"No se pudo notificar a {integracion}; el cambio quedó guardado."
    return None
