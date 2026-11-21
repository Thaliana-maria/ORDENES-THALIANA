import unittest
from unittest.mock import patch

import integrations


class InventoryIntegrationTests(unittest.TestCase):
    @patch("integrations.httpx.post")
    def test_reservation_uses_confirmed_reference_and_items_contract(self, post):
        post.return_value.is_success = True

        integrations.reservar_inventario({
            "numero_orden": "USC-20261119-TEST",
            "items": [
                {"producto_id": "00000000-0000-0000-0000-000000000001", "cantidad": 2}
            ],
        })

        post.assert_called_once()
        args, kwargs = post.call_args
        self.assertIn("/api/inventario/reservar", args[0])
        self.assertEqual(kwargs["json"], {
            "referencia_id": "USC-20261119-TEST",
            "items": [
                {"producto_id": "00000000-0000-0000-0000-000000000001", "cantidad": 2}
            ],
        })

    @patch("integrations.httpx.post")
    def test_confirmation_calls_reference_without_body(self, post):
        post.return_value.is_success = True

        integrations.confirmar_inventario("USC-20261119-TEST")

        post.assert_called_once()
        args, kwargs = post.call_args
        self.assertIn("/api/inventario/confirmar/USC-20261119-TEST", args[0])
        self.assertNotIn("json", kwargs)


if __name__ == "__main__":
    unittest.main()
