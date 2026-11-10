"""Offline Stage 4A tests. The fake client exposes SELECT methods only."""

from copy import deepcopy
import csv
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.services import flexible_candidates as fc
from backend.services.planning_eligibility import validate_candidate_evidence
from backend.scripts import preview_degree_audit as audit


YEAR = "2026-2027"
HUM, SOCIAL, FINE = "Core II Humanities", "Core II Social Sciences", "Core II Fine Arts"
STAMP = "2026-10-14T18:03:02+00:00"
ROOT = Path(__file__).resolve().parents[2]
REAL_SOURCE_HASH = fc._SOURCE_SHA256


def csv_bytes(rows):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=["course_code", "attribute_name", "source_url",
                                               "catalog_year", "catalog_level", "observed_at_utc"])
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode()


def fixture():
    tables = {"catalogyear": [{"catalog_year_id": 7, "catalog_label": YEAR}],
              "attribute": [{"attribute_id": i, "attribute_name": n}
                            for i, n in enumerate([HUM, SOCIAL, FINE], 1)],
              "course": [], "courseattribute": []}
    source = []
    specs = [
        ("PHL 101", 3, 3, True, [HUM]),
        ("HST 101", 3, 3, True, [HUM, SOCIAL]),
        ("ART 112", 3, 3, True, [FINE]),
        ("ART 200", 1, 1, True, [FINE]),
        ("ART 210", 1, 4, True, [FINE]),
        ("ART 220", 3, 6, True, [FINE]),
        ("HST 200", 3, 3, False, [HUM]),
    ]
    ids = {a["attribute_name"]: a["attribute_id"] for a in tables["attribute"]}
    for i, (code, low, high, active, names) in enumerate(specs, 1):
        url = "https://catalog.marshall.edu/undergraduate/courses-az/" + code.split()[0].lower() + "/"
        tables["course"].append({"course_id": i, "course_code": code, "course_title": "Fixture " + code,
                                 "min_credit_hours": low, "max_credit_hours": high,
                                 "active_status": active, "source_url": url})
        for name in names:
            tables["courseattribute"].append({"course_id": i, "attribute_id": ids[name],
                "source_url": url, "observed_at": STAMP, "catalog_year_id": 7})
            source.append({"course_code": code, "attribute_name": name, "source_url": url,
                           "catalog_year": YEAR, "catalog_level": "undergraduate", "observed_at_utc": STAMP})
    return tables, source


class SelectQuery:
    def __init__(self, client, table):
        self.client, self.table_name = client, table
        self.equals, self.within, self.orders = {}, {}, []

    def select(self, columns, *, count):
        if count != "exact":
            raise AssertionError("Exact count required")
        self.columns = columns.split(",")
        return self

    def eq(self, key, value):
        self.equals[key] = value
        return self

    def in_(self, key, values):
        if not values:
            raise AssertionError("Empty IN filter")
        self.within[key] = list(values)
        return self

    def order(self, key):
        self.orders.append(key)
        return self

    def range(self, start, end):
        self.start, self.end = start, end
        return self

    def execute(self):
        self.client.calls.append(self)
        rows = [r for r in self.client.tables[self.table_name]
                if all(r[k] == v for k, v in self.equals.items())
                and all(r[k] in v for k, v in self.within.items())]
        if self.client.visible:
            rows = [r for r in rows if self.client.visible(self.table_name, r)]
        rows = sorted(rows, key=lambda r: tuple(r[k] for k in self.orders))
        limit = min(self.end - self.start + 1, self.client.cap)
        response = SimpleNamespace(count=len(rows), data=deepcopy([
            {k: r[k] for k in self.columns} for r in rows[self.start:self.start + limit]
        ]))
        return self.client.hook(self, response) if self.client.hook else response


class SelectClient:
    def __init__(self, tables, *, cap=1000, visible=None, hook=None):
        self.tables, self.cap, self.visible, self.hook = deepcopy(tables), cap, visible, hook
        self.calls = []

    def table(self, table):
        if table not in ("catalogyear", "attribute", "courseattribute", "course"):
            raise AssertionError("Unexpected table")
        return SelectQuery(self, table)


