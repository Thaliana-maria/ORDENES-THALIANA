import os
import unittest
from decimal import Decimal

from fastapi import HTTPException

from auth import verificar_token
from domain import calcular_finanzas, validar_transicion


class OrderRulesTests(unittest.TestCase):
    def test_shipping_is_fifteen_thousand_at_exact_threshold(self):
        result = calcular_finanzas(
            [{"cantidad": 1, "precio_unitario": Decimal("200000")}],
            Decimal("15000"),
            Decimal("0"),
        )
        self.assertEqual(result["subtotal"], Decimal("200000.00"))
        self.assertEqual(result["impuestos"], Decimal("38000.00"))
        self.assertEqual(result["costo_envio"], Decimal("15000"))
        self.assertEqual(result["total"], Decimal("253000.00"))

    def test_shipping_is_free_only_above_threshold(self):
        result = calcular_finanzas(
            [{"cantidad": 1, "precio_unitario": Decimal("200000.01")}],
            Decimal("15000"),
            Decimal("0"),
        )
        self.assertEqual(result["costo_envio"], Decimal("0.00"))
        self.assertEqual(result["total"], Decimal("238000.01"))

    def test_discount_is_subtracted_and_cannot_exceed_order(self):
        result = calcular_finanzas(
            [{"cantidad": 2, "precio_unitario": Decimal("10000")}],
            Decimal("15000"),
            Decimal("5000"),
        )
        self.assertEqual(result["total"], Decimal("33800.00"))
        with self.assertRaises(HTTPException) as error:
            calcular_finanzas(
                [{"cantidad": 1, "precio_unitario": Decimal("100")}],
                Decimal("15000"),
                Decimal("20000"),
            )
        self.assertEqual(error.exception.status_code, 422)

    def test_state_transition_rejects_skipping_and_terminal_changes(self):
        validar_transicion("PENDIENTE_PAGO", "CONFIRMADA")
        with self.assertRaises(HTTPException) as error:
            validar_transicion("PENDIENTE_PAGO", "ENVIADA")
        self.assertEqual(error.exception.status_code, 409)
        with self.assertRaises(HTTPException):
            validar_transicion("ENTREGADA", "CANCELADA")

    def test_unsigned_dev_token_is_disabled_by_default(self):
        os.environ.pop("ALLOW_DEV_TOKEN", None)
        with self.assertRaises(HTTPException) as error:
            verificar_token("Bearer " + "dev-token")
        self.assertEqual(error.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
