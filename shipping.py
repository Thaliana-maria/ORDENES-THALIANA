import unicodedata
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import es_admin, requiere_admin, requiere_vendedor, usuario_id, verificar_token
from database import ejecutar, get_supabase
from domain import ENVIO_BASE, dinero
from schemas import (
    AsociarOpcionInput,
    CotizacionInput,
    DireccionInput,
    OpcionEnvioInput,
    ProductoEnvioInput,
    TarifaInput,
    TransportadoraInput,
    ZonaInput,
)

router = APIRouter(prefix="/api")
AVISO_PRIVACIDAD = (
    "Los datos personales de destinatario y entrega se usan para gestionar el despacho "
    "y la compra, conforme a la Ley 1581 de 2012. El titular puede solicitar consulta, "
    "actualización o supresión por los canales de atención del Marketplace USC."
)


def _normalizar(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip().casefold())
    return "".join(char for char in normalized if not unicodedata.combining(char))


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unico(rows: list[dict], detail: str) -> dict:
    if not rows:
        raise HTTPException(status_code=404, detail=detail)
    return rows[0]


def municipio_oficial(db, codigo: str | None, nombre: str, departamento: str | None) -> dict:
    if not codigo or not departamento:
        raise HTTPException(
            status_code=422,
            detail="Seleccione el municipio y departamento del catálogo oficial DIVIPOLA.",
        )
    municipality = _unico(
        ejecutar(
            db.table("env_municipios_oficiales")
            .select("*")
            .eq("codigo", codigo),
            "validar el código municipal DIVIPOLA",
        ),
        "El código DIVIPOLA no existe. Ejecute sql/seed_municipios.sql.",
    )
    if (
        _normalizar(municipality["municipio"]) != _normalizar(nombre)
        or _normalizar(municipality["departamento"]) != _normalizar(departamento)
    ):
        raise HTTPException(
            status_code=422,
            detail="El municipio, departamento y código DIVIPOLA no corresponden.",
        )
    return municipality


def _municipio_oficial(db, payload: ZonaInput) -> dict:
    return municipio_oficial(
        db,
        payload.municipio_codigo,
        payload.municipio,
        payload.departamento,
    )


def _validate_zone_and_option(
    db,
    zona_id: UUID,
    opcion_id: UUID,
    require_tarifa: bool = False,
) -> dict:
    zone = _unico(
        ejecutar(
            db.table("env_zonas").select("*").eq("id", str(zona_id)).eq("activa", True),
            "consultar la zona de cobertura",
        ),
        "La zona no existe o está inactiva.",
    )
    option = _unico(
        ejecutar(
            db.table("env_opciones_envio")
            .select("*")
            .eq("id", str(opcion_id))
            .eq("activa", True),
            "consultar la opción de envío",
        ),
        "La opción de envío no existe o está inactiva.",
    )
    relation = _unico(
        ejecutar(
            db.table("env_zonas_opciones")
            .select("*")
            .eq("zona_id", str(zona_id))
            .eq("opcion_envio_id", str(opcion_id))
            .eq("activa", True),
            "consultar la cobertura de la opción",
        ),
        "La opción de envío no tiene cobertura activa en esa zona.",
    )
    if require_tarifa and not ejecutar(
        db.table("env_tarifas").select("id").eq("zona_opcion_id", relation["id"]),
        "consultar tarifas",
    ):
        raise HTTPException(status_code=409, detail="La opción no tiene tarifas configuradas.")
    return {"zona": zone, "opcion": option, "zona_opcion": relation}


