from pydantic import BaseModel, Field
from typing import List, Optional, Literal

EstadoOrden = Literal[
    "PENDIENTE_PAGO",
    "CONFIRMADA",
    "EN_PREPARACION",
    "LISTA_DESPACHO",
    "ENVIADA",
    "ENTREGADA",
    "CANCELADA",
    "DEVOLUCION_SOLICITADA"
]

class ItemInput(BaseModel):
    producto_id: str
    nombre_producto: str
    cantidad: int = Field(..., gt=0)
    precio_unitario: float = Field(..., ge=0)

class CrearOrdenInput(BaseModel):
    comprador_id: str
    vendedor_id: str
    direccion_entrega: str
    ciudad_entrega: str = "Cali"
    descuentos: float = Field(default=0.0, ge=0)
    notas: Optional[str] = None
    items: List[ItemInput]

class ActualizarEstadoInput(BaseModel):
    nuevo_estado: EstadoOrden
    observaciones: Optional[str] = None