# Marketplace USC — API de Órdenes y Envíos

Servicio FastAPI sin interfaz de usuario. La aplicación que consume el módulo debe
presentar sus propias pantallas y enviar un JWT del servicio central.

## Preparación local y Supabase

1. Cree el proyecto Supabase de órdenes descrito en `GUIA_EQUIPO_1.md`.
2. Ejecute el SQL de esa guía y después ejecute completo
   [`sql/schema.sql`](./sql/schema.sql). El segundo script añade los campos de
   destinatario y logística, las tablas de envíos y las funciones transaccionales
   que usa esta API. No sustituya el SQL de la guía por una copia parcial.
3. Ejecute [`sql/seed_municipios.sql`](./sql/seed_municipios.sql) para cargar el
   catálogo DIVIPOLA oficial usado por la administración de zonas. La fuente es
   [datos.gov.co, dataset gdxc-w37w](https://www.datos.gov.co/resource/gdxc-w37w).
   Si el catálogo oficial cambia, actualice `data/municipios_divipola.csv` y
   regenere el script de carga.
4. Configure `SUPABASE_URL` y `SUPABASE_KEY` en el entorno. En el servidor use
   únicamente la clave `service_role` del **proyecto de órdenes**: la clave `anon`
   no tiene permisos de escritura con las políticas abiertas de la guía retiradas.
   Nunca exponga esta clave a la aplicación cliente.
5. Copie `.env.example` a `.env` para desarrollo y complete los valores. No
   sobrescriba ni publique el `.env` existente.
6. Instale dependencias con `pip install -r requirements.txt` y ejecute
   `uvicorn main:app --reload`.

Render sirve Swagger en `/docs`, OpenAPI en `/openapi.json`, liveness en
`/api/health` y readiness en `/api/health/ready`.

## Autenticación y autorización

Las rutas de negocio requieren `Authorization: Bearer <JWT>`. Se verifica la
firma con el JWKS central, el issuer y el audience `authenticated`. No existe
fallback de decodificación sin verificar la firma. Los roles de administrador
para configuración de zonas, opciones, tarifas y transportadoras deben estar en
`app_metadata.role`; no se concede administración por `user_metadata`.

Compradores solo pueden leer sus pedidos y crear pedidos para sí mismos.
Vendedores solo pueden consultar y cambiar el estado de pedidos propios.
El administrador puede gestionar catálogos y consultar pedidos indicando
`usuario_id` y `rol`. Los datos personales de destinatario requieren aceptación
de la política de tratamiento de datos.

`ALLOW_DEV_TOKEN` está desactivado por defecto. Para pruebas locales aisladas,
se puede habilitar explícitamente junto con `DEV_USER_ID` y `DEV_USER_ROLE`;
no habilite esta opción en Render.

## Endpoints

### Órdenes

- `POST /api/ordenes`: crea pedido, reserva inventario, calcula subtotal, IVA,
  envío, descuentos y total, y registra el historial inicial.
- `GET /api/ordenes?usuario_id={id}&rol={COMPRADOR|VENDEDOR}`: lista solo las
  órdenes que el usuario autenticado está autorizado a consultar. Para comprador
  o vendedor, `usuario_id` debe ser su propio UUID; el administrador puede elegir
  un usuario.
- `GET /api/ordenes/{id}`: comprobante, ítems, auditoría y aviso de privacidad.
- `GET /api/ordenes/{id}/rastreo`: estados, origen, destino y guía.
- `PUT /api/ordenes/{id}/estado`: cambia estado con validación de transición,
  auditoría transaccional y notificaciones.
- `PUT /api/ordenes/{id}/direccion`: cambia dirección mientras el pedido siga en
  `PENDIENTE_PAGO`.

Ejemplo mínimo de `POST /api/ordenes` (con JWT de comprador):

```json
{
  "vendedor_id": "22222222-2222-2222-2222-222222222222",
  "direccion_origen": "Calle 5 # 62-00",
  "ciudad_origen": "Cali",
  "direccion_entrega": "Avenida 6N # 28-10",
  "ciudad_entrega": "Cali",
  "ciudad_entrega_codigo": "76001",
  "destinatario_departamento": "VALLE DEL CAUCA",
  "destinatario_nombre": "Nombre del destinatario",
  "destinatario_telefono": "3001234567",
  "acepta_tratamiento_datos": true,
  "items": [
    {
      "producto_id": "33333333-3333-3333-3333-333333333333",
      "nombre_producto": "Producto",
      "cantidad": 1,
      "precio_unitario": 180000
    }
  ]
}
```

También se puede enviar `direccion_id` para usar una dirección guardada del
comprador; en ese caso se omiten los campos de destino y destinatario. El
comprador se obtiene del JWT, no del cuerpo de la solicitud.

El total se calcula en servidor. Como no se entregó un módulo de cupones ni una
fuente confiable para validar promociones, la API no acepta `descuentos` enviado
por el cliente y registra cero descuentos; así se evita que el consumidor altere
el precio final.

La regla de la guía se aplica en todos los casos: envío gratis cuando el subtotal
es **mayor** a $200.000 COP. Para pedidos bajo el umbral se aplica la tarifa
configurada por zona, opción y peso; si esa opción no tiene un rango que cubra el
peso, se usa $15.000 COP como tarifa base de la guía y se devuelve una advertencia.
Todos los productos deben tener peso, dimensiones y opción configurados por su
vendedor antes de cotizar o crear el pedido. Los rangos de peso son semiabiertos
`[peso_min_kg, peso_max_kg)`. Una orden solo puede reunir productos del mismo
vendedor.

Transiciones: `PENDIENTE_PAGO → CONFIRMADA → EN_PREPARACION → LISTA_DESPACHO →
ENVIADA → ENTREGADA → DEVOLUCION_SOLICITADA`. `CANCELADA` es una salida terminal
permitida antes del despacho; `DEVOLUCION_SOLICITADA` solo procede desde
`ENTREGADA`. Se requiere transportadora activa y número de guía para pasar a
`ENVIADA`.

### Envíos (HU)

- Zonas y municipios: `GET/POST /api/envios/zonas`,
  `PUT /api/envios/zonas/{zona_id}`.
- Catálogo oficial: `GET /api/envios/municipios?departamento=...&buscar=...`.
  Cada zona requiere el código DIVIPOLA correspondiente; no se aceptan nombres
  arbitrarios de municipio.
- Opciones: `GET/POST /api/envios/opciones`,
  `PUT /api/envios/opciones/{opcion_id}`. Una opción no puede desactivarse si es
  la única activa en una zona.
- Cobertura de opción: `PUT /api/envios/zonas/{zona_id}/opciones/{opcion_id}`
  con `{"activa": true|false}`. Cada zona conserva al menos una opción activa.
- Tarifas: `GET/POST /api/envios/tarifas?zona_id=...&opcion_envio_id=...`,
  `PUT /api/envios/tarifas/{tarifa_id}` y
  `GET /api/envios/tarifas/{tarifa_id}/historial`. No se permiten rangos
  superpuestos; altas y cambios se registran en una auditoría inmutable.
- Transportadoras: `GET/POST /api/envios/transportadoras`,
  `PUT /api/envios/transportadoras/{id}`. Las rutas de despacho solo aceptan
  transportadoras activas.
- Peso, dimensiones y opción por producto: `GET/PUT/DELETE
  /api/productos/{producto_id}/envio`. Vendedor autenticado solo administra sus
  productos.
- Direcciones: `GET/POST /api/direcciones`,
  `PUT/DELETE /api/direcciones/{direccion_id}`. La dirección predeterminada es
  única por usuario.
- Cotización previa: `POST /api/envios/cotizaciones`.

Al consultar zonas, opciones o transportadoras se listan solo registros activos.
El administrador puede añadir `?incluir_inactivas=true` para ver registros
inactivos. Las escrituras de configuración requieren `app_metadata.role` de
administrador.

## Integraciones externas

- Inventario: antes de crear la orden se valida cada producto mediante
  `GET {URL_INVENTARIO}/api/productos/{producto_id}` y se reserva stock con
  `POST {URL_INVENTARIO}/api/inventario/reservar` usando `referencia_id` igual al
  número de orden e `items[{producto_id,cantidad}]`. Al confirmar el pago se
  llama a `POST {URL_INVENTARIO}/api/inventario/confirmar/{referencia_id}` sin
  cuerpo. Las rutas son configurables mediante las variables de Inventario.
- Compensación: si se cancela una reserva todavía no despachada, el módulo intenta
  ajustar cada producto mediante
  `POST /api/inventario/productos/{producto_id}/movimientos` con
  `{"tipo":"AJUSTE","cantidad":N,"nota":"..."}`. Equipo 4 debe confirmar
  si `cantidad` positiva representa devolución/entrada o si necesita incrementos
  negativos.
- Notificaciones: `POST {URL_NOTIFICACIONES}/api/notificaciones` al cambiar
  estados.
- Contabilidad: al confirmar, `POST
  {URL_CONTABILIDAD}/api/contabilidad/escrow/retener`; al cancelar con
  `requiere_reembolso: true`, `POST
  {URL_CONTABILIDAD}/api/contabilidad/escrow/bloquear/{pedido_id}`.
- Seguimiento: al confirmar el pedido, `POST
  {URL_SEGUIMIENTO}/api/seguimiento/crear` con los datos del pedido y la dirección.
- Se notifica al comprador y al vendedor en cada cambio relevante de estado.

Las fallas de notificaciones/contabilidad se informan en `advertencias` y se
registran en el log; no revierten un cambio de estado ya confirmado. La ruta de
inventario confirmada por Equipo 4 se configura por URL y ruta.

## Despliegue

Render usa `render.yaml`; configure allí `SUPABASE_URL`, `SUPABASE_KEY` y
`URL_INVENTARIO`. Las URLs de notificaciones, contabilidad y autenticación ya
están definidas según los datos entregados. La URL pública depende de crear el
Web Service y desplegar el repositorio en la cuenta del equipo; no se puede
determinar antes del despliegue.
