from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException


CENTAVO = Decimal("0.01")
UMBRAL_ENVIO_GRATIS = Decimal("200000.00")
ENVIO_BASE = Decimal("15000.00")

ESTADOS = {
    "PENDIENTE_PAGO",
    "CONFIRMADA",
    "EN_PREPARACION",
    "LISTA_DESPACHO",
    "ENVIADA",
    "ENTREGADA",
    "CANCELADA",
    "DEVOLUCION_SOLICITADA",
}

TRANSICIONES_VALIDAS = {
    "PENDIENTE_PAGO": {"CONFIRMADA", "CANCELADA"},
    "CONFIRMADA": {"EN_PREPARACION", "CANCELADA"},
    "EN_PREPARACION": {"LISTA_DESPACHO", "CANCELADA"},
    "LISTA_DESPACHO": {"ENVIADA", "CANCELADA"},
    "ENVIADA": {"ENTREGADA"},
    "ENTREGADA": {"DEVOLUCION_SOLICITADA"},
    "CANCELADA": set(),
    "DEVOLUCION_SOLICITADA": set(),
}


def dinero(value: Decimal) -> Decimal:
    return value.quantize(CENTAVO, rounding=ROUND_HALF_UP)


def calcular_finanzas(
    items: list[dict], costo_envio: Decimal, descuentos: Decimal
) -> dict[str, Decimal]:
    subtotal = dinero(
        sum(
            (Decimal(str(item["cantidad"])) * Decimal(str(item["precio_unitario"])) for item in items),
            Decimal("0"),
        )
    )
    impuestos = dinero(subtotal * Decimal("0.19"))
    if subtotal > UMBRAL_ENVIO_GRATIS:
        costo_envio = Decimal("0.00")
    if descuentos > subtotal + impuestos + costo_envio:
        raise HTTPException(
            status_code=422,
            detail="Los descuentos no pueden superar el valor total del pedido.",
        )
    total = dinero(subtotal + impuestos + costo_envio - descuentos)
    return {
        "subtotal": subtotal,
        "impuestos": impuestos,
        "costo_envio": dinero(costo_envio),
        "descuentos": dinero(descuentos),
        "total": total,
    }


def validar_transicion(estado_actual: str, nuevo_estado: str) -> None:
    if nuevo_estado not in ESTADOS:
        raise HTTPException(status_code=422, detail="El estado solicitado no es válido.")
    if nuevo_estado not in TRANSICIONES_VALIDAS.get(estado_actual, set()):
        permitidos = sorted(TRANSICIONES_VALIDAS.get(estado_actual, set()))
        raise HTTPException(
            status_code=409,
            detail=(
                f"Transición inválida: '{estado_actual}' -> '{nuevo_estado}'. "
                f"Estados permitidos: {permitidos}"
            ),
        )
