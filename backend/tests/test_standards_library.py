from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from esg_encoding.services.standards_library_service import (
    StandardsDataError,
    StandardsScopeNotFound,
    get_standard_metrics,
    get_standards_catalog,
)


class StandardsLibraryBundledDataTests(unittest.TestCase):
    def test_catalog_reports_the_real_bundled_scope_counts(self) -> None:
        catalog = get_standards_catalog()
        frameworks = {
            framework["id"]: framework for framework in catalog["frameworks"]
        }

        self.assertEqual(
            {key: frameworks[key]["scope_count"] for key in frameworks},
            {
                "sasb": 77,
                "gri": 98,
                "cdp": 8,
                "aasb": 0,
            },
        )
        self.assertNotIn("tcfd", frameworks)
        self.assertEqual(
            [(group["id"], len(group["scopes"])) for group in frameworks["gri"]["groups"]],
            [
                ("agriculture_aquaculture_and_fishing_sectors", 27),
                ("coal_sector", 22),
                ("mining_sector", 25),
                ("oil_and_gas_sector", 24),
            ],
        )
        self.assertTrue(frameworks["sasb"]["available"])
        self.assertFalse(frameworks["aasb"]["available"])

    def test_sasb_catalog_preserves_all_industry_groups_and_unique_industries(self) -> None:
        catalog = get_standards_catalog()
        sasb = next(
            framework for framework in catalog["frameworks"] if framework["id"] == "sasb"
        )

        expected_groups = {
            "consumer_goods": ("Consumer Goods", 7),
            "extractives_minerals_processing": (
                "Extractives & Minerals Processing",
                8,
            ),
            "financials": ("Financials", 7),
            "food_beverage": ("Food & Beverage", 8),
            "health_care": ("Health Care", 6),
            "infrastructure": ("Infrastructure", 8),
            "renewable_resources_alternative_energy": (
                "Renewable Resources & Alternative Energy",
                6,
            ),
            "resource_transformation": ("Resource Transformation", 5),
            "services": ("Services", 7),
            "technology_communications": ("Technology & Communications", 6),
            "transportation": ("Transportation", 9),
        }
        actual_groups = {
            group["id"]: (group["label"], len(group["scopes"]))
            for group in sasb["groups"]
        }
        leaf_ids = [
            scope["id"]
            for group in sasb["groups"]
            for scope in group["scopes"]
        ]

        self.assertEqual(actual_groups, expected_groups)
        self.assertEqual(len(sasb["groups"]), 11)
        self.assertEqual(len(leaf_ids), 77)
        self.assertEqual(len(set(leaf_ids)), 77)
        self.assertEqual(sasb["scope_count"], 77)

        technology = next(
            group
            for group in sasb["groups"]
            if group["id"] == "technology_communications"
        )
        self.assertIn(
            {"id": "Hardware", "label": "Hardware"},
            technology["scopes"],
        )

    def test_real_sasb_scope_resolves_its_group_when_group_id_is_omitted(self) -> None:
        result = get_standard_metrics("SASB", "Hardware")

        self.assertEqual(result["framework"]["id"], "sasb")
        self.assertEqual(
            result["group"],
            {
                "id": "technology_communications",
                "label": "Technology & Communications",
            },
        )
        self.assertEqual(result["scope"], {"id": "Hardware", "label": "Hardware"})
        self.assertEqual(result["total_metrics"], 24)
        self.assertEqual(
            result["metrics"][0]["id"],
            "sasb:technology_communications:Hardware:1",
        )
        self.assertEqual(result["metrics"][0]["code"], "TC-HW-230a.1")
        self.assertEqual(
            set(result["metrics"][0]),
            {
                "id",
                "code",
                "name",
                "topic",
                "category",
                "type",
                "unit",
                "standard",
                "definition",
                "simple_definition",
            },
        )

    def test_real_sasb_scope_accepts_its_explicit_group(self) -> None:
        result = get_standard_metrics(
            "sasb",
            "Hardware",
            group_id="technology_communications",
        )

        self.assertEqual(
            result["group"],
            {
                "id": "technology_communications",
                "label": "Technology & Communications",
            },
        )
        self.assertEqual(result["scope"]["id"], "Hardware")
        self.assertEqual(result["total_metrics"], 24)

    def test_real_sasb_scope_rejects_an_explicit_wrong_group(self) -> None:
        with self.assertRaises(StandardsScopeNotFound):
            get_standard_metrics(
                "sasb",
                "Hardware",
                group_id="consumer_goods",
            )

    def test_aasb_is_listed_but_local_metrics_are_not_fabricated(self) -> None:
        catalog = get_standards_catalog()
        aasb = next(
            framework for framework in catalog["frameworks"] if framework["id"] == "aasb"
        )

        self.assertEqual(aasb["groups"], [])
        self.assertEqual(aasb["scope_count"], 0)
        self.assertFalse(aasb["available"])
        with self.assertRaisesRegex(
            StandardsScopeNotFound, "Local metric data is not available for AASB"
        ):
            get_standard_metrics("aasb", "anything")


class StandardsLibraryValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.data_root = Path(self._temporary_directory.name)

    def tearDown(self) -> None:
        self._temporary_directory.cleanup()

    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def _write_sasb_scope(self, rows: object) -> None:
        directory = self.data_root / "sasb_metrics"
        self._write_json(
            directory / "manifest.json",
            {"semi_industry_to_file": {"Example Industry": "Example.json"}},
        )
        self._write_json(directory / "Example.json", rows)

    def test_legacy_sasb_manifest_without_groups_uses_the_single_group_fallback(self) -> None:
        self._write_sasb_scope([{"Metric": "Legacy metric", "Code": "LEGACY-1"}])

        catalog = get_standards_catalog(self.data_root)
        sasb = next(
            framework for framework in catalog["frameworks"] if framework["id"] == "sasb"
        )
        result = get_standard_metrics(
            "sasb",
            "Example Industry",
            data_root=self.data_root,
        )

        self.assertEqual(
            sasb["groups"],
            [
                {
                    "id": "industries",
                    "label": "Industries",
                    "scopes": [
                        {"id": "Example Industry", "label": "Example Industry"}
                    ],
                }
            ],
        )
        self.assertEqual(result["group"], {"id": "industries", "label": "Industries"})
        self.assertEqual(result["total_metrics"], 1)
        self.assertEqual(result["metrics"][0]["id"], "sasb:industries:Example Industry:1")

    def test_metric_rows_are_normalized_across_supported_key_styles(self) -> None:
        self._write_sasb_scope(
            [
                {
                    "Metric": "  Uppercase metric  ",
                    "Code": float("nan"),
                    "Topic": " Topic A ",
                    "Category": " Quantitative ",
                    "Type": " Disclosure ",
                    "Unit": " % ",
                    "Standard": " Standard A ",
                    "Definition": " Full definition ",
                    "Simple Definition": " Simple definition ",
                },
                {
                    "metric": "lowercase metric",
                    "code": " code-2 ",
                    "topic": "topic b",
                    "category": "qualitative",
                    "type": "activity metric",
                    "unit": "number",
                    "standard": "standard b",
                    "definition": "definition b",
                    "simple_definition": "simple b",
                },
                {},
            ]
        )

        result = get_standard_metrics(
            " sasb ",
            "Example Industry",
            data_root=self.data_root,
        )

        self.assertEqual(result["total_metrics"], 3)
        self.assertEqual(
            result["metrics"][0],
            {
                "id": "sasb:industries:Example Industry:1",
                "code": None,
                "name": "Uppercase metric",
                "topic": "Topic A",
                "category": "Quantitative",
                "type": "Disclosure",
                "unit": "%",
                "standard": "Standard A",
                "definition": "Full definition",
                "simple_definition": "Simple definition",
            },
        )
        self.assertEqual(result["metrics"][1]["code"], "code-2")
        self.assertEqual(result["metrics"][1]["name"], "lowercase metric")
        self.assertEqual(result["metrics"][2]["name"], "Metric 3")
        self.assertIsNone(result["metrics"][2]["definition"])

    def test_manifest_path_traversal_is_rejected_even_when_target_exists(self) -> None:
        directory = self.data_root / "sasb_metrics"
        self._write_json(self.data_root / "outside.json", [])
        self._write_json(
            directory / "manifest.json",
            {"semi_industry_to_file": {"Unsafe": "../outside.json"}},
        )

        with self.assertRaisesRegex(StandardsDataError, "unsafe file reference"):
            get_standards_catalog(self.data_root)

    def test_malformed_manifest_json_has_a_controlled_data_error(self) -> None:
        path = self.data_root / "sasb_metrics" / "manifest.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"semi_industry_to_file":', encoding="utf-8")

        with self.assertRaisesRegex(StandardsDataError, "manifest.json"):
            get_standards_catalog(self.data_root)

    def test_malformed_metric_json_has_a_controlled_data_error(self) -> None:
        directory = self.data_root / "sasb_metrics"
        self._write_json(
            directory / "manifest.json",
            {"semi_industry_to_file": {"Example Industry": "Example.json"}},
        )
        (directory / "Example.json").write_text("[not valid json", encoding="utf-8")

        with self.assertRaisesRegex(StandardsDataError, "Example.json"):
            get_standard_metrics(
                "sasb",
                "Example Industry",
                data_root=self.data_root,
            )

    def test_metric_file_must_be_an_array_of_objects(self) -> None:
        for payload in ({"Metric": "wrong container"}, ["wrong row"]):
            with self.subTest(payload=payload):
                self._write_sasb_scope(payload)
                with self.assertRaises(StandardsDataError):
                    get_standard_metrics(
                        "sasb",
                        "Example Industry",
                        data_root=self.data_root,
                    )


if __name__ == "__main__":
    unittest.main()