def cotizar_envio(
    db,
    ciudad_destino: str,
    municipio_codigo: str,
    items: list[dict],
    subtotal: Decimal,
) -> dict:
    zones = ejecutar(
        db.table("env_zonas").select("*").eq("activa", True),
        "consultar cobertura de envío",
    )
    zone = next((item for item in zones if item["municipio_codigo"] == municipio_codigo), None)
    product_ids = list({str(item["producto_id"]) for item in items})
    products = ejecutar(
        db.table("env_productos")
        .select("*")
        .in_("producto_id", product_ids)
        .eq("activo", True),
        "consultar la configuración logística de los productos",
    )
    products_by_id = {row["producto_id"]: row for row in products}
    missing_products = product_ids - products_by_id.keys()
    if missing_products:
        raise HTTPException(
            status_code=409,
            detail=(
                "Cada producto debe tener peso, dimensiones y opción de envío "
                "configurados por su vendedor antes de cotizar."
            ),
        )
    if zone is None:
        raise HTTPException(
            status_code=409,
            detail=f"No hay cobertura activa para el municipio '{ciudad_destino}'.",
        )

    details: list[dict[str, Any]] = []
    configured_cost = Decimal("0")
    needs_base_rate = False

    for item in items:
        product = products_by_id[str(item["producto_id"])]
        config = _validate_zone_and_option(
            db, UUID(zone["id"]), UUID(product["opcion_envio_id"])
        )
        weight = Decimal(str(product["peso_kg"])) * int(item["cantidad"])
        rates = ejecutar(
            db.table("env_tarifas")
            .select("*")
            .eq("zona_opcion_id", config["zona_opcion"]["id"])
            .lte("peso_min_kg", str(weight))
            .gt("peso_max_kg", str(weight)),
            "calcular la tarifa de envío",
        )
        if rates:
            rate = dinero(Decimal(str(rates[0]["valor"])))
            configured_cost += rate
            rate_type = "configurada"
        else:
            needs_base_rate = True
            rate = ENVIO_BASE
            rate_type = "base_guia_sin_rango"
        details.append(
            {
                "producto_id": str(item["producto_id"]),
                "peso_kg": str(weight),
                "opcion_envio_id": product["opcion_envio_id"],
                "vendedor_id": product["vendedor_id"],
                "opcion_envio": config["opcion"]["nombre"],
                "tarifa": str(rate),
                "tarifa_envio": str(rate),
                "tipo_tarifa": rate_type,
            }
        )

    base_cost = ENVIO_BASE if needs_base_rate else Decimal("0")
    quote = configured_cost + base_cost
    if subtotal > Decimal("200000"):
        quote = Decimal("0")

    warnings = []
    if needs_base_rate:
        warnings.append(
            "Uno o más productos no tienen tarifa por peso configurada; se aplicó "
            "la tarifa base de la guía ($15.000 COP)."
        )
    return {
        "zona_id": zone["id"] if zone else None,
        "ciudad_destino": ciudad_destino,
        "costo_envio": str(dinero(quote)),
        "envio_gratis": subtotal > Decimal("200000"),
        "detalles": details,
        "advertencias": warnings,
    }


@router.get("/envios/zonas", summary="Consultar zonas de cobertura")
def listar_zonas(
    incluir_inactivas: bool = False,
    user: dict = Depends(verificar_token),
):
    if incluir_inactivas:
        requiere_admin(user)
    query = get_supabase().table("env_zonas").select("*").order("departamento").order("municipio")
    if not incluir_inactivas:
        query = query.eq("activa", True)
    return ejecutar(query, "listar zonas de cobertura")


@router.get("/envios/municipios", summary="Consultar el catálogo oficial DIVIPOLA")
def listar_municipios(
    departamento: str | None = None,
    buscar: str | None = Query(default=None, min_length=2, max_length=100),
    limite: int = Query(default=100, ge=1, le=500),
    desde: int = Query(default=0, ge=0),
    user: dict = Depends(verificar_token),
):
    query = get_supabase().table("env_municipios_oficiales").select("*")
    if departamento:
        query = query.ilike("departamento", f"%{departamento.strip()}%")
    if buscar:
        query = query.ilike("municipio", f"%{buscar.strip()}%")
    return ejecutar(
        query.order("departamento")
        .order("municipio")
        .range(desde, desde + limite - 1),
        "consultar municipios oficiales",
    )


@router.post("/envios/zonas", status_code=201, summary="Crear zona de cobertura")
def crear_zona(payload: ZonaInput, user: dict = Depends(verificar_token)):
    requiere_admin(user)
    db = get_supabase()
    municipality = _municipio_oficial(db, payload)
    data = payload.model_dump(mode="json")
    data.update(
        nombre=payload.nombre.strip(),
        departamento=municipality["departamento"],
        municipio=municipality["municipio"],
        municipio_codigo=municipality["codigo"],
    )
    return _unico(
        ejecutar(db.table("env_zonas").insert(data), "crear la zona"),
        "No se pudo crear la zona.",
    )


