from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class ItemInput(BaseModel):
    producto_id: UUID
    nombre_producto: str = Field(min_length=1, max_length=255)
    cantidad: int = Field(gt=0)
    precio_unitario: Decimal = Field(ge=0, max_digits=14, decimal_places=2)

    @field_validator("nombre_producto")
    @classmethod
    def nombre_no_vacio(cls, value: str) -> str:
        return value.strip()


class CrearOrdenInput(BaseModel):
    comprador_id: Optional[UUID] = None
    vendedor_id: UUID
    direccion_id: Optional[UUID] = None
    direccion_origen: Optional[str] = Field(default=None, min_length=1, max_length=500)
    ciudad_origen: Optional[str] = Field(default=None, min_length=1, max_length=100)
    direccion_entrega: Optional[str] = Field(default=None, min_length=1, max_length=500)
    ciudad_entrega: Optional[str] = Field(default=None, min_length=1, max_length=100)
    ciudad_entrega_codigo: Optional[str] = Field(default=None, pattern=r"^[0-9]{5}$")
    destinatario_nombre: Optional[str] = Field(default=None, min_length=1, max_length=200)
    destinatario_telefono: Optional[str] = Field(
        default=None,
        min_length=7,
        max_length=30,
        pattern=r"^\+?[0-9][0-9\s().-]{5,27}$",
    )
    destinatario_barrio: Optional[str] = Field(default=None, max_length=100)
    destinatario_departamento: Optional[str] = Field(default=None, max_length=100)
    destinatario_indicaciones: Optional[str] = Field(default=None, max_length=1000)
    acepta_tratamiento_datos: bool = False
    notas: Optional[str] = Field(default=None, max_length=2000)
    items: list[ItemInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def productos_unicos(self):
        ids = [item.producto_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("Cada producto debe aparecer una sola vez en la orden.")
        return self


class ActualizarEstadoInput(BaseModel):
    nuevo_estado: str = Field(min_length=1, max_length=40)
    observaciones: Optional[str] = Field(default=None, max_length=2000)
    requiere_reembolso: bool = False
    transportadora_id: Optional[UUID] = None
    numero_guia: Optional[str] = Field(default=None, max_length=100)
    fecha_estimada_entrega: Optional[date] = None


class ZonaInput(BaseModel):
    nombre: str = Field(min_length=2, max_length=100)
    departamento: str = Field(min_length=2, max_length=100)
    municipio: str = Field(min_length=2, max_length=100)
    municipio_codigo: str = Field(pattern=r"^[0-9]{5}$")
    activa: bool = True


class OpcionEnvioInput(BaseModel):
    nombre: str = Field(min_length=2, max_length=100)
    modalidad: str = Field(min_length=2, max_length=50)
    tiempo_minimo_dias: int = Field(ge=0, le=365)
    activa: bool = True


class AsociarOpcionInput(BaseModel):
    activa: bool = True


class TarifaInput(BaseModel):
    peso_min_kg: Decimal = Field(ge=0, max_digits=10, decimal_places=3)
    peso_max_kg: Decimal = Field(gt=0, max_digits=10, decimal_places=3)
    valor: Decimal = Field(ge=0, max_digits=14, decimal_places=0)

    @field_validator("peso_max_kg")
    @classmethod
    def rango_valido(cls, value: Decimal, info):
        minimum = info.data.get("peso_min_kg")
        if minimum is not None and value <= minimum:
            raise ValueError("El peso máximo debe ser mayor que el mínimo.")
        return value


class TransportadoraInput(BaseModel):
    nombre: str = Field(min_length=2, max_length=120)
    nit: str = Field(
        min_length=5,
        max_length=30,
        pattern=r"^[0-9.\-\s]+$",
    )
    contacto: str = Field(min_length=3, max_length=200)
    activa: bool = True


class ProductoEnvioInput(BaseModel):
    peso_kg: Decimal = Field(gt=0, max_digits=10, decimal_places=3)
    largo_cm: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    ancho_cm: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    alto_cm: Decimal = Field(gt=0, max_digits=10, decimal_places=2)
    opcion_envio_id: UUID


class DireccionInput(BaseModel):
    nombre_destinatario: str = Field(min_length=2, max_length=200)
    telefono: str = Field(
        min_length=7,
        max_length=30,
        pattern=r"^\+?[0-9][0-9\s().-]{5,27}$",
    )
    direccion: str = Field(min_length=5, max_length=500)
    barrio: Optional[str] = Field(default=None, max_length=100)
    municipio: str = Field(min_length=2, max_length=100)
    municipio_codigo: str = Field(pattern=r"^[0-9]{5}$")
    departamento: str = Field(min_length=2, max_length=100)
    indicaciones: Optional[str] = Field(default=None, max_length=1000)
    acepta_tratamiento_datos: bool
    predeterminada: bool = False


class DireccionOrdenInput(BaseModel):
    direccion_id: Optional[UUID] = None
    nombre_destinatario: Optional[str] = Field(default=None, min_length=2, max_length=200)
    telefono: Optional[str] = Field(
        default=None,
        min_length=7,
        max_length=30,
        pattern=r"^\+?[0-9][0-9\s().-]{5,27}$",
    )
    direccion: Optional[str] = Field(default=None, min_length=5, max_length=500)
    barrio: Optional[str] = Field(default=None, max_length=100)
    municipio: Optional[str] = Field(default=None, min_length=2, max_length=100)
    municipio_codigo: Optional[str] = Field(default=None, pattern=r"^[0-9]{5}$")
    departamento: Optional[str] = Field(default=None, min_length=2, max_length=100)
    indicaciones: Optional[str] = Field(default=None, max_length=1000)
    acepta_tratamiento_datos: bool = False
    predeterminada: bool = False


class CotizacionItemInput(BaseModel):
    producto_id: UUID
    cantidad: int = Field(gt=0)
    precio_unitario: Decimal = Field(ge=0, max_digits=14, decimal_places=2)


class CotizacionInput(BaseModel):
    ciudad_destino: str = Field(min_length=2, max_length=100)
    municipio_codigo: str = Field(pattern=r"^[0-9]{5}$")
    departamento_destino: str = Field(min_length=2, max_length=100)
    items: list[CotizacionItemInput] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def productos_unicos(self):
        ids = [item.producto_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("Cada producto debe aparecer una sola vez en la cotización.")
        return self
