import json
import tempfile
import unittest
from pathlib import Path

from wb_assistant.ozon_migration import (
    OzonSeoAdapter,
    approve_plan,
    build_migration_plan,
    extract_wb_products,
    flatten_ozon_categories,
    map_ozon_attributes,
    suggest_ozon_categories,
    verify_approval,
)
from wb_assistant.ozon_schema import OzonOpenApi


def _schema(path: Path) -> OzonOpenApi:
    spec = {
        "openapi": "3.0.0",
        "info": {"title": "Ozon test", "version": "1"},
        "servers": [{"url": "//api-seller.ozon.ru"}],
        "paths": {
            "/v3/product/import": {
                "post": {
                    "operationId": "ProductAPI_ImportProductsV3",
                    "summary": "Создать или обновить товар",
                    "tags": ["ProductAPI"],
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["items"],
                                    "properties": {
                                        "items": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "required": [
                                                    "description_category_id",
                                                    "type_id",
                                                    "offer_id",
                                                    "price",
                                                ],
                                                "properties": {
                                                    "description_category_id": {
                                                        "type": "integer"
                                                    },
                                                    "type_id": {"type": "integer"},
                                                    "offer_id": {"type": "string"},
                                                    "price": {"type": "string"},
                                                },
                                            },
                                        }
                                    },
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
        "components": {"schemas": {}},
    }
    bundle = {"specifications": [{"format": "json", "spec": spec}]}
    path.write_text(json.dumps(bundle), encoding="utf-8")
    return OzonOpenApi(path)


class OzonMigrationTests(unittest.TestCase):
    def test_false_boolean_override_is_preserved(self) -> None:
        attributes, report, errors, warnings = map_ozon_attributes(
            {
                "identity": {},
                "content": {"attributes": []},
                "compliance": {},
            },
            {
                "attributes": [
                    {
                        "id": 23536,
                        "name": "Нужен код маркировки",
                        "type": "Boolean",
                        "is_required": True,
                        "dictionary_id": 0,
                        "max_value_count": 1,
                    }
                ],
                "dictionaries": {},
            },
            {"name": "Тест", "description": "Тест"},
            {"aliases": {}},
            {"23536": False},
        )
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertEqual(attributes[0]["values"], [{"value": "false"}])
        self.assertEqual(report[0]["source_name"], "manual_override")

    def test_extracts_full_wb_card(self) -> None:
        source = {
            "cards": [
                {
                    "nmID": 123,
                    "imtID": 456,
                    "vendorCode": "TEST 1",
                    "title": "Тестовый товар",
                    "description": "Описание",
                    "brand": "Brand",
                    "needKiz": False,
                    "kizMarked": False,
                    "subjectName": "Массажеры",
                    "dimensions": {
                        "length": 20,
                        "width": 10,
                        "height": 5,
                        "weightBrutto": 0.7,
                    },
                    "characteristics": [
                        {"id": 1, "name": "Материал", "value": ["дерево"]}
                    ],
                    "photos": [{"big": "https://example.test/1.webp"}],
                    "sizes": [
                        {
                            "skus": ["200000000001"],
                            "currentPrice": 1200,
                        }
                    ],
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "wb.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            products = extract_wb_products(source, source_path=path)
        product = products[0]
        self.assertEqual(product["source"]["external_id"], 123)
        self.assertEqual(product["identity"]["offer_id"], "TEST-1")
        self.assertEqual(product["commercial"]["price"], 1200)
        self.assertEqual(product["physical"]["weight"], 0.7)
        self.assertEqual(len(product["media"]), 1)
        self.assertIs(product["compliance"]["need_kiz"], False)

    def test_category_tree_and_suggestions(self) -> None:
        tree = {
            "result": [
                {
                    "category_name": "Спорт",
                    "children": [
                        {
                            "description_category_id": 10,
                            "category_name": "Тренажеры",
                            "type_id": 20,
                            "type_name": "Тренажер для спины",
                            "disabled": False,
                        }
                    ],
                }
            ]
        }
        categories = flatten_ozon_categories(tree)
        product = {
            "content": {
                "source_category": "Тренажеры реабилитационные",
                "name": "Тренажер для спины",
            }
        }
        suggestions = suggest_ozon_categories(product, categories)
        self.assertEqual(suggestions[0]["description_category_id"], 10)
        self.assertGreater(suggestions[0]["score"], 0)

    def test_category_id_is_inherited_by_type_children(self) -> None:
        tree = {
            "result": [
                {
                    "category_name": "Хобби",
                    "children": [
                        {
                            "description_category_id": 30,
                            "category_name": "Рукоделие",
                            "children": [
                                {
                                    "type_id": 40,
                                    "type_name": "Набор для скрапбукинга",
                                    "children": [],
                                    "disabled": False,
                                }
                            ],
                            "disabled": False,
                        }
                    ],
                }
            ]
        }
        categories = flatten_ozon_categories(tree)
        self.assertEqual(len(categories), 1)
        self.assertEqual(categories[0]["description_category_id"], 30)
        self.assertEqual(categories[0]["type_id"], 40)
        self.assertEqual(categories[0]["category_name"], "Рукоделие")

    def test_seo_removes_marketplace_and_promotional_claims(self) -> None:
        policy = {
            "title_max_length": 80,
            "description_max_length": 500,
            "keyword_limit": 5,
            "forbidden_phrases": ["wildberries", "хит продаж", "скидка"],
            "drop_sentences_containing": ["закажите"],
        }
        product = {
            "identity": {"brand": "АРТВУД"},
            "content": {
                "name": "Хит продаж Wildberries Горка для спины",
                "description": "Скидка. Закажите лучший набор. https://example.test",
                "attributes": [
                    {"name": "Материал", "values": ["дерево"]}
                ],
            },
        }
        seo = OzonSeoAdapter(policy).adapt(product, "Тренажер")
        self.assertNotIn("wildberries", seo["name"].lower())
        self.assertNotIn("скидка", seo["description"].lower())
        self.assertNotIn("закажите", seo["description"].lower())
        self.assertIn("Материал", seo["description"])
        self.assertFalse(seo["report"]["invented_claims"])

    def test_seo_does_not_prefix_no_brand_sentinel(self) -> None:
        product = {
            "identity": {"brand": "Нет бренда"},
            "content": {
                "name": "Печатный скетчбук для рисования",
                "description": "Бумажное изделие для эскизов.",
                "attributes": [],
            },
        }
        seo = OzonSeoAdapter({}).adapt(product, "Скетчбук")
        self.assertEqual(seo["name"], "Печатный скетчбук для рисования")

    def test_build_approve_and_verify_plan(self) -> None:
        product = {
            "schema_version": 1,
            "product_uid": "wb:123",
            "source": {"external_id": 123},
            "identity": {
                "offer_id": "TEST-1",
                "vendor_code": "TEST 1",
                "barcodes": ["200000000001"],
                "brand": "Brand",
            },
            "content": {
                "name": "Массажер для спины",
                "description": "Описание товара",
                "source_category": "Массажеры",
                "attributes": [{"name": "Материал", "values": ["дерево"]}],
            },
            "physical": {
                "length": 20,
                "width": 10,
                "height": 5,
                "weight": 0.7,
                "dimension_unit": "cm",
                "weight_unit": "kg",
            },
            "media": [
                {
                    "kind": "image",
                    "position": 0,
                    "source_url": "https://wb.test/1.webp",
                }
            ],
            "commercial": {"currency": "RUB", "price": 1200, "vat": "0"},
            "compliance": {"manual_review": False},
        }
        attributes = {
            "attributes": [
                {
                    "id": 1,
                    "name": "Бренд",
                    "is_required": True,
                    "dictionary_id": 1,
                    "max_value_count": 1,
                },
                {
                    "id": 2,
                    "name": "Аннотация",
                    "is_required": True,
                    "dictionary_id": 0,
                    "max_value_count": 1,
                },
            ],
            "dictionaries": {
                "1": [{"id": 99, "value": "Brand"}],
            },
        }
        media_map = {
            "items": [
                {
                    "wb_nm_id": 123,
                    "source_url": "https://wb.test/1.webp",
                    "ozon_public_url": "https://cdn.test/123/1.jpg",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            schema = _schema(root / "schema.json")
            result = build_migration_plan(
                product=product,
                category_id=10,
                type_id=20,
                type_name="Массажер",
                attribute_snapshot=attributes,
                schema=schema,
                seo_policy={
                    "title_max_length": 200,
                    "description_max_length": 6000,
                    "keyword_limit": 12,
                    "forbidden_phrases": [],
                },
                alias_config={
                    "aliases": {
                        "бренд": ["бренд"],
                        "аннотация": ["описание"],
                    }
                },
                media_map=media_map,
            )
            self.assertEqual(result.plan["items"][0]["validation_status"], "passed")
            plan_path = root / "migration-plan.json"
            plan_path.write_bytes(
                json.dumps(
                    result.plan,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
                + b"\n"
            )
            approval_path = approve_plan(
                plan_path, result.approval_confirmation
            )
            plan, upload_confirmation = verify_approval(
                plan_path, approval_path
            )
            self.assertEqual(plan["plan_id"], result.plan["plan_id"])
            self.assertEqual(upload_confirmation, result.upload_confirmation)


if __name__ == "__main__":
    unittest.main()