@router.put("/envios/zonas/{zona_id}", summary="Actualizar o desactivar zona")
def actualizar_zona(
    zona_id: UUID, payload: ZonaInput, user: dict = Depends(verificar_token)
):
    requiere_admin(user)
    municipality = _municipio_oficial(get_supabase(), payload)
    data = payload.model_dump(mode="json")
    data.update(
        nombre=payload.nombre.strip(),
        departamento=municipality["departamento"],
        municipio=municipality["municipio"],
        municipio_codigo=municipality["codigo"],
    )
    data["actualizado_en"] = _ahora()
    return _unico(
        ejecutar(
            get_supabase().table("env_zonas")
            .update(data)
            .eq("id", str(zona_id))
            .select("*"),
            "actualizar la zona",
        ),
        "La zona no existe.",
    )


@router.get("/envios/opciones", summary="Consultar opciones de envío")
def listar_opciones(
    incluir_inactivas: bool = False,
    user: dict = Depends(verificar_token),
):
    if incluir_inactivas:
        requiere_admin(user)
    query = get_supabase().table("env_opciones_envio").select("*").order("nombre")
    if not incluir_inactivas:
        query = query.eq("activa", True)
    return ejecutar(query, "listar opciones de envío")


@router.post("/envios/opciones", status_code=201, summary="Crear opción de envío")
def crear_opcion(
    payload: OpcionEnvioInput, user: dict = Depends(verificar_token)
):
    requiere_admin(user)
    data = payload.model_dump(mode="json")
    data["nombre"] = payload.nombre.strip()
    data["modalidad"] = payload.modalidad.strip()
    data["actualizado_en"] = _ahora()
    return _unico(
        ejecutar(
            get_supabase().table("env_opciones_envio").insert(data),
            "crear la opción de envío",
        ),
        "No se pudo crear la opción.",
    )


def _bloquear_ultima_opcion(db, opcion_id: UUID) -> None:
    relations = ejecutar(
        db.table("env_zonas_opciones")
        .select("zona_id")
        .eq("opcion_envio_id", str(opcion_id))
        .eq("activa", True),
        "validar la cobertura de la opción",
    )
    for relation in relations:
        zone = ejecutar(
            db.table("env_zonas").select("id").eq("id", relation["zona_id"]).eq("activa", True),
            "validar la zona de cobertura",
        )
        if not zone:
            continue
        other_options = ejecutar(
            db.table("env_zonas_opciones")
            .select("id")
            .eq("zona_id", relation["zona_id"])
            .eq("activa", True)
            .neq("opcion_envio_id", str(opcion_id)),
            "validar las opciones activas de la zona",
        )
        if not other_options:
            raise HTTPException(
                status_code=409,
                detail="No se puede desactivar la única opción activa de una zona.",
            )


@router.put("/envios/opciones/{opcion_id}", summary="Actualizar o desactivar opción")
def actualizar_opcion(
    opcion_id: UUID,
    payload: OpcionEnvioInput,
    user: dict = Depends(verificar_token),
):
    requiere_admin(user)
    db = get_supabase()
    if not payload.activa:
        _bloquear_ultima_opcion(db, opcion_id)
    data = payload.model_dump(mode="json")
    data["nombre"] = payload.nombre.strip()
    data["modalidad"] = payload.modalidad.strip()
    data["actualizado_en"] = _ahora()
    rows = ejecutar(
        db.table("env_opciones_envio")
        .update(data)
        .eq("id", str(opcion_id))
        .select("*"),
        "actualizar la opción de envío",
    )
    return _unico(rows, "La opción de envío no existe.")


