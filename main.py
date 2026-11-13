import os
import uuid
import secrets
from datetime import datetime, timezone
from typing import Literal
from dotenv import load_dotenv
import httpx
from fastapi import FastAPI, Depends, HTTPException, Header, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, field_validator

load_dotenv()

MODO_INDEPENDIENTE = os.getenv("MODO_INDEPENDIENTE", "true").lower() == "true"

if not MODO_INDEPENDIENTE:
    from database import supabase
    from auth import verificar_token as verificar_token_real
else:
    supabase = None
    verificar_token_real = None

# ==============================================================================
# ESTADOS Y TRANSICIONES OFICIALES
# ==============================================================================
EstadoOrden = Literal[
    "PENDIENTE_PAGO",
    "CONFIRMADA",
    "EN_PREPARACION",
    "LISTA_DESPACHO",
    "ENVIADA",
    "ENTREGADA",
    "CANCELADA",
    "DEVOLUCION_SOLICITADA",
]

TRANSICIONES_VALIDAS: dict[str, list[str]] = {
    "PENDIENTE_PAGO": ["CONFIRMADA", "CANCELADA"],
    "CONFIRMADA": ["EN_PREPARACION", "CANCELADA"],
    "EN_PREPARACION": ["LISTA_DESPACHO", "CANCELADA"],
    "LISTA_DESPACHO": ["ENVIADA"],
    "ENVIADA": ["ENTREGADA"],
    "ENTREGADA": ["DEVOLUCION_SOLICITADA"],
    "CANCELADA": [],
    "DEVOLUCION_SOLICITADA": [],
}

# ==============================================================================
# ESQUEMAS DE ENTRADA CON VALIDACIÓN ESTRICTA
# ==============================================================================
class ItemOrdenInput(BaseModel):
    producto_id: str = Field(..., min_length=1)
    nombre_producto: str = Field(..., min_length=1)
    cantidad: int = Field(..., gt=0)
    precio_unitario: float = Field(..., gt=0)


class CrearOrdenInput(BaseModel):
    comprador_id: str = Field(..., min_length=1)
    vendedor_id: str = Field(..., min_length=1)
    items: list[ItemOrdenInput] = Field(..., min_length=1)
    descuentos: float = Field(default=0.0, ge=0)
    direccion_entrega: str = Field(..., min_length=3)
    ciudad_entrega: str = Field(..., min_length=2)
    notas: str | None = Field(default=None)


class ActualizarEstadoInput(BaseModel):
    nuevo_estado: EstadoOrden
    observaciones: str | None = Field(default=None)

    @field_validator("nuevo_estado", mode="before")
    @classmethod
    def normalizar_estado(cls, v: str) -> str:
        return v.strip().upper() if isinstance(v, str) else v


class CancelarOrdenInput(BaseModel):
    motivo: str = Field(..., min_length=3)


# ==============================================================================
# SIMULACIÓN LOCAL DE SUPABASE (SOLO SI MODO_INDEPENDIENTE=true)
# ==============================================================================
DB_LOCAL = {
    "ord_pedidos": [],
    "ord_items_pedido": [],
    "ord_historial_estados": [],
}


def ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def verificar_token_seguro(authorization: str | None = Header(default=None)):
    if not MODO_INDEPENDIENTE and verificar_token_real is not None:
        return await verificar_token_real(authorization)
    return {"sub": "00000000-0000-0000-0000-000000000001", "rol": "COMPRADOR"}


