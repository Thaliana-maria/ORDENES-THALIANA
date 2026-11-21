import unittest

from pydantic import ValidationError

from main import app
from schemas import TarifaInput, ZonaInput


class ApiContractTests(unittest.TestCase):
    def test_required_order_and_shipping_paths_are_documented(self):
        paths = app.openapi()["paths"]
        required = {
            "/api/ordenes",
            "/api/ordenes/{orden_id}/estado",
            "/api/envios/municipios",
            "/api/envios/zonas",
            "/api/envios/opciones",
            "/api/envios/tarifas",
            "/api/envios/tarifas/{tarifa_id}/historial",
            "/api/envios/transportadoras",
            "/api/envios/cotizaciones",
            "/api/direcciones",
            "/api/productos/{producto_id}/envio",
        }
        self.assertEqual(required - paths.keys(), set())

    def test_swagger_documentation_uses_expected_service_title(self):
        schema = app.openapi()
        self.assertEqual(schema["info"]["title"], "Marketplace USC | API de Órdenes y Envíos")

    def test_tariffs_reject_fractional_pesos_and_invalid_weight_ranges(self):
        TarifaInput(peso_min_kg="0", peso_max_kg="1", valor="15000")
        with self.assertRaises(ValidationError):
            TarifaInput(peso_min_kg="0", peso_max_kg="1", valor="15000.50")
        with self.assertRaises(ValidationError):
            TarifaInput(peso_min_kg="2", peso_max_kg="1", valor="15000")

    def test_zones_require_an_official_divipola_code(self):
        ZonaInput(
            nombre="Cali",
            departamento="VALLE DEL CAUCA",
            municipio="CALI",
            municipio_codigo="76001",
        )
        with self.assertRaises(ValidationError):
            ZonaInput(
                nombre="Cali",
                departamento="VALLE DEL CAUCA",
                municipio="CALI",
                municipio_codigo="7601",
            )

    def test_clients_cannot_supply_an_unverified_discount(self):
        order_schema = app.openapi()["components"]["schemas"]["CrearOrdenInput"]
        self.assertNotIn("descuentos", order_schema["properties"])


if __name__ == "__main__":
    unittest.main()