@router.put(
    "/envios/zonas/{zona_id}/opciones/{opcion_id}",
    summary="Asociar o actualizar opción disponible en una zona",
)
def asociar_opcion(
    zona_id: UUID,
    opcion_id: UUID,
    payload: AsociarOpcionInput,
    user: dict = Depends(verificar_token),
):
    requiere_admin(user)
    db = get_supabase()
    _unico(
        ejecutar(db.table("env_zonas").select("id").eq("id", str(zona_id)).eq("activa", True),
                 "validar la zona"),
        "La zona no existe o está inactiva.",
    )
    _unico(
        ejecutar(
            db.table("env_opciones_envio").select("id").eq("id", str(opcion_id)).eq("activa", True),
            "validar la opción",
        ),
        "La opción no existe o está inactiva.",
    )
    query = (
        db.table("env_zonas_opciones")
        .select("*")
        .eq("zona_id", str(zona_id))
        .eq("opcion_envio_id", str(opcion_id))
    )
    rows = ejecutar(query, "consultar asociación")
    if rows:
        if not payload.activa:
            active_count = ejecutar(
                db.table("env_zonas_opciones")
                .select("id")
                .eq("zona_id", str(zona_id))
                .eq("activa", True),
                "validar opciones activas de la zona",
            )
            if len(active_count) <= 1:
                raise HTTPException(
                    status_code=409,
                    detail="La zona debe conservar al menos una opción de envío activa.",
                )
        return _unico(
            ejecutar(
                db.table("env_zonas_opciones")
                .update({"activa": payload.activa, "actualizado_en": _ahora()})
                .eq("id", rows[0]["id"])
                .select("*"),
                "actualizar la asociación",
            ),
            "No se pudo actualizar la asociación.",
        )
    if not payload.activa:
        raise HTTPException(status_code=404, detail="La asociación no existe.")
    return _unico(
        ejecutar(
            db.table("env_zonas_opciones").insert(
                {
                    "zona_id": str(zona_id),
                    "opcion_envio_id": str(opcion_id),
                    "activa": True,
                }
            ),
            "asociar la opción a la zona",
        ),
        "No se pudo asociar la opción.",
    )


@router.get("/envios/tarifas", summary="Consultar tarifas por zona y opción")
def listar_tarifas(
    zona_id: UUID,
    opcion_envio_id: UUID,
    user: dict = Depends(verificar_token),
):
    requiere_admin(user)
    db = get_supabase()
    relation = _validate_zone_and_option(db, zona_id, opcion_envio_id)
    return ejecutar(
        db.table("env_tarifas")
        .select("*")
        .eq("zona_opcion_id", relation["zona_opcion"]["id"])
        .order("peso_min_kg"),
        "listar tarifas",
    )


@router.post("/envios/tarifas", status_code=201, summary="Crear tarifa por rango de peso")
def crear_tarifa(
    zona_id: UUID,
    opcion_envio_id: UUID,
    payload: TarifaInput,
    user: dict = Depends(verificar_token),
):
    requiere_admin(user)
    db = get_supabase()
    relation = _validate_zone_and_option(db, zona_id, opcion_envio_id)
    overlap = ejecutar(
        db.table("env_tarifas")
        .select("id")
        .eq("zona_opcion_id", relation["zona_opcion"]["id"])
        .lt("peso_min_kg", str(payload.peso_max_kg))
        .gt("peso_max_kg", str(payload.peso_min_kg)),
        "validar que los rangos de tarifa no se solapen",
    )
    if overlap:
        raise HTTPException(status_code=409, detail="El rango de peso se solapa con otra tarifa.")
    return _unico(
        ejecutar(
            db.rpc(
                "guardar_tarifa_transaccional",
                {
                    "p_tarifa_id": None,
                    "p_zona_opcion_id": relation["zona_opcion"]["id"],
                    "p_peso_min_kg": str(payload.peso_min_kg),
                    "p_peso_max_kg": str(payload.peso_max_kg),
                    "p_valor": str(payload.valor),
                    "p_usuario": usuario_id(user),
                },
            ),
            "crear tarifa y registrar auditoría",
        ),
        "No se pudo crear la tarifa.",
    )