# ==============================================================================
# CONFIGURACIÓN FASTAPI
# ==============================================================================
app = FastAPI(
    title="Servicio de Órdenes - Equipo 1",
    version="2.1.0",
    docs_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==============================================================================
# INTERFAZ VISUAL LIMPIA (ACUMULA ÓRDENES ABAJO Y LIMPIA PARA ESCRIBIR OTRA)
# ==============================================================================
HTML_PANEL_VISUAL = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8" />
  <title>Servicio de Órdenes - Equipo 1</title>
  <style>
    body { font-family: system-ui, -apple-system, sans-serif; background: #f1f5f9; color: #0f172a; margin: 0; padding: 24px; }
    .container { max-width: 1020px; margin: 0 auto; }
    .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
    .badge { background: #dcfce7; color: #166534; padding: 6px 14px; border-radius: 999px; font-weight: 600; font-size: 13px; }
    .card { background: #ffffff; border-radius: 12px; padding: 22px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }
    h2 { margin-top: 0; font-size: 18px; color: #1e293b; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 12px; }
    label { display: block; font-size: 12px; font-weight: 600; color: #475569; margin-bottom: 4px; }
    input, select { width: 100%; padding: 9px 10px; border: 1px solid #cbd5e1; border-radius: 8px; box-sizing: border-box; font-size: 14px; }
    input:focus, select:focus { outline: none; border-color: #2563eb; }
    button { background: #2563eb; color: #fff; border: none; padding: 10px 18px; border-radius: 8px; font-weight: 600; cursor: pointer; margin-top: 14px; font-size: 14px; }
    button:hover { background: #1d4ed8; }
    button.sec { background: #0f766e; }
    button.warn { background: #d97706; }
    button.mini { padding: 5px 10px; margin-top: 0; font-size: 12px; background: #334155; }
    button.mini:hover { background: #0f172a; }
    .alerta { margin-top: 12px; padding: 10px 14px; border-radius: 8px; font-size: 14px; display: none; }
    .alerta.ok { background: #f0fdf4; border: 1px solid #86efac; color: #166534; display: block; }
    .alerta.err { background: #fef2f2; border: 1px solid #fca5a5; color: #991b1b; display: block; }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 13px; }
    th, td { text-align: left; padding: 10px 8px; border-bottom: 1px solid #e2e8f0; }
    th { background: #f8fafc; color: #475569; font-weight: 700; }
    .pill { display: inline-block; padding: 3px 9px; border-radius: 999px; font-size: 11px; font-weight: 700; background: #e0f2fe; color: #0369a1; }
    .vacio { color: #64748b; font-size: 14px; text-align: center; padding: 18px 0; }
  </style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>Servicio de Órdenes - Equipo 1</h1>
    <span class="badge" id="estado-srv">Conectando...</span>
  </div>

  <!-- 1. FORMULARIO PARA CREAR ORDEN -->
  <div class="card">
    <h2>1. Registrar Nueva Orden</h2>
    <div class="grid">
      <div><label>ID Comprador (UUID)</label><input id="comp" value="11111111-1111-1111-1111-111111111111" /></div>
      <div><label>ID Vendedor (UUID)</label><input id="vend" value="22222222-2222-2222-2222-222222222222" /></div>
      <div><label>ID Producto (UUID)</label><input id="prod" value="33333333-3333-3333-3333-333333333333" /></div>
      <div><label>Nombre Producto</label><input id="nom_prod" placeholder="Ej: Camiseta USC" /></div>
      <div><label>Cantidad</label><input id="cant" type="number" min="1" placeholder="Ej: 2" /></div>
      <div><label>Precio Unitario (COP)</label><input id="precio" type="number" min="1" placeholder="Ej: 50000" /></div>
      <div><label>Descuento (COP)</label><input id="desc" type="number" min="0" value="0" /></div>
      <div><label>Ciudad de Entrega</label><input id="ciudad" placeholder="Ej: Cali" /></div>
      <div><label>Dirección de Entrega</label><input id="dir" placeholder="Ej: Calle 5 # 62-00" /></div>
      <div><label>Notas (Opcional)</label><input id="notas" placeholder="Ej: Entregar en portería" /></div>
    </div>
    <button onclick="crearOrden()">Guardar Orden y Limpiar Campos</button>
    <div id="msg-crear" class="alerta"></div>
  </div>

  <!-- 2. LISTADO ACUMULADO DE ÓRDENES ABAJO -->
  <div class="card">
    <div style="display:flex; justify-content:space-between; align-items:center;">
      <h2 style="margin:0;">2. Órdenes Registradas</h2>
      <button class="sec" style="margin:0;" onclick="cargarOrdenesDesdeBD()">Actualizar Lista desde BD</button>
    </div>
    <div id="contenedor-tabla">
      <div class="vacio">Aún no has agregado órdenes en esta sesión. Registra una arriba y aparecerá aquí abajo.</div>
    </div>
  </div>

  <!-- 3. ACTUALIZAR ESTADO DE CUALQUIER ORDEN -->
  <div class="card">
    <h2>3. Actualizar Estado de Orden</h2>
    <div class="grid">
      <div><label>ID de la Orden</label><input id="ord_id_estado" placeholder="Haz clic en 'Usar ID' en la tabla de arriba" /></div>
      <div>
        <label>Nuevo Estado</label>
        <select id="nuevo_estado">
          <option value="CONFIRMADA">CONFIRMADA</option>
          <option value="EN_PREPARACION">EN_PREPARACION</option>
          <option value="LISTA_DESPACHO">LISTA_DESPACHO</option>
          <option value="ENVIADA">ENVIADA</option>
          <option value="ENTREGADA">ENTREGADA</option>
          <option value="DEVOLUCION_SOLICITADA">DEVOLUCION_SOLICITADA</option>
          <option value="CANCELADA">CANCELADA</option>
        </select>
      </div>
      <div><label>Observaciones</label><input id="obs_estado" placeholder="Ej: Pago verificado" /></div>
    </div>
    <button class="warn" onclick="cambiarEstado()">Cambiar Estado</button>
    <div id="msg-estado" class="alerta"></div>
  </div>
</div>

<script>
  let ordenesRegistradas = [];

  fetch('/api/estado-servicio').then(r => r.json()).then(d => {
    document.getElementById('estado-srv').textContent = `Activo • Modo: ${d.modo.toUpperCase()}`;
  });

  function renderizarTabla() {
    const cont = document.getElementById('contenedor-tabla');
    if (!ordenesRegistradas.length) {
      cont.innerHTML = '<div class="vacio">No hay órdenes registradas todavía.</div>';
      return;
    }
    const filas = ordenesRegistradas.map(o => `
      <tr>
        <td style="font-family:monospace; font-size:12px;">${o.id}</td>
        <td><strong>${o.numero_orden}</strong></td>
        <td><span class="pill">${o.estado}</span></td>
        <td>$${Number(o.subtotal).toLocaleString()}</td>
        <td>$${Number(o.impuestos).toLocaleString()}</td>
        <td>$${Number(o.costo_envio).toLocaleString()}</td>
        <td><strong>$${Number(o.total).toLocaleString()}</strong></td>
        <td>${o.ciudad_entrega}</td>
        <td><button class="mini" onclick="seleccionarOrden('${o.id}')">Usar ID</button></td>
      </tr>
    `).join('');

    cont.innerHTML = `
      <table>
        <thead>
          <tr>
            <th>ID Orden</th>
            <th>Número</th>
            <th>Estado</th>
            <th>Subtotal</th>
            <th>IVA (19%)</th>
            <th>Envío</th>
            <th>Total</th>
            <th>Ciudad</th>
            <th>Acción</th>
          </tr>
        </thead>
        <tbody>${filas}</tbody>
      </table>
    `;
  }

  function seleccionarOrden(id) {
    document.getElementById('ord_id_estado').value = id;
    document.getElementById('ord_id_estado').focus();
  }

  function limpiarFormularioOrden() {
    document.getElementById('nom_prod').value = '';
    document.getElementById('cant').value = '';
    document.getElementById('precio').value = '';
    document.getElementById('desc').value = '0';
    document.getElementById('ciudad').value = '';
    document.getElementById('dir').value = '';
    document.getElementById('notas').value = '';
    document.getElementById('nom_prod').focus();
  }

  async function crearOrden() {
    const msg = document.getElementById('msg-crear');
    const body = {
      comprador_id: document.getElementById('comp').value.trim(),
      vendedor_id: document.getElementById('vend').value.trim(),
      items: [{
        producto_id: document.getElementById('prod').value.trim(),
        nombre_producto: document.getElementById('nom_prod').value.trim(),
        cantidad: Number(document.getElementById('cant').value),
        precio_unitario: Number(document.getElementById('precio').value)
      }],
      descuentos: Number(document.getElementById('desc').value || 0),
      direccion_entrega: document.getElementById('dir').value.trim(),
      ciudad_entrega: document.getElementById('ciudad').value.trim(),
      notas: document.getElementById('notas').value.trim() || null
    };

    const res = await fetch('/api/ordenes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const data = await res.json();

    if (!res.ok) {
      msg.className = 'alerta err';
      msg.textContent = `Error: ${typeof data.detail === 'string' ? data.detail : 'Revisa que todos los campos estén completos'}`;
      return;
    }

    // Agrega la nueva orden arriba de la tabla de abajo y limpia los campos para escribir otra
    ordenesRegistradas.unshift(data);
    renderizarTabla();
    document.getElementById('ord_id_estado').value = data.id;
    limpiarFormularioOrden();

    msg.className = 'alerta ok';
    msg.textContent = `Orden ${data.numero_orden} guardada correctamente (Total: $${Number(data.total).toLocaleString()}). Ya puedes escribir la siguiente orden.`;
  }

  async function cargarOrdenesDesdeBD() {
    const u = document.getElementById('comp').value.trim();
    const res = await fetch(`/api/ordenes?usuario_id=${encodeURIComponent(u)}&rol=COMPRADOR`);
    if (res.ok) {
      ordenesRegistradas = await res.json();
      renderizarTabla();
    }
  }

  async function cambiarEstado() {
    const msg = document.getElementById('msg-estado');
    const id = document.getElementById('ord_id_estado').value.trim();
    const body = {
      nuevo_estado: document.getElementById('nuevo_estado').value,
      observaciones: document.getElementById('obs_estado').value.trim() || null
    };

    const res = await fetch(`/api/ordenes/${id}/estado`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const data = await res.json();

    if (!res.ok) {
      msg.className = 'alerta err';
      msg.textContent = `Error: ${data.detail || 'No se pudo actualizar el estado'}`;
      return;
    }

    // Actualiza también el estado en la fila de la tabla de abajo
    const idx = ordenesRegistradas.findIndex(o => o.id === id);
    if (idx !== -1) {
      ordenesRegistradas[idx].estado = data.estado;
      renderizarTabla();
    }

    msg.className = 'alerta ok';
    msg.textContent = `Estado de la orden ${data.numero_orden} actualizado a ${data.estado}.`;
  }

  // Carga inicial automática de órdenes existentes
  cargarOrdenesDesdeBD();
</script>
</body>
</html>"""


@app.get("/", include_in_schema=False)
@app.get("/docs", include_in_schema=False)
def panel_principal():
    return HTMLResponse(HTML_PANEL_VISUAL)


@app.get("/api/estado-servicio")
def health_check():
    return {
        "servicio": "Órdenes de Pedido - Equipo 1",
        "estado": "activo",
        "modo": "independiente" if MODO_INDEPENDIENTE else "vinculado",
    }


# ==============================================================================
# ENDPOINTS DE NEGOCIO
# ==============================================================================
@app.post("/api/ordenes", status_code=status.HTTP_201_CREATED)
async def crear_orden(payload: CrearOrdenInput, user: dict = Depends(verificar_token_seguro)):
    subtotal = round(sum(i.cantidad * i.precio_unitario for i in payload.items), 2)
    impuestos = round(subtotal * 0.19, 2)
    costo_envio = 0.0 if subtotal > 200000 else 15000.0
    bruto = round(subtotal + impuestos + costo_envio, 2)

    if payload.descuentos > bruto:
        raise HTTPException(
            status_code=400,
            detail=f"El descuento (${payload.descuentos}) no puede superar el valor total (${bruto})",
        )

    total = round(bruto - payload.descuentos, 2)
    num_orden = f"USC-{secrets.randbelow(900000) + 100000}"

    datos_pedido = {
        "comprador_id": payload.comprador_id,
        "vendedor_id": payload.vendedor_id,
        "numero_orden": num_orden,
        "estado": "PENDIENTE_PAGO",
        "subtotal": subtotal,
        "impuestos": impuestos,
        "costo_envio": costo_envio,
        "descuentos": payload.descuentos,
        "total": total,
        "direccion_entrega": payload.direccion_entrega,
        "ciudad_entrega": payload.ciudad_entrega,
        "notas": payload.notas,
    }

    if MODO_INDEPENDIENTE:
        orden_id = str(uuid.uuid4())
        orden_creada = {"id": orden_id, **datos_pedido, "creado_en": ahora_iso()}
        DB_LOCAL["ord_pedidos"].append(orden_creada)

        for it in payload.items:
            DB_LOCAL["ord_items_pedido"].append({
                "id": str(uuid.uuid4()),
                "pedido_id": orden_id,
                "producto_id": it.producto_id,
                "nombre_producto": it.nombre_producto,
                "cantidad": it.cantidad,
                "precio_unitario": it.precio_unitario,
                "subtotal": round(it.cantidad * it.precio_unitario, 2),
            })

        DB_LOCAL["ord_historial_estados"].append({
            "id": str(uuid.uuid4()),
            "pedido_id": orden_id,
            "estado_anterior": None,
            "estado_nuevo": "PENDIENTE_PAGO",
            "usuario_responsable_id": user.get("sub"),
            "observaciones": "Orden creada en el sistema",
            "fecha": ahora_iso(),
        })
    else:
        try:
            res_pedido = supabase.table("ord_pedidos").insert(datos_pedido).execute()
            if not res_pedido.data:
                raise HTTPException(status_code=500, detail="Supabase no devolvió la orden creada")

            orden_creada = res_pedido.data[0]
            orden_id = orden_creada["id"]

            items_db = [
                {
                    "pedido_id": orden_id,
                    "producto_id": it.producto_id,
                    "nombre_producto": it.nombre_producto,
                    "cantidad": it.cantidad,
                    "precio_unitario": it.precio_unitario,
                    "subtotal": round(it.cantidad * it.precio_unitario, 2),
                }
                for it in payload.items
            ]
            supabase.table("ord_items_pedido").insert(items_db).execute()

            supabase.table("ord_historial_estados").insert({
                "pedido_id": orden_id,
                "estado_anterior": None,
                "estado_nuevo": "PENDIENTE_PAGO",
                "usuario_responsable_id": user.get("sub"),
                "observaciones": "Orden creada en el sistema",
            }).execute()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Error en base de datos Supabase: {e}")

    url_inv = os.getenv("URL_INVENTARIO")
    if not MODO_INDEPENDIENTE and url_inv and "url-equipo-4" not in url_inv:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"{url_inv}/api/inventario/reservar-stock",
                    json={"orden_id": orden_id, "items": [i.model_dump() for i in payload.items]},
                )
        except Exception as e:
            print(f"Aviso: No se pudo contactar a Inventario: {e}")

    return orden_creada


@app.get("/api/ordenes")
async def listar_ordenes(
    usuario_id: str,
    rol: str = "COMPRADOR",
    estado: str | None = Query(default=None),
    user: dict = Depends(verificar_token_seguro),
):
    if MODO_INDEPENDIENTE:
        campo = "vendedor_id" if rol.upper() == "VENDEDOR" else "comprador_id"
        pedidos = [p for p in DB_LOCAL["ord_pedidos"] if p[campo] == usuario_id]
        if estado:
            pedidos = [p for p in pedidos if p["estado"] == estado.strip().upper()]
        pedidos.sort(key=lambda x: x["creado_en"], reverse=True)
        return [
            {**p, "ord_items_pedido": [it for it in DB_LOCAL["ord_items_pedido"] if it["pedido_id"] == p["id"]]}
            for p in pedidos
        ]

    try:
        query = supabase.table("ord_pedidos").select("*, ord_items_pedido(*)")
        if rol.upper() == "VENDEDOR":
            query = query.eq("vendedor_id", usuario_id)
        else:
            query = query.eq("comprador_id", usuario_id)
        if estado:
            query = query.eq("estado", estado.strip().upper())
        res = query.order("creado_en", desc=True).execute()
        return res.data or []
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error consultando Supabase: {e}")


@app.get("/api/ordenes/{orden_id}")
async def detalle_orden(orden_id: str, user: dict = Depends(verificar_token_seguro)):
    if MODO_INDEPENDIENTE:
        pedido = next((p for p in DB_LOCAL["ord_pedidos"] if p["id"] == orden_id), None)
        if not pedido:
            raise HTTPException(status_code=404, detail="Orden no encontrada")
        items = [it for it in DB_LOCAL["ord_items_pedido"] if it["pedido_id"] == orden_id]
        historial = [h for h in DB_LOCAL["ord_historial_estados"] if h["pedido_id"] == orden_id]
        return {**pedido, "ord_items_pedido": items, "ord_historial_estados": historial}

    try:
        res = (
            supabase.table("ord_pedidos")
            .select("*, ord_items_pedido(*), ord_historial_estados(*)")
            .eq("id", orden_id)
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=404, detail="Orden no encontrada")
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error consultando orden en Supabase: {e}")


@app.get("/api/ordenes/{orden_id}/historial")
async def historial_orden(orden_id: str, user: dict = Depends(verificar_token_seguro)):
    if MODO_INDEPENDIENTE:
        pedido = next((p for p in DB_LOCAL["ord_pedidos"] if p["id"] == orden_id), None)
        if not pedido:
            raise HTTPException(status_code=404, detail="Orden no encontrada")
        return [h for h in DB_LOCAL["ord_historial_estados"] if h["pedido_id"] == orden_id]

    try:
        res = (
            supabase.table("ord_historial_estados")
            .select("*")
            .eq("pedido_id", orden_id)
            .execute()
        )
        return res.data or []
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error consultando historial: {e}")


@app.put("/api/ordenes/{orden_id}/estado")
async def actualizar_estado(orden_id: str, payload: ActualizarEstadoInput, user: dict = Depends(verificar_token_seguro)):
    if MODO_INDEPENDIENTE:
        orden_actual = next((p for p in DB_LOCAL["ord_pedidos"] if p["id"] == orden_id), None)
        if not orden_actual:
            raise HTTPException(status_code=404, detail="Orden no encontrada")
    else:
        try:
            actual_res = supabase.table("ord_pedidos").select("*").eq("id", orden_id).execute()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"ID inválido o error en Supabase: {e}")
        if not actual_res.data:
            raise HTTPException(status_code=404, detail="Orden no encontrada")
        orden_actual = actual_res.data[0]

    estado_anterior = orden_actual["estado"]
    nuevo_estado = payload.nuevo_estado

    if nuevo_estado not in TRANSICIONES_VALIDAS.get(estado_anterior, []):
        raise HTTPException(
            status_code=400,
            detail=f"Transición inválida: no se puede pasar de {estado_anterior} a {nuevo_estado}",
        )

    if MODO_INDEPENDIENTE:
        orden_actual["estado"] = nuevo_estado
        DB_LOCAL["ord_historial_estados"].append({
            "id": str(uuid.uuid4()),
            "pedido_id": orden_id,
            "estado_anterior": estado_anterior,
            "estado_nuevo": nuevo_estado,
            "usuario_responsable_id": user.get("sub"),
            "observaciones": payload.observaciones or f"Cambio de estado a {nuevo_estado}",
            "fecha": ahora_iso(),
        })
        orden_actualizada = orden_actual
    else:
        try:
            res = supabase.table("ord_pedidos").update({"estado": nuevo_estado}).eq("id", orden_id).execute()
            supabase.table("ord_historial_estados").insert({
                "pedido_id": orden_id,
                "estado_anterior": estado_anterior,
                "estado_nuevo": nuevo_estado,
                "usuario_responsable_id": user.get("sub"),
                "observaciones": payload.observaciones or f"Cambio de estado a {nuevo_estado}",
            }).execute()
            orden_actualizada = res.data[0]
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Error actualizando estado en Supabase: {e}")

    url_notif = os.getenv("URL_NOTIFICACIONES")
    if not MODO_INDEPENDIENTE and url_notif and "url-equipo-3" not in url_notif:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(
                    f"{url_notif}/api/notificaciones",
                    json={"orden_id": orden_id, "usuario_id": orden_actual["comprador_id"], "estado": nuevo_estado},
                )
        except Exception as e:
            print(f"Aviso: No se pudo contactar a Notificaciones: {e}")

    if nuevo_estado == "CANCELADA" and estado_anterior != "PENDIENTE_PAGO":
        url_cont = os.getenv("URL_CONTABILIDAD")
        if not MODO_INDEPENDIENTE and url_cont and "url-equipo-6" not in url_cont:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(f"{url_cont}/api/contabilidad/escrow/bloquear/{orden_id}")
            except Exception as e:
                print(f"Aviso: No se pudo contactar a Contabilidad: {e}")

    return orden_actualizada


@app.post("/api/ordenes/{orden_id}/cancelar")
async def cancelar_orden(orden_id: str, payload: CancelarOrdenInput, user: dict = Depends(verificar_token_seguro)):
    return await actualizar_estado(
        orden_id=orden_id,
        payload=ActualizarEstadoInput(nuevo_estado="CANCELADA", observaciones=payload.motivo),
        user=user,
    )