def requirement(rule_id="humanities", name="Core II Humanities", category=HUM, *, known=0, remaining=3):
    return {"rule_id": rule_id, "name": name, "kind": "CREDIT_REQUIREMENT_DRAFT",
            "source_status": "NEEDS_REVIEW", "candidate_progress_status": "CANDIDATE_NOT_MET",
            "planning_disposition": "CANDIDATE_COURSEWORK_REMAINING", "additional_coursework_indicated": True,
            "remaining_policy_review_flags": [],
            "attribute_filter": {"attribute_names": [category], "match_mode": "ANY",
                "catalog_year_classification_lookup_applied": True, "catalog_year": YEAR,
                "candidate_course_codes_entered": [], "eligible_catalog_course_options_included": False},
            "credit_progress": {"minimum_credits_interpreted": known + remaining,
                "candidate_known_credit_subtotal": known, "candidate_courses_without_known_credits": [],
                "additional_credits_indicated_candidate": remaining, "earned_credits_transcript_verified": False}}


def handoff(*requirements, completed=None):
    return {"schema_version": "2.0.0", "degree": {"catalog_year": YEAR},
            "completed_course_codes_entered": completed or [],
            "requirements": list(requirements) or [requirement()]}


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.tables, self.source_rows = fixture()
        self.source = csv_bytes(self.source_rows)
        pin = patch.object(fc, "_SOURCE_SHA256", hashlib.sha256(self.source).hexdigest())
        pin.start()
        self.addCleanup(pin.stop)
        self.client = SelectClient(self.tables)

    def load(self, names=None, **kwargs):
        return fc.load_attribute_candidates(
            kwargs.pop("client", self.client), [HUM, SOCIAL, FINE] if names is None else names,
            catalog_year=kwargs.pop("catalog_year", YEAR),
            source_catalog_attributes_csv=kwargs.pop("source_catalog_attributes_csv", self.source), **kwargs)