@router.put("/envios/tarifas/{tarifa_id}", summary="Actualizar tarifa")
def actualizar_tarifa(
    tarifa_id: UUID,
    payload: TarifaInput,
    user: dict = Depends(verificar_token),
):
    requiere_admin(user)
    db = get_supabase()
    current = _unico(
        ejecutar(db.table("env_tarifas").select("*").eq("id", str(tarifa_id)),
                 "consultar tarifa"),
        "La tarifa no existe.",
    )
    overlap = ejecutar(
        db.table("env_tarifas")
        .select("id")
        .eq("zona_opcion_id", current["zona_opcion_id"])
        .neq("id", str(tarifa_id))
        .lt("peso_min_kg", str(payload.peso_max_kg))
        .gt("peso_max_kg", str(payload.peso_min_kg)),
        "validar que los rangos de tarifa no se solapen",
    )
    if overlap:
        raise HTTPException(status_code=409, detail="El rango de peso se solapa con otra tarifa.")
    return _unico(
        ejecutar(
            db.rpc(
                "guardar_tarifa_transaccional",
                {
                    "p_tarifa_id": str(tarifa_id),
                    "p_zona_opcion_id": current["zona_opcion_id"],
                    "p_peso_min_kg": str(payload.peso_min_kg),
                    "p_peso_max_kg": str(payload.peso_max_kg),
                    "p_valor": str(payload.valor),
                    "p_usuario": usuario_id(user),
                },
            ),
            "actualizar tarifa y registrar auditoría",
        ),
        "La tarifa no existe.",
    )


@router.get(
    "/envios/tarifas/{tarifa_id}/historial",
    summary="Consultar cambios auditados de una tarifa",
)
def historial_tarifa(
    tarifa_id: UUID,
    user: dict = Depends(verificar_token),
):
    requiere_admin(user)
    _unico(
        ejecutar(
            get_supabase().table("env_tarifas").select("id").eq("id", str(tarifa_id)),
            "consultar la tarifa",
        ),
        "La tarifa no existe.",
    )
    return ejecutar(
        get_supabase().table("env_tarifas_historial")
        .select("*")
        .eq("tarifa_id", str(tarifa_id))
        .order("creado_en"),
        "consultar el historial de la tarifa",
    )


@router.get("/envios/transportadoras", summary="Consultar transportadoras activas")
def listar_transportadoras(
    incluir_inactivas: bool = False,
    user: dict = Depends(verificar_token),
):
    if incluir_inactivas:
        requiere_admin(user)
    query = get_supabase().table("env_transportadoras").select("*").order("nombre")
    if not incluir_inactivas:
        query = query.eq("activa", True)
    return ejecutar(query, "listar transportadoras")


@router.post(
    "/envios/transportadoras",
    status_code=201,
    summary="Registrar transportadora",
)
def crear_transportadora(
    payload: TransportadoraInput,
    user: dict = Depends(verificar_token),
):
    requiere_admin(user)
    data = payload.model_dump(mode="json")
    data["nit"] = "".join(char for char in payload.nit if char.isdigit())
    data["nombre"] = payload.nombre.strip()
    data["contacto"] = payload.contacto.strip()
    data["actualizado_en"] = _ahora()
    if len(data["nit"]) < 5:
        raise HTTPException(status_code=422, detail="El NIT debe contener al menos cinco dígitos.")
    return _unico(
        ejecutar(
            get_supabase().table("env_transportadoras").insert(data),
            "crear la transportadora",
        ),
        "No se pudo crear la transportadora.",
    )


@router.put("/envios/transportadoras/{transportadora_id}", summary="Actualizar transportadora")
def actualizar_transportadora(
    transportadora_id: UUID,
    payload: TransportadoraInput,
    user: dict = Depends(verificar_token),
):
    requiere_admin(user)
    data = payload.model_dump(mode="json")
    data["nit"] = "".join(char for char in payload.nit if char.isdigit())
    data["nombre"] = payload.nombre.strip()
    data["contacto"] = payload.contacto.strip()
    data["actualizado_en"] = _ahora()
    if len(data["nit"]) < 5:
        raise HTTPException(status_code=422, detail="El NIT debe contener al menos cinco dígitos.")
    return _unico(
        ejecutar(
            get_supabase().table("env_transportadoras")
            .update(data)
            .eq("id", str(transportadora_id))
            .select("*"),
            "actualizar la transportadora",
        ),
        "La transportadora no existe.",
    )


