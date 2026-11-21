CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE IF NOT EXISTS public.ord_pedidos (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    comprador_id UUID NOT NULL,
    vendedor_id UUID NOT NULL,
    numero_orden VARCHAR(40) NOT NULL UNIQUE,
    estado VARCHAR(40) NOT NULL DEFAULT 'PENDIENTE_PAGO' CHECK (
        estado IN (
            'PENDIENTE_PAGO', 'CONFIRMADA', 'EN_PREPARACION',
            'LISTA_DESPACHO', 'ENVIADA', 'ENTREGADA', 'CANCELADA',
            'DEVOLUCION_SOLICITADA'
        )
    ),
    subtotal NUMERIC(14, 2) NOT NULL CHECK (subtotal >= 0),
    impuestos NUMERIC(14, 2) NOT NULL DEFAULT 0 CHECK (impuestos >= 0),
    costo_envio NUMERIC(14, 2) NOT NULL DEFAULT 0 CHECK (costo_envio >= 0),
    descuentos NUMERIC(14, 2) NOT NULL DEFAULT 0 CHECK (descuentos >= 0),
    total NUMERIC(14, 2) NOT NULL CHECK (total >= 0),
    moneda VARCHAR(5) NOT NULL DEFAULT 'COP',
    direccion_entrega TEXT NOT NULL,
    ciudad_entrega VARCHAR(100) NOT NULL DEFAULT 'Cali',
    notas TEXT,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.ord_items_pedido (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pedido_id UUID NOT NULL REFERENCES public.ord_pedidos(id) ON DELETE CASCADE,
    producto_id UUID NOT NULL,
    nombre_producto VARCHAR(255) NOT NULL,
    cantidad INTEGER NOT NULL CHECK (cantidad > 0),
    precio_unitario NUMERIC(14, 2) NOT NULL CHECK (precio_unitario >= 0),
    subtotal NUMERIC(14, 2) NOT NULL CHECK (subtotal >= 0)
);

CREATE TABLE IF NOT EXISTS public.ord_historial_estados (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    pedido_id UUID NOT NULL REFERENCES public.ord_pedidos(id) ON DELETE CASCADE,
    estado_anterior VARCHAR(40),
    estado_nuevo VARCHAR(40) NOT NULL,
    usuario_responsable_id UUID,
    observaciones TEXT,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ord_pedidos_comprador
    ON public.ord_pedidos(comprador_id);
CREATE INDEX IF NOT EXISTS idx_ord_pedidos_vendedor
    ON public.ord_pedidos(vendedor_id);
CREATE INDEX IF NOT EXISTS idx_ord_pedidos_estado
    ON public.ord_pedidos(estado);
CREATE INDEX IF NOT EXISTS idx_ord_items_pedido
    ON public.ord_items_pedido(pedido_id);
CREATE INDEX IF NOT EXISTS idx_ord_historial_pedido
    ON public.ord_historial_estados(pedido_id, creado_en);

CREATE TABLE IF NOT EXISTS public.env_municipios_oficiales (
    codigo VARCHAR(5) PRIMARY KEY CHECK (codigo ~ '^[0-9]{5}$'),
    codigo_departamento VARCHAR(2) NOT NULL,
    departamento VARCHAR(100) NOT NULL,
    municipio VARCHAR(100) NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_env_municipios_oficiales_nombre
    ON public.env_municipios_oficiales(lower(departamento), lower(municipio));

ALTER TABLE public.ord_pedidos
    ADD COLUMN IF NOT EXISTS direccion_origen TEXT,
    ADD COLUMN IF NOT EXISTS ciudad_origen VARCHAR(100),
    ADD COLUMN IF NOT EXISTS destinatario_nombre VARCHAR(200),
    ADD COLUMN IF NOT EXISTS destinatario_telefono VARCHAR(30),
    ADD COLUMN IF NOT EXISTS destinatario_barrio VARCHAR(100),
    ADD COLUMN IF NOT EXISTS destinatario_departamento VARCHAR(100),
    ADD COLUMN IF NOT EXISTS destinatario_municipio_codigo VARCHAR(5),
    ADD COLUMN IF NOT EXISTS destinatario_indicaciones TEXT,
    ADD COLUMN IF NOT EXISTS acepta_tratamiento_datos BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS transportadora_id UUID,
    ADD COLUMN IF NOT EXISTS transportadora_nombre VARCHAR(120),
    ADD COLUMN IF NOT EXISTS numero_guia VARCHAR(100),
    ADD COLUMN IF NOT EXISTS fecha_estimada_entrega DATE,
    ADD COLUMN IF NOT EXISTS direccion_id UUID,
    ADD COLUMN IF NOT EXISTS zona_envio_id UUID,
    ADD COLUMN IF NOT EXISTS detalle_envio JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE public.ord_items_pedido
    ADD COLUMN IF NOT EXISTS peso_kg NUMERIC(10, 3),
    ADD COLUMN IF NOT EXISTS opcion_envio_id UUID,
    ADD COLUMN IF NOT EXISTS tarifa_envio NUMERIC(14, 2);

CREATE TABLE IF NOT EXISTS public.env_zonas (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nombre VARCHAR(100) NOT NULL,
    departamento VARCHAR(100) NOT NULL,
    municipio VARCHAR(100) NOT NULL,
    municipio_codigo VARCHAR(5),
    activa BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.env_zonas
    ADD COLUMN IF NOT EXISTS municipio_codigo VARCHAR(5);
CREATE UNIQUE INDEX IF NOT EXISTS idx_env_zonas_municipio_activo
    ON public.env_zonas(lower(departamento), lower(municipio))
    WHERE activa;
CREATE UNIQUE INDEX IF NOT EXISTS idx_env_zonas_codigo_activo
    ON public.env_zonas(municipio_codigo)
    WHERE activa AND municipio_codigo IS NOT NULL;
ALTER TABLE public.env_zonas
    DROP CONSTRAINT IF EXISTS env_zonas_municipio_codigo_fkey;
ALTER TABLE public.env_zonas
    ADD CONSTRAINT env_zonas_municipio_codigo_fkey
    FOREIGN KEY (municipio_codigo)
    REFERENCES public.env_municipios_oficiales(codigo);

CREATE TABLE IF NOT EXISTS public.env_opciones_envio (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nombre VARCHAR(100) NOT NULL UNIQUE,
    modalidad VARCHAR(50) NOT NULL,
    tiempo_minimo_dias INTEGER NOT NULL CHECK (tiempo_minimo_dias >= 0),
    activa BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_env_opciones_nombre_sin_distinguir_mayusculas
    ON public.env_opciones_envio(lower(nombre));

CREATE TABLE IF NOT EXISTS public.env_zonas_opciones (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    zona_id UUID NOT NULL REFERENCES public.env_zonas(id),
    opcion_envio_id UUID NOT NULL REFERENCES public.env_opciones_envio(id),
    activa BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (zona_id, opcion_envio_id)
);

CREATE TABLE IF NOT EXISTS public.env_tarifas (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    zona_opcion_id UUID NOT NULL REFERENCES public.env_zonas_opciones(id),
    peso_min_kg NUMERIC(10, 3) NOT NULL CHECK (peso_min_kg >= 0),
    peso_max_kg NUMERIC(10, 3) NOT NULL CHECK (peso_max_kg > peso_min_kg),
    valor NUMERIC(14, 2) NOT NULL CHECK (valor >= 0),
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS public.env_tarifas_historial (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tarifa_id UUID NOT NULL REFERENCES public.env_tarifas(id) ON DELETE RESTRICT,
    usuario_responsable_id UUID NOT NULL,
    accion VARCHAR(20) NOT NULL CHECK (accion IN ('CREADA', 'MODIFICADA')),
    datos_anteriores JSONB,
    datos_nuevos JSONB NOT NULL,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_env_tarifas_historial_tarifa
    ON public.env_tarifas_historial(tarifa_id, creado_en);
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'env_tarifas_rangos_sin_traslape'
          AND conrelid = 'public.env_tarifas'::regclass
    ) THEN
        ALTER TABLE public.env_tarifas
            ADD CONSTRAINT env_tarifas_rangos_sin_traslape
            EXCLUDE USING gist (
                zona_opcion_id WITH =,
                numrange(peso_min_kg, peso_max_kg, '[)') WITH &&
            );
    END IF;
END;
$$;

CREATE TABLE IF NOT EXISTS public.env_transportadoras (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    nombre VARCHAR(120) NOT NULL,
    nit VARCHAR(30) NOT NULL UNIQUE,
    contacto VARCHAR(200) NOT NULL,
    activa BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.env_productos (
    producto_id UUID PRIMARY KEY,
    vendedor_id UUID NOT NULL,
    peso_kg NUMERIC(10, 3) NOT NULL CHECK (peso_kg > 0),
    largo_cm NUMERIC(10, 2) NOT NULL CHECK (largo_cm > 0),
    ancho_cm NUMERIC(10, 2) NOT NULL CHECK (ancho_cm > 0),
    alto_cm NUMERIC(10, 2) NOT NULL CHECK (alto_cm > 0),
    opcion_envio_id UUID NOT NULL REFERENCES public.env_opciones_envio(id),
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_env_productos_vendedor
    ON public.env_productos(vendedor_id);

CREATE TABLE IF NOT EXISTS public.env_direcciones (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    usuario_id UUID NOT NULL,
    nombre_destinatario VARCHAR(200) NOT NULL,
    telefono VARCHAR(30) NOT NULL,
    direccion VARCHAR(500) NOT NULL,
    barrio VARCHAR(100),
    municipio VARCHAR(100) NOT NULL,
    municipio_codigo VARCHAR(5) NOT NULL,
    departamento VARCHAR(100) NOT NULL,
    indicaciones TEXT,
    acepta_tratamiento_datos BOOLEAN NOT NULL CHECK (acepta_tratamiento_datos),
    consentimiento_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    predeterminada BOOLEAN NOT NULL DEFAULT FALSE,
    creado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.env_direcciones
    ADD COLUMN IF NOT EXISTS municipio_codigo VARCHAR(5);
ALTER TABLE public.env_direcciones
    DROP CONSTRAINT IF EXISTS env_direcciones_municipio_codigo_fkey;
ALTER TABLE public.env_direcciones
    ADD CONSTRAINT env_direcciones_municipio_codigo_fkey
    FOREIGN KEY (municipio_codigo)
    REFERENCES public.env_municipios_oficiales(codigo);
CREATE INDEX IF NOT EXISTS idx_env_direcciones_usuario
    ON public.env_direcciones(usuario_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_env_direccion_predeterminada
    ON public.env_direcciones(usuario_id)
    WHERE predeterminada;

ALTER TABLE public.ord_pedidos
    DROP CONSTRAINT IF EXISTS ord_pedidos_transportadora_id_fkey;
ALTER TABLE public.ord_pedidos
    ADD CONSTRAINT ord_pedidos_transportadora_id_fkey
    FOREIGN KEY (transportadora_id) REFERENCES public.env_transportadoras(id);
ALTER TABLE public.ord_pedidos
    DROP CONSTRAINT IF EXISTS ord_pedidos_direccion_id_fkey;
ALTER TABLE public.ord_pedidos
    ADD CONSTRAINT ord_pedidos_direccion_id_fkey
    FOREIGN KEY (direccion_id) REFERENCES public.env_direcciones(id);
ALTER TABLE public.ord_pedidos
    DROP CONSTRAINT IF EXISTS ord_pedidos_zona_envio_id_fkey;
ALTER TABLE public.ord_pedidos
    ADD CONSTRAINT ord_pedidos_zona_envio_id_fkey
    FOREIGN KEY (zona_envio_id) REFERENCES public.env_zonas(id);
ALTER TABLE public.ord_pedidos
    DROP CONSTRAINT IF EXISTS ord_pedidos_destinatario_municipio_codigo_fkey;
ALTER TABLE public.ord_pedidos
    ADD CONSTRAINT ord_pedidos_destinatario_municipio_codigo_fkey
    FOREIGN KEY (destinatario_municipio_codigo)
    REFERENCES public.env_municipios_oficiales(codigo);
ALTER TABLE public.ord_items_pedido
    DROP CONSTRAINT IF EXISTS ord_items_pedido_opcion_envio_id_fkey;
ALTER TABLE public.ord_items_pedido
    ADD CONSTRAINT ord_items_pedido_opcion_envio_id_fkey
    FOREIGN KEY (opcion_envio_id) REFERENCES public.env_opciones_envio(id);

ALTER TABLE public.ord_pedidos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ord_items_pedido ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ord_historial_estados ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.env_zonas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.env_opciones_envio ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.env_zonas_opciones ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.env_tarifas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.env_tarifas_historial ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.env_transportadoras ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.env_productos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.env_direcciones ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.env_municipios_oficiales ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS policy_ord_pedidos ON public.ord_pedidos;
DROP POLICY IF EXISTS policy_ord_items ON public.ord_items_pedido;
DROP POLICY IF EXISTS policy_ord_historial ON public.ord_historial_estados;

CREATE OR REPLACE FUNCTION public.crear_orden_transaccional(
    p_orden JSONB,
    p_items JSONB,
    p_usuario UUID,
    p_observaciones TEXT
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    nueva_orden public.ord_pedidos%ROWTYPE;
BEGIN
    INSERT INTO public.ord_pedidos (
        id, comprador_id, vendedor_id, numero_orden, estado, subtotal,
        impuestos, costo_envio, descuentos, total, moneda, direccion_entrega,
        ciudad_entrega, notas, creado_en, actualizado_en, direccion_origen,
        ciudad_origen, destinatario_nombre, destinatario_telefono,
        destinatario_barrio, destinatario_departamento,
        destinatario_municipio_codigo,
        destinatario_indicaciones, acepta_tratamiento_datos, direccion_id,
        zona_envio_id, detalle_envio
    ) VALUES (
        (p_orden->>'id')::UUID,
        (p_orden->>'comprador_id')::UUID,
        (p_orden->>'vendedor_id')::UUID,
        p_orden->>'numero_orden',
        p_orden->>'estado',
        (p_orden->>'subtotal')::NUMERIC,
        (p_orden->>'impuestos')::NUMERIC,
        (p_orden->>'costo_envio')::NUMERIC,
        (p_orden->>'descuentos')::NUMERIC,
        (p_orden->>'total')::NUMERIC,
        p_orden->>'moneda',
        p_orden->>'direccion_entrega',
        p_orden->>'ciudad_entrega',
        p_orden->>'notas',
        (p_orden->>'creado_en')::TIMESTAMPTZ,
        (p_orden->>'actualizado_en')::TIMESTAMPTZ,
        p_orden->>'direccion_origen',
        p_orden->>'ciudad_origen',
        p_orden->>'destinatario_nombre',
        p_orden->>'destinatario_telefono',
        p_orden->>'destinatario_barrio',
        p_orden->>'destinatario_departamento',
        p_orden->>'destinatario_municipio_codigo',
        p_orden->>'destinatario_indicaciones',
        (p_orden->>'acepta_tratamiento_datos')::BOOLEAN,
        NULLIF(p_orden->>'direccion_id', '')::UUID,
        NULLIF(p_orden->>'zona_envio_id', '')::UUID,
        COALESCE(p_orden->'detalle_envio', '[]'::JSONB)
    )
    RETURNING * INTO nueva_orden;

    INSERT INTO public.ord_items_pedido (
        pedido_id, producto_id, nombre_producto, cantidad, precio_unitario,
        subtotal, peso_kg, opcion_envio_id, tarifa_envio
    )
    SELECT
        x.pedido_id, x.producto_id, x.nombre_producto, x.cantidad,
        x.precio_unitario, x.subtotal, x.peso_kg, x.opcion_envio_id,
        x.tarifa_envio
    FROM jsonb_to_recordset(p_items) AS x(
        pedido_id UUID,
        producto_id UUID,
        nombre_producto VARCHAR(255),
        cantidad INTEGER,
        precio_unitario NUMERIC(14, 2),
        subtotal NUMERIC(14, 2),
        peso_kg NUMERIC(10, 3),
        opcion_envio_id UUID,
        tarifa_envio NUMERIC(14, 2)
    );

    INSERT INTO public.ord_historial_estados (
        pedido_id, estado_anterior, estado_nuevo,
        usuario_responsable_id, observaciones
    ) VALUES (
        nueva_orden.id, NULL, nueva_orden.estado, p_usuario, p_observaciones
    );

    RETURN to_jsonb(nueva_orden);
END;
$$;

CREATE OR REPLACE FUNCTION public.actualizar_estado_orden_transaccional(
    p_orden_id UUID,
    p_estado VARCHAR(40),
    p_usuario UUID,
    p_observaciones TEXT,
    p_transportadora_id UUID,
    p_transportadora_nombre VARCHAR(120),
    p_numero_guia VARCHAR(100),
    p_fecha_estimada_entrega DATE
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    orden_actual public.ord_pedidos%ROWTYPE;
    orden_actualizada public.ord_pedidos%ROWTYPE;
    transicion_permitida BOOLEAN := FALSE;
BEGIN
    SELECT * INTO orden_actual
    FROM public.ord_pedidos
    WHERE id = p_orden_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Orden no encontrada.' USING ERRCODE = 'P0001';
    END IF;

    transicion_permitida := CASE orden_actual.estado
        WHEN 'PENDIENTE_PAGO' THEN p_estado IN ('CONFIRMADA', 'CANCELADA')
        WHEN 'CONFIRMADA' THEN p_estado IN ('EN_PREPARACION', 'CANCELADA')
        WHEN 'EN_PREPARACION' THEN p_estado IN ('LISTA_DESPACHO', 'CANCELADA')
        WHEN 'LISTA_DESPACHO' THEN p_estado IN ('ENVIADA', 'CANCELADA')
        WHEN 'ENVIADA' THEN p_estado = 'ENTREGADA'
        WHEN 'ENTREGADA' THEN p_estado = 'DEVOLUCION_SOLICITADA'
        ELSE FALSE
    END;

    IF NOT transicion_permitida THEN
        RAISE EXCEPTION 'Transición de estado inválida.' USING ERRCODE = 'P0001';
    END IF;

    UPDATE public.ord_pedidos
    SET estado = p_estado,
        actualizado_en = now(),
        transportadora_id = COALESCE(p_transportadora_id, transportadora_id),
        transportadora_nombre = COALESCE(p_transportadora_nombre, transportadora_nombre),
        numero_guia = COALESCE(p_numero_guia, numero_guia),
        fecha_estimada_entrega = COALESCE(p_fecha_estimada_entrega, fecha_estimada_entrega)
    WHERE id = p_orden_id
    RETURNING * INTO orden_actualizada;

    INSERT INTO public.ord_historial_estados (
        pedido_id, estado_anterior, estado_nuevo,
        usuario_responsable_id, observaciones
    ) VALUES (
        p_orden_id, orden_actual.estado, p_estado, p_usuario, p_observaciones
    );

    RETURN to_jsonb(orden_actualizada);
END;
$$;

CREATE OR REPLACE FUNCTION public.actualizar_direccion_orden_transaccional(
    p_orden_id UUID,
    p_direccion JSONB,
    p_envio JSONB,
    p_finanzas JSONB
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    orden_actual public.ord_pedidos%ROWTYPE;
    orden_actualizada public.ord_pedidos%ROWTYPE;
BEGIN
    SELECT * INTO orden_actual
    FROM public.ord_pedidos
    WHERE id = p_orden_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Orden no encontrada.' USING ERRCODE = 'P0001';
    END IF;
    IF orden_actual.estado <> 'PENDIENTE_PAGO' THEN
        RAISE EXCEPTION 'La dirección solo se puede cambiar antes de confirmar el pago.'
            USING ERRCODE = 'P0001';
    END IF;

    UPDATE public.ord_pedidos
    SET direccion_id = NULLIF(p_direccion->>'id', '')::UUID,
        direccion_entrega = p_direccion->>'direccion',
        ciudad_entrega = p_direccion->>'municipio',
        destinatario_nombre = p_direccion->>'nombre_destinatario',
        destinatario_telefono = p_direccion->>'telefono',
        destinatario_barrio = p_direccion->>'barrio',
        destinatario_departamento = p_direccion->>'departamento',
        destinatario_municipio_codigo = p_direccion->>'municipio_codigo',
        destinatario_indicaciones = p_direccion->>'indicaciones',
        acepta_tratamiento_datos = TRUE,
        zona_envio_id = NULLIF(p_envio->>'zona_id', '')::UUID,
        detalle_envio = COALESCE(p_envio->'detalles', '[]'::JSONB),
        costo_envio = (p_finanzas->>'costo_envio')::NUMERIC,
        total = (p_finanzas->>'total')::NUMERIC,
        actualizado_en = now()
    WHERE id = p_orden_id
    RETURNING * INTO orden_actualizada;

    UPDATE public.ord_items_pedido AS item
    SET peso_kg = detalle.peso_kg,
        opcion_envio_id = detalle.opcion_envio_id,
        tarifa_envio = detalle.tarifa_envio
    FROM (
        SELECT DISTINCT ON (producto_id)
            producto_id, peso_kg, opcion_envio_id, tarifa_envio
        FROM jsonb_to_recordset(COALESCE(p_envio->'detalles', '[]'::JSONB)) AS x(
            producto_id UUID,
            peso_kg NUMERIC(10, 3),
            opcion_envio_id UUID,
            tarifa_envio NUMERIC(14, 2)
        )
    ) AS detalle
    WHERE item.pedido_id = p_orden_id
      AND item.producto_id = detalle.producto_id;

    RETURN to_jsonb(orden_actualizada);
END;
$$;

CREATE OR REPLACE FUNCTION public.guardar_tarifa_transaccional(
    p_tarifa_id UUID,
    p_zona_opcion_id UUID,
    p_peso_min_kg NUMERIC(10, 3),
    p_peso_max_kg NUMERIC(10, 3),
    p_valor NUMERIC(14, 2),
    p_usuario UUID
) RETURNS JSONB
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    tarifa_actual public.env_tarifas%ROWTYPE;
    tarifa_guardada public.env_tarifas%ROWTYPE;
    accion VARCHAR(20);
    datos_anteriores JSONB;
BEGIN
    IF p_tarifa_id IS NULL THEN
        accion := 'CREADA';
        INSERT INTO public.env_tarifas (
            zona_opcion_id, peso_min_kg, peso_max_kg, valor
        ) VALUES (
            p_zona_opcion_id, p_peso_min_kg, p_peso_max_kg, p_valor
        )
        RETURNING * INTO tarifa_guardada;
    ELSE
        SELECT * INTO tarifa_actual
        FROM public.env_tarifas
        WHERE id = p_tarifa_id
        FOR UPDATE;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'Tarifa no encontrada.' USING ERRCODE = 'P0001';
        END IF;

        accion := 'MODIFICADA';
        datos_anteriores := jsonb_build_object(
            'zona_opcion_id', tarifa_actual.zona_opcion_id,
            'peso_min_kg', tarifa_actual.peso_min_kg,
            'peso_max_kg', tarifa_actual.peso_max_kg,
            'valor', tarifa_actual.valor
        );
        UPDATE public.env_tarifas
        SET zona_opcion_id = p_zona_opcion_id,
            peso_min_kg = p_peso_min_kg,
            peso_max_kg = p_peso_max_kg,
            valor = p_valor,
            actualizado_en = now()
        WHERE id = p_tarifa_id
        RETURNING * INTO tarifa_guardada;
    END IF;

    INSERT INTO public.env_tarifas_historial (
        tarifa_id, usuario_responsable_id, accion,
        datos_anteriores, datos_nuevos
    ) VALUES (
        tarifa_guardada.id, p_usuario, accion, datos_anteriores,
        jsonb_build_object(
            'zona_opcion_id', tarifa_guardada.zona_opcion_id,
            'peso_min_kg', tarifa_guardada.peso_min_kg,
            'peso_max_kg', tarifa_guardada.peso_max_kg,
            'valor', tarifa_guardada.valor
        )
    );

    RETURN to_jsonb(tarifa_guardada);
END;
$$;

REVOKE ALL ON FUNCTION public.crear_orden_transaccional(JSONB, JSONB, UUID, TEXT)
    FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.actualizar_estado_orden_transaccional(
    UUID, VARCHAR, UUID, TEXT, UUID, VARCHAR, VARCHAR, DATE
) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.actualizar_direccion_orden_transaccional(
    UUID, JSONB, JSONB, JSONB
) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.guardar_tarifa_transaccional(
    UUID, UUID, NUMERIC, NUMERIC, NUMERIC, UUID
) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.crear_orden_transaccional(JSONB, JSONB, UUID, TEXT)
    TO service_role;
GRANT EXECUTE ON FUNCTION public.actualizar_estado_orden_transaccional(
    UUID, VARCHAR, UUID, TEXT, UUID, VARCHAR, VARCHAR, DATE
) TO service_role;
    GRANT EXECUTE ON FUNCTION public.actualizar_direccion_orden_transaccional(
        UUID, JSONB, JSONB, JSONB
    ) TO service_role;
    GRANT EXECUTE ON FUNCTION public.guardar_tarifa_transaccional(
        UUID, UUID, NUMERIC, NUMERIC, NUMERIC, UUID
    ) TO service_role;