class AttributeLoaderTests(FixtureCase):
    def test_exact_membership_provenance_and_dynamic_year_id(self):
        result = self.load()
        self.assertEqual(result["catalog_year_id"], 7)
        self.assertEqual(result["source_verification"], "HASH_VERIFIED_SOURCE")
        self.assertEqual(result["retrieval_status"], "VISIBLE_READS_AGREE_NOT_ATOMIC")
        for item in result["attributes"].values():
            self.assertTrue(item["coverage_complete"])
            self.assertEqual(item["issues"], [])
        hum = result["attributes"][HUM]
        self.assertEqual([c["course_code"] for c in hum["courses"]], ["HST 101", "HST 200", "PHL 101"])
        c = hum["courses"][-1]
        self.assertEqual(c["matched_attributes"], [HUM])
        self.assertEqual(c["min_credit_hours"], 3)
        self.assertEqual(c["classification_sources"], [self.tables["courseattribute"][0]])

    def test_batching_and_server_cap_preserve_every_row(self):
        client = SelectClient(self.tables, cap=1)
        result = self.load(client=client, batch_size=1, page_size=3)
        self.assertEqual(result, self.load())
        self.assertTrue(all(len(values) <= 1 for call in client.calls for values in call.within.values()))
        self.assertTrue(any(call.start > 0 for call in client.calls))
        self.assertTrue(all(call.orders == ["course_id", "attribute_id", "source_url"]
                            for call in client.calls if call.table_name == "courseattribute"))

    def test_deterministic_input_order_and_duplicates(self):
        shuffled = deepcopy(self.tables)
        for rows in shuffled.values():
            rows.reverse()
        self.assertEqual(self.load(), self.load([SOCIAL, HUM, FINE, HUM], client=SelectClient(shuffled)))

    def test_only_requested_attributes_are_loaded(self):
        result = self.load([HUM])
        self.assertEqual(list(result["attributes"]), [HUM])
        self.assertTrue(all(call.within["attribute_id"] == [1] for call in self.client.calls
                            if call.table_name == "courseattribute"))

    def test_undated_and_other_year_rows_are_not_substitutes(self):
        for year in (None, 8):
            with self.subTest(year=year):
                tables = deepcopy(self.tables)
                tables["courseattribute"][0]["catalog_year_id"] = year
                result = self.load(client=SelectClient(tables))
                item = result["attributes"][HUM]
                self.assertFalse(item["coverage_complete"])
                self.assertNotIn("PHL 101", [c["course_code"] for c in item["courses"]])
                self.assertEqual(item["missing_source_memberships"][0]["course_code"], "PHL 101")

    def test_requested_year_is_not_replaced_with_source_year(self):
        tables = deepcopy(self.tables)
        tables["catalogyear"] = [{"catalog_year_id": 8, "catalog_label": "2025-2026"}]
        tables["courseattribute"][0]["catalog_year_id"] = 8
        result = self.load(client=SelectClient(tables), catalog_year="2025-2026")
        self.assertEqual(result["source_verification"], "SOURCE_YEAR_UNSUPPORTED")
        self.assertEqual(result["catalog_year_id"], 8)
        self.assertEqual([c["course_code"] for c in result["attributes"][HUM]["courses"]], ["PHL 101"])
        self.assertFalse(result["attributes"][HUM]["coverage_complete"])

    def test_missing_or_ambiguous_year(self):
        for years in ([], self.tables["catalogyear"] + [{"catalog_year_id": 8, "catalog_label": YEAR}]):
            tables = deepcopy(self.tables)
            tables["catalogyear"] = years
            result = self.load(client=SelectClient(tables))
            self.assertIsNone(result["catalog_year_id"])
            self.assertEqual(result["attributes"][HUM]["courses"], [])
            self.assertIn("CATALOG_YEAR_MISSING_OR_AMBIGUOUS_OR_HIDDEN", result["attributes"][HUM]["issues"])

    def test_missing_or_ambiguous_attribute_definition(self):
        for attributes in ([a for a in self.tables["attribute"] if a["attribute_name"] != HUM],
                           self.tables["attribute"] + [{"attribute_id": 4, "attribute_name": HUM}]):
            tables = deepcopy(self.tables)
            tables["attribute"] = attributes
            result = self.load(client=SelectClient(tables))
            self.assertFalse(result["attributes"][HUM]["coverage_complete"])
            self.assertEqual(result["attributes"][HUM]["courses"], [])
            self.assertIn("ATTRIBUTE_MISSING_OR_AMBIGUOUS_OR_HIDDEN", result["attributes"][HUM]["issues"])

    def test_missing_course_relationship_blocks_coverage(self):
        self.client.tables["course"].pop(0)
        item = self.load()["attributes"][HUM]
        self.assertFalse(item["coverage_complete"])
        self.assertIn("REFERENCED_COURSE_MISSING_OR_HIDDEN", item["issues"])
        self.assertTrue(item["missing_source_memberships"])

    def test_duplicate_canonical_course_identity_is_not_selected(self):
        self.client.tables["course"][1]["course_code"] = "phl101"
        result = self.load()
        self.assertNotIn("PHL 101", [c["course_code"] for c in result["attributes"][HUM]["courses"]])
        self.assertIn("INVALID_OR_AMBIGUOUS_COURSE_CODE", result["attributes"][HUM]["issues"])

    def test_invalid_course_identity_is_diagnostic(self):
        self.client.tables["course"][0]["course_code"] = "unmapped"
        self.assertIn("INVALID_OR_AMBIGUOUS_COURSE_CODE", self.load()["attributes"][HUM]["issues"])

    def test_no_source_and_changed_source_cannot_prove_coverage(self):
        for raw, expected in ((None, "SOURCE_NOT_SUPPLIED"), (self.source + b"\n", "SOURCE_HASH_MISMATCH")):
            result = self.load(source_catalog_attributes_csv=raw)
            self.assertEqual(result["source_verification"], expected)
            self.assertTrue(result["attributes"][HUM]["courses"])
            self.assertFalse(result["attributes"][HUM]["coverage_complete"])

    def test_invalid_pinned_source_is_rejected(self):
        for raw in (b"not,catalog\n1,2\n", b"\xff", csv_bytes([*self.source_rows, self.source_rows[0]])):
            with patch.object(fc, "_SOURCE_SHA256", hashlib.sha256(raw).hexdigest()):
                self.assertEqual(self.load(source_catalog_attributes_csv=raw)["source_verification"], "SOURCE_INVALID")

    def test_permission_filtered_rows_do_not_establish_complete_coverage(self):
        client = SelectClient(self.tables, visible=lambda table, row: table != "courseattribute" or row["course_id"] != 1)
        result = self.load(client=client)
        self.assertFalse(result["attributes"][HUM]["coverage_complete"])
        self.assertIn("SOURCE_MEMBERSHIP_MISMATCH", result["attributes"][HUM]["issues"])
        self.assertTrue(result["attributes"][FINE]["coverage_complete"])

    def test_unexpected_membership_is_preserved_but_unverified(self):
        extra = deepcopy(self.tables["courseattribute"][3])
        extra["attribute_id"] = 1  # ART 112 is not in the reviewed Humanities export.
        self.client.tables["courseattribute"].append(extra)
        item = self.load()["attributes"][HUM]
        extra_course = next(c for c in item["courses"] if c["course_code"] == "ART 112")
        self.assertEqual(extra_course["membership_status"], "NOT_IN_REVIEWED_SOURCE")
        self.assertTrue(item["unexpected_source_memberships"])
        self.assertFalse(item["coverage_complete"])

    def test_multiple_sources_deduplicate_courses_not_provenance(self):
        extra = {**self.source_rows[0], "source_url": self.source_rows[0]["source_url"] + "additional/"}
        raw = csv_bytes([*self.source_rows, extra])
        self.client.tables["courseattribute"].append({**self.tables["courseattribute"][0], "source_url": extra["source_url"]})
        with patch.object(fc, "_SOURCE_SHA256", hashlib.sha256(raw).hexdigest()):
            item = self.load(source_catalog_attributes_csv=raw, page_size=1)["attributes"][HUM]
        self.assertTrue(item["coverage_complete"])
        self.assertEqual(len(item["courses"]), 3)
        self.assertEqual(len(next(c for c in item["courses"] if c["course_code"] == "PHL 101")["classification_sources"]), 2)

    def test_invalid_provenance_is_not_accepted(self):
        for field, value in (("source_url", ""), ("observed_at", "2026-01-01"), ("observed_at", None)):
            tables = deepcopy(self.tables)
            tables["courseattribute"][0][field] = value
            item = self.load(client=SelectClient(tables))["attributes"][HUM]
            self.assertIn("INVALID_CLASSIFICATION_PROVENANCE", item["issues"])
            self.assertFalse(item["coverage_complete"])

    def test_unstable_reads_never_claim_membership_verified(self):
        reads = []
        def change(query, response):
            if query.table_name == "course":
                reads.append(1)
                if len(reads) == 2:
                    response.data[0]["active_status"] = False
            return response
        result = self.load(client=SelectClient(self.tables, hook=change))
        self.assertEqual(result["retrieval_status"], "READ_CHANGED")
        self.assertTrue(all(not a["coverage_complete"] for a in result["attributes"].values()))
        self.assertTrue(all(c["membership_status"] == "UNSTABLE_READ" for a in result["attributes"].values() for c in a["courses"]))

    def test_query_errors_discard_partial_results_and_redact_sdk_details(self):
        def fail(query, response):
            if query.table_name == "course":
                raise RuntimeError("secret-credential https://private.example/")
            return response
        result = self.load(client=SelectClient(self.tables, hook=fail))
        self.assertEqual(result["retrieval_status"], "UNAVAILABLE")
        self.assertEqual(result["issues"], [{"code": "QUERY_ERROR", "table": "course"}])
        self.assertTrue(all(a["courses"] == [] and not a["coverage_complete"] for a in result["attributes"].values()))
        self.assertNotIn("secret-credential", str(result))

    def test_invalid_response_missing_counts_and_pagination_fail_closed(self):
        def hook_for(mode):
            def change(query, response):
                if query.table_name == "courseattribute":
                    if mode == "count": response.count = None
                    if mode == "empty": response.data = []
                    if mode == "shape": response.data = {"unexpected": True}
                    if mode == "row": response.data[0].pop("observed_at", None)
                    if mode == "filter": response.data[0]["catalog_year_id"] = 99
                    if mode == "bool_year": response.data[0]["catalog_year_id"] = True
                    if mode == "duplicate": response.data.append(deepcopy(response.data[0])); response.count += 1
                return response
            return change
        for mode in ("count", "empty", "shape", "row", "filter", "bool_year", "duplicate"):
            with self.subTest(mode=mode):
                result = self.load(client=SelectClient(self.tables, hook=hook_for(mode)))
                self.assertEqual(result["retrieval_status"], "UNAVAILABLE")
                self.assertEqual(result["attributes"][HUM]["courses"], [])

    def test_count_drift_and_repeated_pages_are_rejected(self):
        for mode in ("count", "repeat"):
            def change(query, response):
                if query.table_name == "courseattribute" and query.start:
                    if mode == "count": response.count += 1
                    else: response.data = [deepcopy(self.tables["courseattribute"][0])]
                return response
            result = self.load(client=SelectClient(self.tables, cap=1, hook=change))
            self.assertEqual(result["retrieval_status"], "UNAVAILABLE")

    def test_read_only_empty_and_invalid_requests(self):
        result = self.load([])
        self.assertEqual(result["retrieval_status"], "NOT_REQUESTED")
        self.assertFalse(self.client.calls)
        for names in (["humanities"], ["Core II Natural Sciences"], "Core II Humanities", [None]):
            with self.assertRaises(ValueError): self.load(names)
        for kwargs in ({"catalog_year": ""}, {"page_size": True}, {"batch_size": 0}, {"page_size": 1001},
                       {"source_catalog_attributes_csv": "not bytes"}):
            with self.assertRaises(ValueError): self.load(**kwargs)
        self.assertFalse(self.client.calls)
        with self.assertRaises(TypeError): self.load(coverage_complete=True)

    def test_results_are_detached_from_client_data(self):
        before = deepcopy(self.client.tables)
        result = self.load()
        result["attributes"][HUM]["courses"][0]["classification_sources"][0]["source_url"] = "changed"
        self.assertEqual(before, self.client.tables)

    def test_actual_reviewed_export_hash_and_selected_memberships(self):
        raw = (ROOT / "backend/scripts/catalog_attributes.csv").read_bytes()
        source = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        selected = [r for r in source if r["attribute_name"] in fc.SUPPORTED_ATTRIBUTES]
        codes = sorted({r["course_code"] for r in selected})
        tables = {"catalogyear": deepcopy(self.tables["catalogyear"]),
                  "attribute": deepcopy(self.tables["attribute"]), "course": [], "courseattribute": []}
        ids = {c: i for i, c in enumerate(codes, 1)}
        attributes = {a["attribute_name"]: a["attribute_id"] for a in tables["attribute"]}
        for code, course_id in ids.items():
            tables["course"].append({"course_id": course_id, "course_code": code, "course_title": "Fixture",
                "min_credit_hours": 3, "max_credit_hours": 3, "active_status": True, "source_url": None})
        for row in selected:
            tables["courseattribute"].append({"course_id": ids[row["course_code"]],
                "attribute_id": attributes[row["attribute_name"]], "source_url": row["source_url"],
                "observed_at": row["observed_at_utc"], "catalog_year_id": 7})
        with patch.object(fc, "_SOURCE_SHA256", REAL_SOURCE_HASH):
            result = self.load(client=SelectClient(tables, cap=7), source_catalog_attributes_csv=raw, batch_size=10)
        self.assertEqual(result["source_verification"], "HASH_VERIFIED_SOURCE")
        self.assertTrue(all(a["coverage_complete"] for a in result["attributes"].values()))
        self.assertEqual({n: len(a["courses"]) for n, a in result["attributes"].items()}, {HUM: 60, SOCIAL: 43, FINE: 13})


