import unittest

from wb_assistant.ozon_api import role_methods


class OzonApiTests(unittest.TestCase):
    def test_role_methods_are_combined_without_duplicates(self) -> None:
        response = {
            "roles": [
                {"name": "Read", "methods": ["/v1/roles", "/v3/product/list"]},
                {"name": "Write", "methods": ["/v3/product/list", "/v3/product/import"]},
            ]
        }
        self.assertEqual(
            role_methods(response),
            {"/v1/roles", "/v3/product/list", "/v3/product/import"},
        )


if __name__ == "__main__":
    unittest.main()