@router.get("/productos/{producto_id}/envio", summary="Consultar ficha logística de producto")
def obtener_producto_envio(
    producto_id: UUID,
    user: dict = Depends(verificar_token),
):
    db = get_supabase()
    product = _unico(
        ejecutar(
            db.table("env_productos")
            .select("*")
            .eq("producto_id", str(producto_id))
            .eq("activo", True),
            "consultar la ficha logística del producto",
        ),
        "El producto no tiene una ficha logística activa.",
    )
    if not es_admin(user) and product["vendedor_id"] != usuario_id(user):
        raise HTTPException(status_code=403, detail="No puede consultar la ficha de otro vendedor.")
    return product


@router.put("/productos/{producto_id}/envio", summary="Registrar peso, dimensiones y opción")
def guardar_producto_envio(
    producto_id: UUID,
    payload: ProductoEnvioInput,
    user: dict = Depends(verificar_token),
):
    vendedor_id = requiere_vendedor(user)
    db = get_supabase()
    _unico(
        ejecutar(
            db.table("env_opciones_envio")
            .select("id")
            .eq("id", str(payload.opcion_envio_id))
            .eq("activa", True),
            "validar la opción de envío del producto",
        ),
        "La opción de envío no existe o está inactiva.",
    )
    current = ejecutar(
        db.table("env_productos").select("*").eq("producto_id", str(producto_id)),
        "consultar configuración previa del producto",
    )
    if current and current[0]["vendedor_id"] != vendedor_id and not es_admin(user):
        raise HTTPException(status_code=403, detail="No puede modificar productos de otro vendedor.")
    data = payload.model_dump(mode="json")
    data["producto_id"] = str(producto_id)
    data["vendedor_id"] = current[0]["vendedor_id"] if current else vendedor_id
    data["activo"] = True
    if current:
        data["actualizado_en"] = _ahora()
        return _unico(
            ejecutar(
                db.table("env_productos")
                .update(data)
                .eq("producto_id", str(producto_id))
                .select("*"),
                "actualizar la ficha logística del producto",
            ),
            "El producto no existe.",
        )
    return _unico(
        ejecutar(db.table("env_productos").insert(data), "crear la ficha logística del producto"),
        "No se pudo crear la ficha logística.",
    )


@router.delete("/productos/{producto_id}/envio", status_code=204, summary="Desactivar ficha logística")
def desactivar_producto_envio(
    producto_id: UUID,
    user: dict = Depends(verificar_token),
):
    db = get_supabase()
    product = _unico(
        ejecutar(
            db.table("env_productos").select("*").eq("producto_id", str(producto_id)),
            "consultar la ficha logística del producto",
        ),
        "El producto no tiene ficha logística.",
    )
    requiere_vendedor(user, product["vendedor_id"])
    ejecutar(
        db.table("env_productos")
        .update({"activo": False})
        .eq("producto_id", str(producto_id)),
        "desactivar la ficha logística del producto",
    )


@router.get("/direcciones", summary="Listar direcciones guardadas del usuario")
def listar_direcciones(user: dict = Depends(verificar_token)):
    return ejecutar(
        get_supabase().table("env_direcciones")
        .select("*")
        .eq("usuario_id", usuario_id(user))
        .order("predeterminada", desc=True)
        .order("creado_en", desc=True),
        "listar direcciones",
    )


@router.post("/direcciones", status_code=201, summary="Guardar dirección y consentimiento")
def crear_direccion(
    payload: DireccionInput,
    user: dict = Depends(verificar_token),
):
    if not payload.acepta_tratamiento_datos:
        raise HTTPException(
            status_code=422,
            detail="Se requiere aceptar la política de tratamiento de datos para guardar la dirección.",
        )
    db = get_supabase()
    municipality = municipio_oficial(
        db,
        payload.municipio_codigo,
        payload.municipio,
        payload.departamento,
    )
    if payload.predeterminada:
        ejecutar(
            db.table("env_direcciones")
            .update({"predeterminada": False})
            .eq("usuario_id", usuario_id(user))
            .eq("predeterminada", True),
            "actualizar dirección predeterminada",
        )
    data = payload.model_dump(mode="json")
    data["usuario_id"] = usuario_id(user)
    data["nombre_destinatario"] = payload.nombre_destinatario.strip()
    data["municipio"] = municipality["municipio"]
    data["municipio_codigo"] = municipality["codigo"]
    data["departamento"] = municipality["departamento"]
    data["consentimiento_en"] = _ahora()
    data["actualizado_en"] = _ahora()
    return _unico(
        ejecutar(db.table("env_direcciones").insert(data), "guardar la dirección"),
        "No se pudo guardar la dirección.",
    )