class CandidateBuilderTests(FixtureCase):
    def build(self, *requirements, completed=None, loaded=None):
        return fc.build_flexible_candidates(handoff(*requirements, completed=completed), loaded if loaded is not None else self.load())

    def test_supported_categories_use_existing_resolver_mapping(self):
        for name, attr in (("Core II Humanities", HUM), (" core II SOCIAL STUDIES ", SOCIAL),
                           ("Core II Social Sciences", SOCIAL), ("Core II Fine Arts", FINE)):
            with self.subTest(name=name):
                item = self.build(requirement(name=name, category=attr))["requirements"][0]
                self.assertEqual(item["category"], attr)
                self.assertTrue(item["candidates"])
                self.assertTrue(all(c["matched_attributes"] == [attr] for c in item["candidates"]))

    def test_partial_credit_contribution_and_variable_ranges(self):
        result = self.build(requirement(name="Core II Fine Arts", category=FINE))
        courses = {c["course_code"]: c for c in result["requirements"][0]["candidates"]}
        self.assertEqual(courses["ART 112"]["credit_fit"]["status"], "COVERS_REMAINING_CREDITS")
        self.assertEqual(courses["ART 200"]["credit_fit"]["status"], "PARTIAL_CONTRIBUTION")
        self.assertEqual(courses["ART 210"]["credit_fit"]["status"], "DEPENDS_ON_SELECTED_CREDITS")
        self.assertEqual(courses["ART 220"]["credit_fit"]["status"], "COVERS_REMAINING_CREDITS")
        self.assertEqual(courses["ART 210"]["credit_fit"]["contribution_range"], {"min": 1, "max": 3})
        self.assertTrue(all(c["credit_fit"]["assumed_awarded_credits"] is None for c in courses.values()))
        self.assertIsNone(courses["ART 220"]["credit_fit"]["fixed_credit_estimate"])

    def test_variable_with_zero_minimum_is_not_assigned_maximum(self):
        self.client.tables["course"][4]["min_credit_hours"] = 0
        item = self.build(requirement(name="Core II Fine Arts", category=FINE))["requirements"][0]
        fit = next(c["credit_fit"] for c in item["candidates"] if c["course_code"] == "ART 210")
        self.assertEqual(fit["status"], "DEPENDS_ON_SELECTED_CREDITS")
        self.assertEqual(fit["contribution_range"]["min"], 0)

    def test_completed_requirements_are_not_recommended(self):
        req = requirement()
        req["candidate_progress_status"] = "CANDIDATE_MET"
        req["credit_progress"]["additional_credits_indicated_candidate"] = 0
        item = self.build(req)["requirements"][0]
        self.assertEqual(item["generation_status"], "SKIPPED")
        self.assertEqual(item["reasons"], ["REQUIREMENT_ALREADY_CANDIDATE_COMPLETE"])
        self.assertEqual(item["candidates"], [])

    def test_uncertain_progress_and_policy_do_not_create_candidates(self):
        changes = [("candidate_progress_status", "SOURCE_NEEDS_REVIEW"),
                   ("candidate_progress_status", "INSUFFICIENT_EVIDENCE"),
                   ("planning_disposition", "REVIEW_REQUIRED"),
                   ("additional_coursework_indicated", None),
                   ("remaining_policy_review_flags", ["advisor_approval_required"]),
                   ("remaining_policy_review_flags", None), ("source_status", "UNSUPPORTED")]
        for key, value in changes:
            req = requirement()
            req[key] = value
            item = self.build(req)["requirements"][0]
            self.assertEqual(item["generation_status"], "INSUFFICIENT_EVIDENCE")
            self.assertEqual(item["candidates"], [])

    def test_shortfall_must_be_known_positive_and_consistent(self):
        for value in (None, 0, -1, True, "3", float("nan"), float("inf"), 2):
            req = requirement()
            req["credit_progress"]["additional_credits_indicated_candidate"] = value
            self.assertFalse(self.build(req)["candidate_course_codes"])
        for key, value in (("candidate_known_credit_subtotal", None), ("minimum_credits_interpreted", 0),
                           ("candidate_courses_without_known_credits", ["PHL 101"])):
            req = requirement()
            req["credit_progress"][key] = value
            self.assertFalse(self.build(req)["candidate_course_codes"])

    def test_classification_progress_must_be_for_requested_year(self):
        for key, value in (("catalog_year", "2025-2026"), ("catalog_year_classification_lookup_applied", False)):
            req = requirement()
            req["attribute_filter"][key] = value
            self.assertFalse(self.build(req)["candidate_course_codes"])

    def test_matching_is_exact_and_never_inferred_from_titles(self):
        for name in ("Humanities Elective", "Some Core II Humanities course", "Free Elective"):
            result = self.build(requirement(name=name))
            self.assertEqual(result["requirements"], [])
            self.assertEqual(result["skipped_requirements"][0]["reason"], "UNSUPPORTED_REQUIREMENT")
        req = requirement(category=SOCIAL)
        self.assertEqual(self.build(req)["requirements"][0]["reasons"], ["ATTRIBUTE_MAPPING_MISMATCH"])
        req = requirement()
        req["attribute_filter"]["match_mode"] = "ALL"
        self.assertFalse(self.build(req)["candidate_course_codes"])

    def test_deferred_requirements_and_choice_groups_unchanged(self):
        reqs = [requirement(str(i), name=n, category=c) for i, (n, c) in enumerate([
            ("Sci w/Lab Core II Physical/Natural Science", "Core II Natural Sciences"),
            ("Critical Thinking Course", "Critical Thinking"), ("Multicultural or International", "Multicultural")])]
        choice = {"rule_id": "choice", "name": "Science w/ Lab", "kind": "CHOOSE_N",
                  "course_options": [{"kind": "ALL_OF", "course_codes": ["BSC 120", "BSC 120L"]}]}
        data = handoff(*reqs, choice)
        before = deepcopy(data)
        result = fc.build_flexible_candidates(data, self.load())
        self.assertEqual(data, before)
        self.assertEqual(result["requirements"], [])
        self.assertEqual(len(result["skipped_requirements"]), 4)

    def test_completed_courses_excluded_without_changing_credit_progress(self):
        req = requirement(known=3, remaining=3)
        req["attribute_filter"]["candidate_course_codes_entered"] = ["PHL 101"]
        data = handoff(req, completed=["phl101"])
        before = deepcopy(data)
        result = fc.build_flexible_candidates(data, self.load())
        self.assertEqual(data, before)
        self.assertNotIn("PHL 101", result["candidate_course_codes"])
        self.assertEqual(result["requirements"][0]["excluded_completed_course_codes"], ["PHL 101"])
        self.assertEqual(result["requirements"][0]["remaining_credits_candidate"], 3)

    def test_newly_discovered_completed_match_does_not_trigger_extra_course(self):
        result = self.build(completed=["PHL 101"])
        self.assertEqual(result["requirements"][0]["generation_status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(result["candidate_course_codes"], [])

    def test_missing_completed_classification_prevents_claiming_shortfall(self):
        req = requirement(known=3)
        req["attribute_filter"]["candidate_course_codes_entered"] = ["PHL 101"]
        self.client.tables["courseattribute"].pop(0)
        result = self.build(req, completed=["PHL 101"])
        self.assertEqual(result["requirements"][0]["reasons"], ["COMPLETED_CLASSIFICATION_EVIDENCE_MISMATCH"])

    def test_cross_category_membership_deduplicated_without_allocating_credits(self):
        result = self.build(requirement("hum"), requirement("soc", "Core II Social Studies", SOCIAL))
        self.assertEqual(result["course_requirement_memberships"]["HST 101"], ["hum", "soc"])
        self.assertEqual(result["candidate_course_codes"].count("HST 101"), 1)
        self.assertEqual(result["allocation_status"], "NOT_ALLOCATED_NO_DOUBLE_COUNTING_ASSUMED")
        self.assertFalse(result["prerequisites_evaluated"])
        self.assertFalse(result["registration_eligibility_determined"])
        self.assertEqual(validate_candidate_evidence(result["candidate_course_codes"], [], catalog_year=YEAR), result["candidate_course_codes"])

    def test_inactive_and_unknown_active_status_retained_but_withheld(self):
        for value in (False, None, "true", 1):
            self.client.tables["course"][0]["active_status"] = value
            item = self.build()["requirements"][0]
            self.assertNotIn("PHL 101", [c["course_code"] for c in item["candidates"]])
            withheld = next(c for c in item["withheld_courses"] if c["course_code"] == "PHL 101")
            self.assertTrue(any(r in ("INACTIVE_COURSE", "ACTIVE_STATUS_UNKNOWN") for r in withheld["withheld_reasons"]))

    def test_invalid_or_zero_credit_evidence_is_retained_but_withheld(self):
        for low, high in ((None, 3), (3, None), (4, 3), (-1, 3), (0, 0), (float("inf"), 3), ("3", "3"), (True, 3)):
            self.client.tables["course"][0].update(min_credit_hours=low, max_credit_hours=high)
            item = self.build()["requirements"][0]
            self.assertNotIn("PHL 101", [c["course_code"] for c in item["candidates"]])
            self.assertTrue(any(c["course_code"] == "PHL 101" for c in item["withheld_courses"]))

    def test_partial_source_coverage_retains_only_supported_positive_memberships(self):
        self.client.tables["courseattribute"].pop(0)
        result = self.build()
        item = result["requirements"][0]
        self.assertEqual(item["generation_status"], "PARTIAL")
        self.assertFalse(item["coverage"]["coverage_complete"])
        self.assertEqual(result["candidate_course_codes"], ["HST 101"])

    def test_source_not_supplied_keeps_positive_evidence_but_not_complete_claim(self):
        result = self.build(loaded=self.load(source_catalog_attributes_csv=None))
        self.assertTrue(result["candidate_course_codes"])
        self.assertEqual(result["requirements"][0]["generation_status"], "PARTIAL")
        self.assertIn("SOURCE_NOT_SUPPLIED", result["requirements"][0]["reasons"])

    def test_source_unexpected_and_unstable_memberships_are_withheld(self):
        loaded = self.load()
        for state in ("NOT_IN_REVIEWED_SOURCE", "UNSTABLE_READ"):
            for c in loaded["attributes"][HUM]["courses"]:
                c["membership_status"] = state
            result = self.build(loaded=loaded)
            self.assertEqual(result["candidate_course_codes"], [])
            self.assertTrue(result["requirements"][0]["withheld_courses"])

    def test_missing_category_mismatched_year_and_unavailable_loader(self):
        for kind in ("missing", "year", "error"):
            loaded = self.load()
            if kind == "missing": del loaded["attributes"][HUM]
            elif kind == "year": loaded["catalog_year"] = "2025-2026"
            else: loaded["retrieval_status"] = "UNAVAILABLE"
            result = self.build(loaded=loaded)
            self.assertEqual(result["candidate_course_codes"], [])
            self.assertEqual(result["requirements"][0]["generation_status"], "INCOMPLETE")

    def test_output_is_deterministic_and_does_not_alias_inputs(self):
        data = handoff(requirement("hum"), requirement("fine", "Core II Fine Arts", FINE))
        loaded = self.load()
        before = deepcopy((data, loaded))
        result = fc.build_flexible_candidates(data, loaded)
        self.assertEqual((data, loaded), before)
        data["requirements"].reverse()
        self.assertEqual(result, fc.build_flexible_candidates(data, loaded))
        result["requirements"][0]["candidates"][0]["classification_sources"][0]["source_url"] = "changed"
        self.assertEqual(loaded, before[1])
        json.dumps(result, allow_nan=False)

    def test_invalid_handoff_contract_is_explicit_error(self):
        for data in ({}, {**handoff(), "schema_version": "future"}, {**handoff(), "requirements": None},
                     {**handoff(), "completed_course_codes_entered": None}, handoff(requirement(), requirement())):
            with self.assertRaises(ValueError): fc.build_flexible_candidates(data, self.load())
        with self.assertRaises(ValueError): fc.build_flexible_candidates(handoff(), {})


class AuditCompatibilityTests(FixtureCase):
    def test_saved_handoff_preserves_completed_category_and_remaining_shortfalls(self):
        path = ROOT / "backend/scripts/data/degree_requirements/planning_handoff.json"
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        before = deepcopy(data)
        result = fc.build_flexible_candidates(data, self.load())
        self.assertEqual(data, before)
        self.assertEqual(len(result["requirements"]), 3)
        by_category = {r["category"]: r for r in result["requirements"]}
        self.assertEqual(by_category[SOCIAL]["reasons"], ["REQUIREMENT_ALREADY_CANDIDATE_COMPLETE"])
        self.assertEqual(by_category[HUM]["remaining_credits_candidate"], 3)
        self.assertEqual(by_category[FINE]["remaining_credits_candidate"], 3)
        self.assertTrue(by_category[HUM]["candidates"])
        self.assertTrue(by_category[FINE]["candidates"])

    def test_actual_audit_without_classification_lookup_preserves_uncertainty(self):
        path = ROOT / "backend/scripts/data/degree_requirements/resolved/computer_science_bs_resolved.json"
        draft = json.loads(path.read_text(encoding="utf-8-sig"))
        hand = audit.build_planning_handoff(audit.evaluate_preview(draft, []))
        result = fc.build_flexible_candidates(hand, self.load())
        self.assertEqual(result["candidate_course_codes"], [])
        self.assertTrue(all(r["generation_status"] == "INSUFFICIENT_EVIDENCE" for r in result["requirements"]))

    def test_real_audit_handoff_known_shortfall_and_completed_category(self):
        path = ROOT / "backend/scripts/data/degree_requirements/resolved/computer_science_bs_resolved.json"
        draft = json.loads(path.read_text(encoding="utf-8-sig"))
        for completed in ([], ["PHL 101"]):
            evidence = {"catalog_year": YEAR, "catalog_year_id": 7,
                "courses_by_attribute": {HUM: {"PHL 101"}, SOCIAL: set(), FINE: set()},
                "credit_by_course": audit.course_credit_evidence(completed, [self.tables["course"][0]]),
                "sources": {}, "entered_courses_missing_from_course_table": [],
                "courses_with_no_year_scoped_attribute": [], "year_scoped_associations_retrieved": len(completed)}
            preview = audit.evaluate_preview(draft, completed, catalog_attribute_evidence=evidence)
            hand = audit.build_planning_handoff(preview)
            before = deepcopy((preview, hand))
            for version in ("1.0.0", "2.0.0"):
                hand["schema_version"] = version
                result = fc.build_flexible_candidates(hand, self.load())
                hum = next(r for r in result["requirements"] if r["category"] == HUM)
                if completed:
                    self.assertEqual(hum["reasons"], ["REQUIREMENT_ALREADY_CANDIDATE_COMPLETE"])
                    self.assertFalse(hum["candidates"])
                else:
                    self.assertEqual(hum["remaining_credits_candidate"], 3)
                    self.assertEqual([c["course_code"] for c in hum["candidates"]], ["HST 101", "PHL 101"])
                self.assertEqual(hand["requirements"], before[1]["requirements"])
                self.assertEqual(preview, before[0])


if __name__ == "__main__":
    unittest.main()
