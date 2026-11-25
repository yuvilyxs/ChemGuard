import unittest
from unittest.mock import patch

from core import safety_service
from core.research_library import (
    MAX_RECENT_COMPOUNDS,
    MAX_SEARCH_HISTORY,
    add_recent_compound,
    add_search_entry,
)
from core.report_service import _report_rows, create_safety_report


def _profile(**overrides):
    profile = {
        "available": True,
        "pairwise_evidence": [],
        "storage_information": [],
        "supplier_sds_references": [],
    }
    profile.update(overrides)
    return profile


class StoragePairTests(unittest.TestCase):
    def setUp(self):
        self.chemical_a = {"cid": 702, "iupac_name": "ethanol"}
        self.chemical_b = {"cid": 180, "iupac_name": "propan-2-one"}

    def test_missing_pair_evidence_requires_sds_review(self):
        with patch(
            "core.safety_service.get_compound_safety_profile",
            side_effect=[_profile(), _profile()],
        ):
            result = safety_service.analyze_storage_pair(
                self.chemical_a, self.chemical_b
            )
        self.assertEqual(result["status"], "SDS Review Required")

    def test_source_verified_pair_evidence_is_a_segregation_warning(self):
        evidence = {
            "related_cid": 180,
            "source_verified": True,
            "source_url": "https://example.invalid/source",
        }
        with patch(
            "core.safety_service.get_compound_safety_profile",
            side_effect=[_profile(pairwise_evidence=[evidence]), _profile()],
        ):
            result = safety_service.analyze_storage_pair(
                self.chemical_a, self.chemical_b
            )
        self.assertEqual(result["status"], "Verified Segregation Warning")
        self.assertEqual(result["evidence"], [evidence])

    def test_no_listed_conflict_requires_both_supplier_sds_citations(self):
        sds_statement = {
            "value_text": "No dangerous reactions known.",
            "reference_citations": ["Supplier Safety Data Sheet"],
        }
        with patch(
            "core.safety_service.get_compound_safety_profile",
            side_effect=[
                _profile(storage_information=[sds_statement]),
                _profile(storage_information=[sds_statement]),
            ],
        ):
            result = safety_service.analyze_storage_pair(
                self.chemical_a, self.chemical_b
            )
        self.assertEqual(result["status"], "No Listed Conflict Identified")


class SafetyProfileTests(unittest.TestCase):
    def test_uncoded_classification_note_is_not_an_h_statement(self):
        payload = {
            "Record": {
                "Reference": [],
                "Section": [
                    {
                        "TOCHeading": "Safety and Hazards",
                        "Section": [
                            {
                                "TOCHeading": "GHS Classification",
                                "Information": [
                                    {
                                        "Name": "Hazard Statement",
                                        "Value": {
                                            "StringWithMarkup": [
                                                {"String": "Not Classified"}
                                            ]
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        }
        with patch(
            "core.safety_service.get_compound_safety_record",
            return_value=payload,
        ):
            profile = safety_service.get_compound_safety_profile(702)
        group = profile["ghs_classifications"][0]
        self.assertEqual(group["hazard_statements"], [])
        self.assertIn("Not Classified", group["notes"])


class ReportTests(unittest.TestCase):
    def test_long_report_values_are_split_and_pdf_is_generated(self):
        compound = {
            "cid": 702,
            "iupac_name": "ethanol",
            "properties": {},
            "source_urls": {},
        }
        long_text = "A sourced safety annotation with detail. " * 120
        long_source = "Supplier SDS citation. " * 100
        profile = _profile(
            storage_information=[
                {
                    "heading": "Storage information",
                    "value_text": long_text,
                    "source_name": "PubChem",
                    "reference_citations": [long_source],
                }
            ]
        )
        rows = _report_rows(compound, profile)
        value_rows = [
            row for row in rows if row["Section"] == "Storage information"
        ]
        self.assertGreater(len(value_rows), 1)
        self.assertTrue(all(len(row["Value"]) <= 800 for row in value_rows))
        self.assertTrue(all(len(row["Source"]) <= 500 for row in value_rows))
        self.assertEqual(
            "".join(row["Value"] for row in value_rows).replace(" ", ""),
            long_text.replace(" ", ""),
        )
        self.assertEqual(
            "".join(row["Source"] for row in value_rows).replace(" ", ""),
            ("PubChem; " + long_source).replace(" ", ""),
        )

        report = create_safety_report(compound, profile)
        self.assertTrue(report["pdf"].startswith(b"%PDF"))
        self.assertIn("Chemical Safety Report", report["markdown"])
        self.assertIn("Section,Field,Value,Source", report["csv"])


class ResearchLibraryTests(unittest.TestCase):
    def test_search_history_keeps_newest_entries_and_caps_length(self):
        history = []
        for index in range(MAX_SEARCH_HISTORY + 2):
            history = add_search_entry(history, f"chemical {index}", [])

        self.assertEqual(len(history), MAX_SEARCH_HISTORY)
        self.assertEqual(history[0]["query"], f"chemical {MAX_SEARCH_HISTORY + 1}")
        self.assertEqual(history[-1]["query"], "chemical 2")

    def test_recent_compounds_deduplicate_and_keep_most_recent_first(self):
        recent = []
        for cid in range(1, MAX_RECENT_COMPOUNDS + 2):
            recent = add_recent_compound(
                recent, {"cid": cid, "iupac_name": f"compound {cid}"}, "search"
            )
        recent = add_recent_compound(
            recent, {"cid": 4, "iupac_name": "updated compound 4"}, "again"
        )

        cids = [item["compound"]["cid"] for item in recent]
        self.assertEqual(len(cids), MAX_RECENT_COMPOUNDS)
        self.assertEqual(cids[0], 4)
        self.assertEqual(cids.count(4), 1)
        self.assertEqual(recent[0]["query"], "again")

    def test_recent_compounds_reject_invalid_identifiers(self):
        with self.assertRaises(ValueError):
            add_recent_compound([], {"cid": 0}, "search")


if __name__ == "__main__":
    unittest.main()