@router.put("/direcciones/{direccion_id}", summary="Actualizar dirección guardada")
def actualizar_direccion(
    direccion_id: UUID,
    payload: DireccionInput,
    user: dict = Depends(verificar_token),
):
    if not payload.acepta_tratamiento_datos:
        raise HTTPException(status_code=422, detail="Se requiere aceptar la política de tratamiento de datos.")
    db = get_supabase()
    current = _unico(
        ejecutar(
            db.table("env_direcciones")
            .select("*")
            .eq("id", str(direccion_id))
            .eq("usuario_id", usuario_id(user)),
            "consultar la dirección",
        ),
        "La dirección no existe.",
    )
    municipality = municipio_oficial(
        db,
        payload.municipio_codigo,
        payload.municipio,
        payload.departamento,
    )
    if payload.predeterminada:
        ejecutar(
            db.table("env_direcciones")
            .update({"predeterminada": False})
            .eq("usuario_id", usuario_id(user))
            .eq("predeterminada", True),
            "actualizar dirección predeterminada",
        )
    data = payload.model_dump(mode="json")
    data["municipio"] = municipality["municipio"]
    data["municipio_codigo"] = municipality["codigo"]
    data["departamento"] = municipality["departamento"]
    data["consentimiento_en"] = _ahora()
    data["actualizado_en"] = _ahora()
    return _unico(
        ejecutar(
            db.table("env_direcciones")
            .update(data)
            .eq("id", current["id"])
            .eq("usuario_id", usuario_id(user))
            .select("*"),
            "actualizar dirección",
        ),
        "La dirección no existe.",
    )


@router.delete("/direcciones/{direccion_id}", status_code=204, summary="Eliminar dirección")
def eliminar_direccion(
    direccion_id: UUID,
    user: dict = Depends(verificar_token),
):
    db = get_supabase()
    _unico(
        ejecutar(
            db.table("env_direcciones")
            .select("id")
            .eq("id", str(direccion_id))
            .eq("usuario_id", usuario_id(user)),
            "consultar la dirección",
        ),
        "La dirección no existe.",
    )
    if ejecutar(
        db.table("ord_pedidos").select("id").eq("direccion_id", str(direccion_id)),
        "validar que la dirección no esté asociada a un pedido",
    ):
        raise HTTPException(
            status_code=409,
            detail="No se puede eliminar una dirección utilizada en una orden.",
        )
    ejecutar(
        db.table("env_direcciones")
        .delete()
        .eq("id", str(direccion_id))
        .eq("usuario_id", usuario_id(user)),
        "eliminar la dirección",
    )


@router.post("/envios/cotizaciones", summary="Cotizar envío antes de crear la orden")
def cotizar(payload: CotizacionInput, user: dict = Depends(verificar_token)):
    db = get_supabase()
    municipality = municipio_oficial(
        db,
        payload.municipio_codigo,
        payload.ciudad_destino,
        payload.departamento_destino,
    )
    items = [
        {
            "producto_id": str(item.producto_id),
            "cantidad": item.cantidad,
            "precio_unitario": item.precio_unitario,
        }
        for item in payload.items
    ]
    subtotal = sum(
        (Decimal(str(item["cantidad"])) * Decimal(str(item["precio_unitario"])) for item in items),
        Decimal("0"),
    )
    result = cotizar_envio(
        db,
        municipality["municipio"],
        payload.municipio_codigo,
        items,
        dinero(subtotal),
    )
    result["aviso_privacidad"] = AVISO_PRIVACIDAD
    return result
