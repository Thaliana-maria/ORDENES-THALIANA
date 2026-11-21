import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware

from auth import es_admin, rol_usuario, usuario_id, verificar_token
from database import ejecutar, get_supabase
from domain import calcular_finanzas, validar_transicion
from integrations import (
    CONTABILIDAD_URL,
    NOTIFICACIONES_URL,
    SEGUIMIENTO_URL,
    IntegracionError,
    ajustar_inventario,
    confirmar_inventario,
    consultar_producto,
    notificar,
    reservar_inventario,
)
from schemas import (
    ActualizarEstadoInput,
    CrearOrdenInput,
    DireccionOrdenInput,
)
from shipping import (
    AVISO_PRIVACIDAD,
    cotizar_envio,
    municipio_oficial,
    router as shipping_router,
)

logger = logging.getLogger(__name__)
app = FastAPI(
    title="Marketplace USC | API de Órdenes y Envíos",
    version="3.0.0",
    description=(
        "API unificada para órdenes, cobertura, tarifas, transportadoras, "
        "direcciones de entrega y seguimiento de envíos."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(shipping_router)

ROLES_SERVICIO = {"SERVICE", "SERVICIO", "PASARELA", "PAGOS", "SYSTEM"}


def _unico(rows: list[dict], detail: str) -> dict:
    if not rows:
        raise HTTPException(status_code=404, detail=detail)
    return rows[0]


def _es_admin(user: dict) -> bool:
    return es_admin(user)


def _rol_normalizado(user: dict) -> str:
    raw = rol_usuario(user)
    if isinstance(raw, str) and raw.strip():
        return raw.strip().upper()
    return "COMPRADOR"


def _autorizar_orden(orden: dict, user: dict) -> None:
    if _es_admin(user) or _rol_normalizado(user) in ROLES_SERVICIO:
        return
    subject = usuario_id(user)
    if subject not in {orden["comprador_id"], orden["vendedor_id"]}:
        raise HTTPException(status_code=403, detail="No tiene acceso a esta orden.")


def _direccion_para_orden(payload: CrearOrdenInput, user: dict, db) -> dict:
    if (
        not payload.direccion_origen
        or not payload.direccion_origen.strip()
        or not payload.ciudad_origen
        or not payload.ciudad_origen.strip()
    ):
        raise HTTPException(
            status_code=422,
            detail="Complete la dirección y ciudad de origen del envío.",
        )
    if payload.direccion_id:
        address = _unico(
            ejecutar(
                db.table("env_direcciones")
                .select("*")
                .eq("id", str(payload.direccion_id))
                .eq("usuario_id", usuario_id(user)),
                "consultar dirección de entrega",
            ),
            "La dirección no existe o no pertenece al comprador.",
        )
        if not address["acepta_tratamiento_datos"]:
            raise HTTPException(status_code=422, detail="La dirección no cuenta con consentimiento vigente.")
        return address
    required = {
        "direccion_entrega": payload.direccion_entrega,
        "ciudad_entrega": payload.ciudad_entrega,
        "destinatario_nombre": payload.destinatario_nombre,
        "destinatario_telefono": payload.destinatario_telefono,
        "direccion_origen": payload.direccion_origen,
        "ciudad_origen": payload.ciudad_origen,
    }
    if any(not value or not value.strip() for value in required.values()):
        raise HTTPException(
            status_code=422,
            detail="Complete origen, destino y datos del destinatario, o seleccione una dirección guardada.",
        )
    if not payload.acepta_tratamiento_datos:
        raise HTTPException(
            status_code=422,
            detail="Se requiere aceptar la política de tratamiento de datos para registrar el envío.",
        )
    municipality = municipio_oficial(
        db,
        payload.ciudad_entrega_codigo,
        payload.ciudad_entrega,
        payload.destinatario_departamento,
    )
    return {
        "direccion": payload.direccion_entrega.strip(),
        "municipio": municipality["municipio"],
        "municipio_codigo": municipality["codigo"],
        "departamento": municipality["departamento"],
        "nombre_destinatario": payload.destinatario_nombre.strip(),
        "telefono": payload.destinatario_telefono.strip(),
        "barrio": payload.destinatario_barrio,
        "indicaciones": payload.destinatario_indicaciones,
        "acepta_tratamiento_datos": True,
        "id": None,
    }


@app.get("/", summary="Información del servicio")
def inicio():
    return {
        "servicio": "Marketplace USC | Órdenes y Envíos",
        "documentacion": "/docs",
        "estado": "/api/health",
    }


@app.get("/api/health", summary="Verificar disponibilidad del servicio")
def health_check():
    return {"status": "ok", "servicio": "ordenes-envios-equipo-1"}


@app.get("/api/health/ready", summary="Verificar disponibilidad de Supabase")
def readiness_check():
    try:
        db = get_supabase()
        ejecutar(db.table("ord_pedidos").select("id").limit(1), "verificar Supabase")
        return {"status": "ready", "base_de_datos": "conectada"}
    except Exception as exc:
        logger.exception("Fallo en /api/health/ready")
        raise HTTPException(
            status_code=503,
            detail=f"Base de datos no disponible: {exc}",
        ) from exc


@app.post(
    "/api/ordenes",
    status_code=status.HTTP_201_CREATED,
    summary="Crear orden con cálculo de impuestos y envío",
)
def crear_orden(payload: CrearOrdenInput, user: dict = Depends(verificar_token)):
    db = get_supabase()
    subject = usuario_id(user)
    if payload.comprador_id and str(payload.comprador_id) != subject and not _es_admin(user):
        raise HTTPException(status_code=403, detail="No puede crear una orden para otro comprador.")
    if _rol_normalizado(user) not in {"COMPRADOR", "BUYER", "ADMIN", "ADMINISTRADOR", "ADMINISTRATOR"}:
        raise HTTPException(status_code=403, detail="Se requiere el rol de comprador.")
    address = _direccion_para_orden(payload, user, db)

    item_data = [
        {
            "producto_id": str(item.producto_id),
            "nombre_producto": item.nombre_producto,
            "cantidad": item.cantidad,
            "precio_unitario": item.precio_unitario,
        }
        for item in payload.items
    ]
    catalog_products = {}
    for item in item_data:
        try:
            catalog_product = consultar_producto(item["producto_id"])
        except IntegracionError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"No fue posible validar el producto {item['producto_id']}: {exc}",
            ) from exc

        activo = catalog_product.get("activo", catalog_product.get("active", True))
        vendedor = catalog_product.get("vendedor_id", catalog_product.get("seller_id"))
        precio = catalog_product.get("precio", catalog_product.get("price"))
        stock = catalog_product.get("stock")
        peso = catalog_product.get("peso_kg", catalog_product.get("peso"))

        if activo is False:
            raise HTTPException(status_code=409, detail=f"El producto {item['producto_id']} está inactivo.")
        if vendedor is None or str(vendedor) != str(payload.vendedor_id):
            raise HTTPException(status_code=409, detail=f"El producto {item['producto_id']} no pertenece al vendedor seleccionado.")
        if precio is None or Decimal(str(precio)) != Decimal(str(item["precio_unitario"])):
            raise HTTPException(status_code=409, detail=f"El precio del producto {item['producto_id']} no coincide con Catálogo.")
        if stock is None or int(stock) < item["cantidad"]:
            raise HTTPException(status_code=409, detail=f"Stock insuficiente para el producto {item['producto_id']}.")
        if peso is None:
            raise HTTPException(status_code=409, detail=f"Catálogo no informó el peso del producto {item['producto_id']}.")

        catalog_products[item["producto_id"]] = {
            "peso_kg": Decimal(str(peso)),
        }

    subtotal = sum(
        (Decimal(str(item["cantidad"])) * Decimal(str(item["precio_unitario"])) for item in item_data),
        Decimal("0"),
    )
    quote = cotizar_envio(
        db,
        address["municipio"],
        address["municipio_codigo"],
        item_data,
        subtotal,
    )
    configured_sellers = {detail["vendedor_id"] for detail in quote["detalles"]}
    if configured_sellers != {str(payload.vendedor_id)}:
        raise HTTPException(
            status_code=409,
            detail="El vendedor del pedido debe coincidir con el vendedor configurado en todos sus productos.",
        )
    finances = calcular_finanzas(
        item_data,
        Decimal(str(quote["costo_envio"])),
        Decimal("0"),
    )

    now = datetime.now(timezone.utc).isoformat()
    order_id = str(uuid4())
    order_number = f"USC-{datetime.now(timezone.utc):%Y%m%d}-{uuid4().hex[:8].upper()}"
    order_row = {
        "id": order_id,
        "comprador_id": subject,
        "vendedor_id": str(payload.vendedor_id),
        "numero_orden": order_number,
        "estado": "PENDIENTE_PAGO",
        **{key: str(value) for key, value in finances.items()},
        "moneda": "COP",
        "direccion_origen": payload.direccion_origen.strip(),
        "ciudad_origen": payload.ciudad_origen.strip(),
        "direccion_entrega": address["direccion"],
        "ciudad_entrega": address["municipio"],
        "destinatario_nombre": address["nombre_destinatario"],
        "destinatario_telefono": address["telefono"],
        "destinatario_barrio": address.get("barrio"),
        "destinatario_departamento": address.get("departamento"),
        "destinatario_municipio_codigo": address.get("municipio_codigo"),
        "destinatario_indicaciones": address.get("indicaciones"),
        "acepta_tratamiento_datos": True,
        "direccion_id": address.get("id"),
        "zona_envio_id": quote["zona_id"],
        "detalle_envio": quote["detalles"],
        "notas": payload.notas,
        "creado_en": now,
        "actualizado_en": now,
    }
    shipping_by_product = {
        detail["producto_id"]: detail for detail in quote["detalles"]
    }
    item_rows = []
    for item in item_data:
        shipping = shipping_by_product.get(item["producto_id"], {})
        item_rows.append(
            {
                "pedido_id": order_id,
                "producto_id": item["producto_id"],
                "nombre_producto": item["nombre_producto"],
                "cantidad": item["cantidad"],
                "precio_unitario": str(item["precio_unitario"]),
                "subtotal": str(
                    Decimal(str(item["cantidad"])) * Decimal(str(item["precio_unitario"]))
                ),
                "peso_kg": shipping.get("peso_kg"),
                "opcion_envio_id": shipping.get("opcion_envio_id"),
                "tarifa_envio": shipping.get("tarifa"),
            }
        )

    inventory_payload = {
        "orden_id": order_id,
        "numero_orden": order_number,
        "comprador_id": subject,
        "vendedor_id": str(payload.vendedor_id),
        "items": [
            {"producto_id": item["producto_id"], "cantidad": item["cantidad"]}
            for item in item_data
        ],
    }
    reservar_inventario(inventory_payload)

    created = _unico(
        ejecutar(
            db.rpc(
                "crear_orden_transaccional",
                {
                    "p_orden": order_row,
                    "p_items": item_rows,
                    "p_usuario": subject,
                    "p_observaciones": "Orden creada y reserva de inventario solicitada.",
                },
            ),
            "crear orden, ítems e historial de forma transaccional",
        ),
        "Supabase no devolvió la orden creada.",
    )

    warnings = quote["advertencias"]
    return {
        "mensaje": "Orden creada exitosamente",
        "orden": created,
        "items": item_rows,
        "envio": quote,
        "aviso_privacidad": AVISO_PRIVACIDAD,
        "advertencias": warnings,
    }


@app.get("/api/ordenes", summary="Listar órdenes del comprador o vendedor autenticado")
def listar_ordenes(
    usuario_id_param: UUID | None = Query(default=None, alias="usuario_id"),
    rol: str | None = None,
    user: dict = Depends(verificar_token),
):
    db = get_supabase()
    subject = usuario_id(user)
    role = _rol_normalizado(user)

    if _es_admin(user) or role in ROLES_SERVICIO:
        query = db.table("ord_pedidos").select("*")
        if usuario_id_param and rol:
            req_role = rol.strip().upper()
            field = "vendedor_id" if req_role in {"VENDEDOR", "SELLER"} else "comprador_id"
            query = query.eq(field, str(usuario_id_param))
        elif usuario_id_param:
            query = query.eq("comprador_id", str(usuario_id_param))
        return ejecutar(query.order("creado_en", desc=True), "listar órdenes")

    if usuario_id_param and str(usuario_id_param) != subject:
        raise HTTPException(status_code=403, detail="Solo puede consultar sus propias órdenes.")

    try:
        UUID(str(subject))
    except ValueError:
        return []

    requested_role = rol.strip().upper() if rol and rol.strip() else role
    if requested_role != role:
        raise HTTPException(status_code=403, detail="El rol solicitado no coincide con su sesión.")

    if requested_role in {"VENDEDOR", "SELLER"}:
        field = "vendedor_id"
    elif requested_role in {"COMPRADOR", "BUYER"}:
        field = "comprador_id"
    else:
        raise HTTPException(status_code=422, detail="El rol debe ser COMPRADOR o VENDEDOR.")

    return ejecutar(
        db.table("ord_pedidos")
        .select("*")
        .eq(field, subject)
        .order("creado_en", desc=True),
        "listar órdenes",
    )


@app.get("/api/ordenes/{orden_id}", summary="Consultar comprobante y datos de privacidad")
def obtener_detalle_orden(orden_id: UUID, user: dict = Depends(verificar_token)):
    db = get_supabase()
    order = _unico(
        ejecutar(
            db.table("ord_pedidos").select("*").eq("id", str(orden_id)),
            "consultar la orden",
        ),
        "Orden no encontrada.",
    )
    _autorizar_orden(order, user)
    items = ejecutar(
        db.table("ord_items_pedido").select("*").eq("pedido_id", str(orden_id)),
        "consultar los ítems de la orden",
    )
    history = ejecutar(
        db.table("ord_historial_estados")
        .select("*")
        .eq("pedido_id", str(orden_id))
        .order("creado_en"),
        "consultar el historial de la orden",
    )
    return {
        **order,
        "comprobante": order,
        "items": items,
        "historial_auditoria": history,
        "aviso_privacidad": AVISO_PRIVACIDAD,
    }


@app.get("/api/ordenes/{orden_id}/rastreo", summary="Consultar el estado y rastreo de una orden")
def rastrear_envio_orden(orden_id: UUID, user: dict = Depends(verificar_token)):
    db = get_supabase()
    order = _unico(
        ejecutar(
            db.table("ord_pedidos").select("*").eq("id", str(orden_id)),
            "consultar la orden",
        ),
        "Orden no encontrada.",
    )
    _autorizar_orden(order, user)
    history = ejecutar(
        db.table("ord_historial_estados")
        .select("estado_anterior, estado_nuevo, observaciones, creado_en")
        .eq("pedido_id", str(orden_id))
        .order("creado_en"),
        "consultar el historial de la orden",
    )
    return {
        "numero_orden": order["numero_orden"],
        "estado_actual": order["estado"],
        "punto_partida": {
            "direccion": order.get("direccion_origen"),
            "ciudad": order.get("ciudad_origen"),
        },
        "punto_destino": {
            "direccion": order.get("direccion_entrega"),
            "ciudad": order.get("ciudad_entrega"),
        },
        "destinatario": {
            "nombre": order.get("destinatario_nombre"),
            "telefono": order.get("destinatario_telefono"),
        },
        "logistica": {
            "transportadora": order.get("transportadora_nombre") or "Pendiente de asignar",
            "numero_guia": order.get("numero_guia") or "Sin guía generada",
            "fecha_estimada_entrega": order.get("fecha_estimada_entrega"),
            "costo_envio": order["costo_envio"],
        },
        "linea_tiempo": history,
    }


@app.put("/api/ordenes/{orden_id}/direccion", summary="Actualizar dirección antes de confirmar el pago")
def actualizar_direccion_orden(
    orden_id: UUID,
    payload: DireccionOrdenInput,
    user: dict = Depends(verificar_token),
):
    db = get_supabase()
    order = _unico(
        ejecutar(
            db.table("ord_pedidos").select("*").eq("id", str(orden_id)),
            "consultar la orden",
        ),
        "Orden no encontrada.",
    )
    if not _es_admin(user) and order["comprador_id"] != usuario_id(user):
        raise HTTPException(status_code=403, detail="Solo el comprador puede cambiar la dirección.")
    if order["estado"] != "PENDIENTE_PAGO":
        raise HTTPException(
            status_code=409,
            detail="La dirección solo se puede modificar antes de confirmar el pago.",
        )
    if payload.direccion_id:
        address = _unico(
            ejecutar(
                db.table("env_direcciones")
                .select("*")
                .eq("id", str(payload.direccion_id))
                .eq("usuario_id", order["comprador_id"]),
                "consultar la dirección de entrega",
            ),
            "La dirección no existe o no pertenece al comprador.",
        )
    else:
        if not payload.acepta_tratamiento_datos:
            raise HTTPException(
                status_code=422,
                detail="Se requiere aceptar la política de tratamiento de datos.",
            )
        required = (
            payload.nombre_destinatario,
            payload.telefono,
            payload.direccion,
            payload.municipio,
            payload.departamento,
        )
        if any(not value or not value.strip() for value in required):
            raise HTTPException(status_code=422, detail="Faltan datos de la dirección del destinatario.")
        address = {
            "id": None,
            "nombre_destinatario": payload.nombre_destinatario.strip(),
            "telefono": payload.telefono.strip(),
            "direccion": payload.direccion.strip(),
            "barrio": payload.barrio,
            "municipio": payload.municipio.strip(),
            "departamento": payload.departamento.strip(),
            "indicaciones": payload.indicaciones,
            "acepta_tratamiento_datos": True,
        }
        municipality = municipio_oficial(
            db,
            payload.municipio_codigo,
            payload.municipio,
            payload.departamento,
        )
        address["municipio"] = municipality["municipio"]
        address["municipio_codigo"] = municipality["codigo"]
        address["departamento"] = municipality["departamento"]

    quote = cotizar_envio(
        db,
        address["municipio"],
        address["municipio_codigo"],
        ejecutar(
            db.table("ord_items_pedido").select("*").eq("pedido_id", str(orden_id)),
            "consultar ítems para recalcular envío",
        ),
        Decimal(str(order["subtotal"])),
    )
    finances = calcular_finanzas(
        [
            {"cantidad": item["cantidad"], "precio_unitario": item["precio_unitario"]}
            for item in ejecutar(
                db.table("ord_items_pedido").select("*").eq("pedido_id", str(orden_id)),
                "consultar ítems para recalcular el pedido",
            )
        ],
        Decimal(str(quote["costo_envio"])),
        Decimal(str(order["descuentos"])),
    )
    return _unico(
        ejecutar(
            db.rpc(
                "actualizar_direccion_orden_transaccional",
                {
                    "p_orden_id": str(orden_id),
                    "p_direccion": address,
                    "p_envio": quote,
                    "p_finanzas": {key: str(value) for key, value in finances.items()},
                },
            ),
            "actualizar dirección, tarifa, total e ítems de forma transaccional",
        ),
        "La orden no existe o ya fue confirmada.",
    )


@app.put("/api/ordenes/{orden_id}/estado", summary="Cambiar estado y actualizar logística")
def actualizar_estado(
    orden_id: UUID,
    payload: ActualizarEstadoInput,
    user: dict = Depends(verificar_token),
):
    db = get_supabase()
    order = _unico(
        ejecutar(
            db.table("ord_pedidos").select("*").eq("id", str(orden_id)),
            "consultar la orden",
        ),
        "Orden no encontrada.",
    )
    _autorizar_orden(order, user)
    estado = payload.nuevo_estado.strip().upper()
    validar_transicion(order["estado"], estado)
    role = _rol_normalizado(user)
    if not _es_admin(user) and role not in ROLES_SERVICIO:
        if role in {"COMPRADOR", "BUYER"}:
            if estado != "CANCELADA" or order["comprador_id"] != usuario_id(user):
                raise HTTPException(status_code=403, detail="El comprador solo puede cancelar su orden.")
        elif role in {"VENDEDOR", "SELLER"}:
            if order["vendedor_id"] != usuario_id(user):
                raise HTTPException(status_code=403, detail="No puede actualizar la orden de otro vendedor.")
        else:
            raise HTTPException(status_code=403, detail="No puede actualizar estados de órdenes.")
    if estado == "CANCELADA" and order["estado"] in {"ENVIADA", "ENTREGADA"}:
        raise HTTPException(status_code=409, detail="Una orden despachada no se puede cancelar.")

    if estado == "ENVIADA" and (not payload.transportadora_id or not payload.numero_guia):
        raise HTTPException(
            status_code=422,
            detail="Para despachar se requiere una transportadora activa y un número de guía.",
        )
    carrier = None
    if payload.transportadora_id:
        carrier = _unico(
            ejecutar(
                db.table("env_transportadoras")
                .select("*")
                .eq("id", str(payload.transportadora_id))
                .eq("activa", True),
                "validar transportadora activa",
            ),
            "La transportadora no existe o está inactiva.",
        )
    observation = payload.observaciones or f"Cambio de estado a {estado}"
    if carrier and payload.numero_guia:
        observation += f" | Guía: {payload.numero_guia.strip()} ({carrier['nombre']})"
    responsable = usuario_id(user)

    updated = _unico(
        ejecutar(
            db.rpc(
                "actualizar_estado_orden_transaccional",
                {
                    "p_orden_id": str(orden_id),
                    "p_estado": estado,
                    "p_usuario": responsable,
                    "p_observaciones": observation,
                    "p_transportadora_id": str(payload.transportadora_id) if payload.transportadora_id else None,
                    "p_transportadora_nombre": carrier["nombre"] if carrier else None,
                    "p_numero_guia": payload.numero_guia.strip() if payload.numero_guia else None,
                    "p_fecha_estimada_entrega": payload.fecha_estimada_entrega.isoformat() if payload.fecha_estimada_entrega else None,
                },
            ),
            "actualizar estado e historial de forma transaccional",
        ),
        "La orden no existe o cambió de estado en otra solicitud.",
    )

    warnings = []
    notification_url = NOTIFICACIONES_URL + "api/notificaciones"
    for usuario_id_destino in {str(order["comprador_id"]), str(order["vendedor_id"])}:
        notification_payload = {
            "usuario_id": usuario_id_destino,
            "tipo": "ORDEN_CREADA" if estado == "CONFIRMADA" else (
                "ENVIO_ENTREGADO" if estado == "ENTREGADA" else (
                    "DEVOLUCION_SOLICITADA" if estado == "DEVOLUCION_SOLICITADA" else "SISTEMA_INFO"
                )
            ),
            "titulo": f"Actualización de orden {order['numero_orden']}",
            "mensaje": f"Tu orden {order['numero_orden']} ahora está en estado {estado}.",
            "referencia_id": str(orden_id),
            "referencia_tipo": "ORDEN",
        }
        warning = notificar(notification_url, notification_payload, "Notificaciones (Equipo 3)")
        if warning:
            warnings.append(warning)

    if estado == "CONFIRMADA":
        try:
            confirmar_inventario(order["numero_orden"])
        except IntegracionError as exc:
            warnings.append(f"No se pudo confirmar la reserva de inventario: {exc}")

        accounting_url = CONTABILIDAD_URL + "api/contabilidad/escrow/retener"
        accounting_payload = {
            "pedido_id": str(orden_id),
            "vendedor_id": str(order["vendedor_id"]),
            "comprador_id": str(order["comprador_id"]),
            "monto_bruto": float(order["total"]),
        }
        warning = notificar(accounting_url, accounting_payload, "Contabilidad (Equipo 6)")
        if warning:
            warnings.append(warning)

        order_items = ejecutar(
            db.table("ord_items_pedido")
            .select("peso_kg")
            .eq("pedido_id", str(orden_id)),
            "consultar peso de los ítems para Seguimiento",
        )
        peso_total = sum(
            (Decimal(str(item["peso_kg"])) for item in order_items if item.get("peso_kg") is not None),
            Decimal("0"),
        )

        tracking_url = SEGUIMIENTO_URL + "api/seguimiento/crear"
        tracking_payload = {
            "pedido_id": str(orden_id),
            "vendedor_id": str(order["vendedor_id"]),
            "comprador_id": str(order["comprador_id"]),
            "numero_guia": updated.get("numero_guia"),
            "transportadora": updated.get("transportadora_nombre"),
            "origen": order.get("ciudad_origen") or "",
            "destino": order.get("ciudad_entrega") or "",
            "direccion_entrega": order.get("direccion_entrega") or "",
            "peso_kg": float(peso_total) if peso_total > 0 else None,
            "valor_declarado": float(order["subtotal"]),
            "observaciones": observation,
        }
        warning = notificar(tracking_url, tracking_payload, "Seguimiento (Equipo 5)")
        if warning:
            warnings.append(warning)

    if estado == "CANCELADA":
        cancelled_items = ejecutar(
            db.table("ord_items_pedido")
            .select("producto_id,cantidad")
            .eq("pedido_id", str(orden_id)),
            "consultar ítems para compensar inventario",
        )
        for cancelled_item in cancelled_items:
            try:
                ajustar_inventario(
                    str(cancelled_item["producto_id"]),
                    int(cancelled_item["cantidad"]),
                    order["numero_orden"],
                )
            except (IntegracionError, HTTPException) as exc:
                warnings.append(
                    f"No se pudo compensar la reserva del producto {cancelled_item['producto_id']}: {exc}"
                )

    if estado == "CANCELADA" and payload.requiere_reembolso:
        accounting_url = CONTABILIDAD_URL + f"api/contabilidad/escrow/bloquear/{orden_id}"
        accounting_payload = {
            "razon_pqr": (observation.strip() if len(observation.strip()) >= 5 else "Cancelación con solicitud de reembolso")
        }
        warning = notificar(accounting_url, accounting_payload, "Contabilidad (Equipo 6)")
        if warning:
            warnings.append(warning)

    return {
        "mensaje": f"Estado actualizado de {order['estado']} a {estado}",
        "orden": updated,
        "advertencias": warnings,
    }