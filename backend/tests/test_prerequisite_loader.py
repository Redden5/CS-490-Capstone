"""Mocked REST and loader/evaluator integration tests; no live DB or writes."""

from copy import deepcopy
import csv
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from backend.services import prerequisite_loader as loader
from backend.services.prerequisite_evaluator import evaluate_course_rules
from backend.scripts.parse_prerequisites import parse_rows
from backend.scripts.seed_prerequisites import build_plan, digest


YEAR = "2026-2027"
URL = "https://catalog.marshall.edu/undergraduate/courses-az/cs/"


def csv_bytes(rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def fixture():
    codes = ["CS 110", "CS 120", "CS 210", "CS 220", "CS 230", "MTH 220", "BSC 120L", "CS 999"]
    metadata = [
        {"course_code": code, "catalog_year": YEAR, "source_url": URL,
         "field_label": label, "requirement_text": text}
        for code, label, text in [
            ("CS 210", "pre-req", "CS 110 with a minimum grade of C and (CS 120 or MTH 220 (may be taken concurrently))"),
            ("CS 220", "pre-req", "CS 110"),
            ("CS 220", "co-req", "BSC 120L"),
            ("CS 230", "pre-req", "Consent of instructor."),
        ]
    ]
    catalog = csv_bytes([{"course_code": c} for c in codes])
    raw = csv_bytes(metadata)
    return catalog, raw, tables_from_source(codes, parse_rows(metadata, set(codes)))


def tables_from_source(codes, artifact):
    """Use the EXISTING pure seeder plan as an independent storage fixture."""
    courses = [{"course_id": i, "course_code": c, "source_url": URL} for i, c in enumerate(sorted(codes), 1)]
    years = [{"catalog_year_id": 7, "catalog_label": YEAR}]
    plan = build_plan(artifact, {"courses": courses, "catalog_years": years, "rules": []})
    tables = {"course": courses, "catalogyear": years, "courserule": [], "ruleclause": [], "rulecourse": []}
    for i, operation in enumerate(plan["operations"], 1):
        payload = deepcopy(operation["payload"])
        clauses = payload.pop("clauses")
        tables["courserule"].append({"rule_id": i, **payload})
        for number, atoms in enumerate(clauses, 1):
            clause_id = len(tables["ruleclause"]) + 1
            tables["ruleclause"].append({"clause_id": clause_id, "rule_id": i,
                                         "clause_number": number, "condition_text": None})
            for atom in atoms:
                tables["rulecourse"].append({"rule_course_id": len(tables["rulecourse"]) + 1,
                                             "clause_id": clause_id, **atom})
    return tables


class SelectQuery:
    """Only SELECT methods exist: any mutation, RPC or auth access fails loudly."""

    def __init__(self, client, table):
        self.client, self.table_name = client, table
        self.equals, self.within, self.nulls = {}, {}, []

    def select(self, columns, *, count):
        if count != "exact":
            raise AssertionError("Exact counts required")
        self.columns = columns.split(",")
        return self

    def eq(self, key, value):
        self.equals[key] = value
        return self

    def in_(self, key, values):
        if not values:
            raise AssertionError("Empty IN query")
        self.within[key] = list(values)
        return self

    def is_(self, key, value):
        if value != "null":
            raise AssertionError("Unexpected null filter")
        self.nulls.append(key)
        return self

    def order(self, key):
        self.order_key = key
        return self

    def range(self, start, end):
        self.start, self.end = start, end
        return self

    def execute(self):
        self.client.calls.append(self)
        rows = [r for r in self.client.tables[self.table_name]
                if all(r[k] == v for k, v in self.equals.items())
                and all(r[k] in v for k, v in self.within.items())
                and all(r[k] is None for k in self.nulls)]
        if self.client.visible is not None:
            rows = [r for r in rows if self.client.visible(self.table_name, r)]
        rows = sorted(rows, key=lambda r: r[self.order_key])
        size = min(self.end - self.start + 1, self.client.cap)
        response = SimpleNamespace(data=deepcopy([{k: r[k] for k in self.columns}
                                                  for r in rows[self.start:self.start + size]]), count=len(rows))
        if self.client.hook:
            response = self.client.hook(self, response)
        return response


class SelectClient:
    def __init__(self, tables, *, cap=1000, visible=None, hook=None):
        self.tables, self.cap, self.visible, self.hook = deepcopy(tables), cap, visible, hook
        self.calls = []

    def table(self, name):
        if name not in {"course", "catalogyear", "courserule", "ruleclause", "rulecourse"}:
            raise AssertionError("Unapproved table")
        return SelectQuery(self, name)


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.catalog, self.raw, self.tables = fixture()
        # Synthetic snapshots only in the test process. Production has no pin override argument.
        self.addCleanup(patch.stopall)
        patch.multiple(loader, _CATALOG_SHA256=hashlib.sha256(self.catalog).hexdigest(),
                       _PREREQUISITE_SHA256=hashlib.sha256(self.raw).hexdigest()).start()

    def load(self, codes=None, client=None, **kwargs):
        return loader.load_prerequisite_rules(
            client or SelectClient(self.tables), ["CS 210"] if codes is None else codes,
            source_catalog_csv=self.catalog, source_prerequisite_csv=self.raw, **kwargs)

    def evaluate(self, result, completed, code="CS 210", **kwargs):
        return evaluate_course_rules(code, completed, result["courses"][code]["rule_data"], catalog_year=YEAR, **kwargs)

    def test_happy_path_normalization_and_grade_evaluation(self):
        result = self.load()
        item = result["courses"]["CS 210"]
        self.assertTrue(item["rule_data"]["data_complete"])
        self.assertEqual(item["coverage_status"], "MATCHES_REVIEWED_SOURCE")
        self.assertEqual(item["evaluability"], "COURSE_RULES_REPRESENTABLE")
        rule = item["rule_data"]["rules"][0]
        self.assertEqual(len(rule["clauses"]), 2)
        self.assertEqual(rule["catalog_year_id"], 7)
        self.assertEqual(rule["source_url"], URL)
        self.assertEqual(rule["review_flags"], [])
        for key in ["rule_id", "source_metadata", "content_sha256", "raw_text", "parse_status"]:
            self.assertEqual(rule[key], self.tables["courserule"][0][key])
        self.assertTrue(item["clause_provenance"])
        completed = [{"course_code": "CS 110", "grade": "B"}, "CS 120"]
        self.assertEqual(self.evaluate(result, completed)["status"], "MET")
        self.assertEqual(self.evaluate(result, ["CS 110", "CS 120"])["status"], "UNKNOWN")
        self.assertEqual(self.evaluate(result, [], concurrent_courses=[])["status"], "UNMET")
        json.dumps(result, allow_nan=False)

    def test_resolved_concurrent_atom_is_preserved_not_auto_satisfied(self):
        result = self.load()
        completed = [{"course_code": "CS 110", "grade": "A"}]
        self.assertEqual(self.evaluate(result, completed)["status"], "UNKNOWN")
        self.assertEqual(self.evaluate(result, completed, concurrent_courses=["MTH 220"])["status"], "MET")

    def test_unresolved_and_corequisite_parents_are_complete_but_unknown(self):
        result = self.load(["CS 220", "CS 230"])
        for code in ["CS 220", "CS 230"]:
            with self.subTest(code=code):
                item = result["courses"][code]
                self.assertTrue(item["rule_data"]["data_complete"])
                self.assertEqual(item["evaluability"], "UNKNOWN")
                self.assertTrue(all(r["clauses"] == [] for r in item["rule_data"]["rules"]))
                self.assertEqual(self.evaluate(result, ["CS 110", "BSC 120L"], code)["status"], "UNKNOWN")
        rules = result["courses"]["CS 220"]["rule_data"]["rules"]
        self.assertEqual({r["rule_type"] for r in rules}, {"PREREQUISITE", "COREQUISITE"})
        self.assertTrue(all("timing_policy" not in r for r in rules))

    def test_missing_source_never_trusts_db_metadata_or_hash_alone(self):
        result = loader.load_prerequisite_rules(SelectClient(self.tables), ["CS 210"])
        self.assertEqual(result["source_verification"], "SOURCE_NOT_SUPPLIED")
        self.assertFalse(result["courses"]["CS 210"]["rule_data"]["data_complete"])
        self.assertEqual(self.evaluate(result, [{"course_code": "CS 110", "grade": "A"}, "CS 120"])["status"], "UNKNOWN")

    def test_line_endings_preserve_verified_prerequisite_evidence(self):
        windows = self.load()
        self.assertEqual(windows["source_verification"], "HASH_VERIFIED_SOURCE")
        for catalog in (self.catalog, self.catalog.replace(b"\r\n", b"\n")):
            for raw in (self.raw, self.raw.replace(b"\r\n", b"\n")):
                with self.subTest(catalog_lf=catalog != self.catalog, prerequisites_lf=raw != self.raw):
                    result = loader.load_prerequisite_rules(
                        SelectClient(self.tables), ["CS 210"], source_catalog_csv=catalog, source_prerequisite_csv=raw,
                    )
                    self.assertEqual(result, windows)
                    completed = [{"course_code": "CS 110", "grade": "A"}, "CS 120"]
                    self.assertEqual(self.evaluate(result, completed)["status"], "MET")

    def test_modified_source_bytes_fail_hash_verification(self):
        for catalog, raw in [
            (self.catalog + b"\n", self.raw), (self.catalog, self.raw + b"\n"),
            (self.catalog.replace(b"CS 110", b"CS 111"), self.raw),
            (self.catalog, self.raw.replace(b"grade of C", b"grade of D")),
        ]:
            with self.subTest(catalog_changed=catalog != self.catalog):
                result = loader.load_prerequisite_rules(SelectClient(self.tables), ["CS 210"],
                                                        source_catalog_csv=catalog, source_prerequisite_csv=raw)
                self.assertEqual(result["source_verification"], "SOURCE_HASH_MISMATCH")
                self.assertFalse(result["courses"]["CS 210"]["rule_data"]["data_complete"])

    def test_no_arbitrary_assertion_parameters(self):
        for name in ["data_complete", "visibility_verified", "read_context", "coverage"]:
            with self.subTest(name=name), self.assertRaises(TypeError):
                loader.load_prerequisite_rules(SelectClient(self.tables), ["CS 210"], **{name: True})

    def test_no_metadata_in_verified_catalog_remains_unknown(self):
        result = self.load(["CS 999"])
        item = result["courses"]["CS 999"]
        self.assertEqual(item["coverage_status"], "NO_PUBLISHED_METADATA_IN_REVIEWED_EXPORT")
        self.assertFalse(item["rule_data"]["data_complete"])
        self.assertEqual(item["rule_data"]["rules"], [])
        self.assertEqual(self.evaluate(result, [], "CS 999")["status"], "UNKNOWN")

    def test_missing_target_and_outside_catalog_are_distinct_from_no_metadata(self):
        result = self.load(["CS 888"])
        item = result["courses"]["CS 888"]
        self.assertEqual(item["coverage_status"], "NOT_IN_REVIEWED_CATALOG")
        self.assertIn("TARGET_COURSE_MISSING_OR_HIDDEN", item["issues"])

    def test_source_expected_rule_missing_is_not_no_metadata(self):
        self.tables["courserule"] = []
        result = self.load()
        item = result["courses"]["CS 210"]
        self.assertEqual(item["coverage_status"], "EXPECTED_RULES_INCOMPLETE_OR_DIFFERENT")
        self.assertFalse(item["rule_data"]["data_complete"])

    def test_filtered_parent_and_child_rows_detected_even_with_filtered_counts(self):
        hidden_conditions = [
            lambda t, r: t != "courserule",
            lambda t, r: t != "ruleclause" or r["clause_id"] != 1,
            lambda t, r: t != "rulecourse" or r["rule_course_id"] != 1,
            lambda t, r: t != "course" or r["course_code"] != "CS 110",
            lambda t, r: t != "course" or r["course_code"] != "CS 210",
        ]
        for visible in hidden_conditions:
            with self.subTest(visible=visible):
                result = self.load(client=SelectClient(self.tables, visible=visible))
                self.assertFalse(result["courses"]["CS 210"]["rule_data"]["data_complete"])

    def test_filtered_unresolved_sibling_rule_cannot_disappear(self):
        client = SelectClient(self.tables, visible=lambda t, r: t != "courserule" or r["rule_type"] != "COREQUISITE")
        result = self.load(["CS 220"], client)
        self.assertFalse(result["courses"]["CS 220"]["rule_data"]["data_complete"])

    def test_condition_text_preserved_and_blocks_evaluator(self):
        self.tables["ruleclause"][0]["condition_text"] = "Instructor permission required"
        result = self.load()
        item = result["courses"]["CS 210"]
        self.assertIn("UNSUPPORTED_CONDITION_TEXT", item["issues"])
        self.assertIn("unsupported_conditions", item["rule_data"]["rules"][0])
        self.assertEqual(item["clause_provenance"][0]["condition_text"], "Instructor permission required")
        self.assertEqual(self.evaluate(result, ["CS 110", "CS 120"])["status"], "UNKNOWN")

    def test_missing_reference_atom_is_retained(self):
        missing_id = self.tables["rulecourse"][0]["required_course_id"]
        self.tables["course"] = [r for r in self.tables["course"] if r["course_id"] != missing_id]
        item = self.load()["courses"]["CS 210"]
        atom = item["rule_data"]["rules"][0]["clauses"][0][0]
        self.assertIsNone(atom["course_code"])
        self.assertEqual(atom["required_course_id"], missing_id)
        self.assertIn("REQUIRED_COURSE_MISSING_OR_HIDDEN", item["issues"])

    def test_null_required_id_remains_diagnostic(self):
        self.tables["rulecourse"][0]["required_course_id"] = None
        self.assertFalse(self.load()["courses"]["CS 210"]["rule_data"]["data_complete"])

    def test_no_boolean_coercion_or_grade_defaults(self):
        for key, value in [("concurrent_allowed", "false"), ("concurrent_allowed", None), ("minimum_grade", 3)]:
            with self.subTest(key=key, value=value):
                tables = deepcopy(self.tables)
                tables["rulecourse"][0][key] = value
                item = self.load(client=SelectClient(tables))["courses"]["CS 210"]
                self.assertFalse(item["rule_data"]["data_complete"])
                self.assertEqual(item["rule_data"]["rules"][0]["clauses"][0][0][key], value)

    def test_clause_gaps_duplicates_and_empty_children(self):
        for number in [0, 4, 2, None]:
            with self.subTest(number=number):
                tables = deepcopy(self.tables)
                tables["ruleclause"][0]["clause_number"] = number
                self.assertFalse(self.load(client=SelectClient(tables))["courses"]["CS 210"]["rule_data"]["data_complete"])
        self.tables["rulecourse"] = []
        item = self.load()["courses"]["CS 210"]
        self.assertIn("EMPTY_CLAUSE", item["issues"])

    def test_fingerprint_detects_drift_and_missing_hash(self):
        for value in [None, "a" * 64]:
            tables = deepcopy(self.tables)
            tables["courserule"][0]["content_sha256"] = value
            item = self.load(client=SelectClient(tables))["courses"]["CS 210"]
            self.assertIn("CONTENT_FINGERPRINT_MISMATCH_OR_MISSING", item["issues"])

    def test_self_consistent_but_weakened_db_graph_fails_independent_source(self):
        # Removing an AND condition and recomputing the stored hash must NOT pass.
        parent = self.tables["courserule"][0]
        self.tables["rulecourse"] = [r for r in self.tables["rulecourse"] if r["rule_course_id"] != 1]
        payload = {k: parent[k] for k in loader._PARENT_FIELDS}
        payload["clauses"] = [
            [{k: a[k] for k in ("required_course_id", "minimum_grade", "concurrent_allowed")}
             for a in self.tables["rulecourse"] if a["clause_id"] == c["clause_id"]]
            for c in self.tables["ruleclause"] if c["rule_id"] == parent["rule_id"]
        ]
        parent["content_sha256"] = digest(payload)
        item = self.load()["courses"]["CS 210"]
        self.assertNotIn("CONTENT_FINGERPRINT_MISMATCH_OR_MISSING", item["issues"])
        self.assertIn("REVIEWED_SOURCE_GRAPH_NOT_VERIFIED", item["issues"])

    def test_legacy_and_other_year_rows_never_substitute(self):
        for i, year in [(100, None), (101, 8)]:
            self.tables["courserule"].append({**deepcopy(self.tables["courserule"][0]), "rule_id": i, "catalog_year_id": year})
        result = self.load()
        self.assertTrue(result["courses"]["CS 210"]["rule_data"]["data_complete"])
        self.assertEqual(result["courses"]["CS 210"]["excluded_legacy_rule_ids"], [100])
        self.tables["courserule"] = [r for r in self.tables["courserule"] if r["rule_id"] >= 100]
        item = self.load()["courses"]["CS 210"]
        self.assertFalse(item["rule_data"]["data_complete"])
        self.assertEqual(item["rule_data"]["rules"], [])

    def test_different_requested_year_has_no_source_assurance(self):
        self.tables["catalogyear"].append({"catalog_year_id": 8, "catalog_label": "2027-2028"})
        result = self.load(catalog_year="2027-2028")
        self.assertEqual(result["source_verification"], "SOURCE_YEAR_UNSUPPORTED")
        self.assertFalse(result["courses"]["CS 210"]["rule_data"]["data_complete"])

    def test_missing_or_duplicate_catalog_label_is_incomplete(self):
        for years in [[], self.tables["catalogyear"] + [{"catalog_year_id": 8, "catalog_label": YEAR}]]:
            tables = deepcopy(self.tables)
            tables["catalogyear"] = years
            item = self.load(client=SelectClient(tables))["courses"]["CS 210"]
            self.assertIn("CATALOG_YEAR_MISSING_OR_AMBIGUOUS_OR_HIDDEN", item["issues"])

    def test_duplicate_rule_type_and_course_code(self):
        self.tables["courserule"].append({**self.tables["courserule"][0], "rule_id": 90})
        self.assertIn("INVALID_OR_DUPLICATE_RULE_TYPE", self.load()["courses"]["CS 210"]["issues"])
        self.tables["course"].append({"course_id": 90, "course_code": "CS 210", "source_url": URL})
        with self.assertRaises(loader.PrerequisiteLoadError) as error:
            self.load()
        self.assertEqual(error.exception.code, "AMBIGUOUS_COURSE_CODE")

    def test_batching_normalization_ordering_and_server_cap(self):
        client = SelectClient(self.tables, cap=1)
        result = self.load(["cs220", "CS 210", "cs 210", "CS 230", "CS 999"], client, batch_size=2, page_size=5)
        self.assertEqual(result["requested_course_codes"], ["CS 210", "CS 220", "CS 230", "CS 999"])
        self.assertTrue(result["courses"]["CS 210"]["rule_data"]["data_complete"])
        self.assertTrue(any(q.start > 0 for q in client.calls))
        self.assertTrue(all(len(v) <= 2 for q in client.calls for v in q.within.values()))
        self.assertTrue(all(q.order_key == loader._COLUMNS[q.table_name][0] for q in client.calls))
        for q in client.calls:
            if q.table_name == "courserule" and not q.nulls:
                self.assertEqual(q.equals["catalog_year_id"], 7)

    def test_inactive_flags_do_not_filter_historical_data(self):
        for row in self.tables["course"] + self.tables["catalogyear"]:
            row["active_status"] = False
        self.assertTrue(self.load()["courses"]["CS 210"]["rule_data"]["data_complete"])

    def test_more_than_1000_atoms_with_lower_server_cap(self):
        codes = [f"CS {i}" for i in range(101, 117)] + ["CS 210"]
        expression = " and ".join(f"(CS {i} or CS {i + 1})" for i in range(101, 117, 2))
        rows = [{"course_code": "CS 210", "catalog_year": YEAR, "source_url": URL,
                 "field_label": "pre-req", "requirement_text": expression}]
        catalog, raw = csv_bytes([{"course_code": c} for c in codes]), csv_bytes(rows)
        tables = tables_from_source(codes, parse_rows(rows, set(codes)))
        self.assertEqual(len(tables["rulecourse"]), 2048)
        client = SelectClient(tables, cap=150)
        with patch.multiple(loader, _CATALOG_SHA256=hashlib.sha256(catalog).hexdigest(),
                            _PREREQUISITE_SHA256=hashlib.sha256(raw).hexdigest()):
            result = loader.load_prerequisite_rules(client, ["CS 210"], source_catalog_csv=catalog,
                                                    source_prerequisite_csv=raw, page_size=1000, batch_size=1000)
        item = result["courses"]["CS 210"]
        self.assertTrue(item["rule_data"]["data_complete"])
        self.assertEqual(sum(len(c) for c in item["rule_data"]["rules"][0]["clauses"]), 2048)
        self.assertTrue(any(q.table_name == "rulecourse" and q.start >= 1000 for q in client.calls))

    def test_empty_request_does_not_touch_client(self):
        client = SelectClient(self.tables)
        self.assertEqual(self.load([], client)["courses"], {})
        self.assertEqual(client.calls, [])

    def test_bad_requests_fail_before_network(self):
        for kwargs in [{"course_codes": "CS 210"}, {"course_codes": [None]},
                       {"catalog_year": "2026"}, {"page_size": 0}, {"page_size": True},
                       {"batch_size": 1001}, {"source_catalog_csv": b"only one"},
                       {"source_catalog_csv": "not bytes", "source_prerequisite_csv": b"x"}]:
            client = SelectClient(self.tables)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                loader.load_prerequisite_rules(client, **{"course_codes": ["CS 210"], **kwargs})
            self.assertEqual(client.calls, [])

    def test_inputs_and_client_rows_not_mutated(self):
        client = SelectClient(self.tables)
        before = deepcopy(client.tables)
        result = self.load(client=client)
        result["courses"]["CS 210"]["rule_data"]["rules"][0]["review_flags"].append("consumer edit")
        self.assertEqual(client.tables, before)

    def test_no_count_and_malformed_rows_raise_without_partial_result(self):
        mutations = [lambda r: setattr(r, "count", None), lambda r: setattr(r, "data", None),
                     lambda r: setattr(r, "data", [{}]), lambda r: setattr(r, "count", True)]
        for mutation in mutations:
            def hook(q, response):
                if q.table_name == "rulecourse":
                    mutation(response)
                return response
            with self.subTest(mutation=mutation), self.assertRaises(loader.PrerequisiteLoadError):
                self.load(client=SelectClient(self.tables, hook=hook))

    def test_truncated_empty_page_duplicate_ids_and_count_changes(self):
        for mode in ["empty", "duplicate", "count"]:
            first_id = None
            def hook(q, response):
                nonlocal first_id
                if q.table_name == "rulecourse":
                    if q.start == 0:
                        first_id = response.data[0]["rule_course_id"]
                    else:
                        if mode == "empty":
                            response.data = []
                        elif mode == "duplicate":
                            response.data[0]["rule_course_id"] = first_id
                        else:
                            response.count += 1
                return response
            with self.subTest(mode=mode), self.assertRaises(loader.PrerequisiteLoadError):
                self.load(client=SelectClient(self.tables, hook=hook), page_size=1)

    def test_query_errors_are_sanitized_at_every_table(self):
        for table in self.tables:
            def hook(q, response):
                if q.table_name == table:
                    raise RuntimeError("SECRET https://private.invalid credential details")
                return response
            with self.subTest(table=table), self.assertRaises(loader.PrerequisiteLoadError) as error:
                self.load(client=SelectClient(self.tables, hook=hook))
            self.assertNotIn("SECRET", str(error.exception))
            self.assertNotIn("private.invalid", str(error.exception))
            self.assertEqual(error.exception.table, table)

    def test_second_read_failure_never_returns_first_read_as_complete(self):
        reads = 0
        def hook(q, response):
            nonlocal reads
            if q.table_name == "catalogyear":
                reads += 1
            if reads == 2 and q.table_name == "rulecourse":
                raise RuntimeError("permission denied or connection lost")
            return response
        with self.assertRaises(loader.PrerequisiteLoadError):
            self.load(client=SelectClient(self.tables, hook=hook))

    def test_wrong_year_or_relationship_in_response_is_not_accepted(self):
        for table, field in [("courserule", "catalog_year_id"), ("ruleclause", "rule_id"),
                             ("rulecourse", "clause_id")]:
            def hook(q, response):
                if q.table_name == table and response.data:
                    response.data[0][field] = 99999
                return response
            with self.subTest(table=table), self.assertRaises(loader.PrerequisiteLoadError) as error:
                self.load(client=SelectClient(self.tables, hook=hook))
            self.assertEqual(error.exception.code, "FILTER_MISMATCH")

    def test_unexpected_rule_for_no_metadata_course_is_not_a_coverage_match(self):
        target_id = next(r["course_id"] for r in self.tables["course"] if r["course_code"] == "CS 999")
        self.tables["courserule"].append({**deepcopy(self.tables["courserule"][0]),
                                         "rule_id": 88, "course_id": target_id})
        item = self.load(["CS 999"])["courses"]["CS 999"]
        self.assertIn("UNEXPECTED_DATABASE_RULES", item["issues"])
        self.assertFalse(item["rule_data"]["data_complete"])

    def test_visible_graph_change_between_reads_blocks_whole_batch(self):
        reads = 0
        def hook(q, response):
            nonlocal reads
            if q.table_name == "catalogyear":
                reads += 1
            if reads == 2 and q.table_name == "courserule" and not q.nulls:
                response.data[0]["raw_text"] = "changed during read"
            return response
        result = self.load(["CS 210", "CS 220"], SelectClient(self.tables, hook=hook))
        self.assertEqual(result["retrieval_status"], "READ_CHANGED")
        self.assertTrue(all(not item["rule_data"]["data_complete"] for item in result["courses"].values()))

    def test_filtered_empty_catalog_is_not_a_successful_empty_rule_set(self):
        result = self.load(client=SelectClient(self.tables, visible=lambda t, r: t != "catalogyear"))
        self.assertFalse(result["courses"]["CS 210"]["rule_data"]["data_complete"])

    def test_only_select_interface_used(self):
        client = SelectClient(self.tables)
        self.load(["CS 210", "CS 220"], client)
        self.assertTrue(client.calls)
        self.assertFalse(hasattr(client, "rpc"))
        self.assertFalse(hasattr(client, "auth"))
        self.assertTrue(all(not hasattr(q, verb) for q in client.calls
                            for verb in ["insert", "update", "upsert", "delete"]))


class ReviewedSourceIntegrationTests(unittest.TestCase):
    def test_real_pinned_source_matches_existing_seeder_and_evaluator(self):
        # Local source reads only; the loader itself has no filesystem dependency.
        root = Path(__file__).resolve().parents[1] / "scripts"
        catalog, raw = (root / "courses.csv").read_bytes(), (root / "catalog_prerequisites.csv").read_bytes()
        codes = {r["course_code"] for r in csv.DictReader(io.StringIO(catalog.decode("utf-8-sig")))}
        metadata = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        artifact = parse_rows(metadata, codes)
        artifact["courses"] = [c for c in artifact["courses"] if c["course_code"] in {"CS 210", "CS 110"}]
        client = SelectClient(tables_from_source(codes, artifact))
        result = loader.load_prerequisite_rules(client, ["CS 210", "CS 110"],
                                                source_catalog_csv=catalog, source_prerequisite_csv=raw)
        self.assertEqual(result["source_verification"], "HASH_VERIFIED_SOURCE")
        self.assertTrue(all(item["rule_data"]["data_complete"] for item in result["courses"].values()))
        result210 = evaluate_course_rules("CS 210", [{"course_code": "CS 120", "grade": "A"},
                                                    {"course_code": "MTH 220", "grade": "B"}],
                                          result["courses"]["CS 210"]["rule_data"], catalog_year=YEAR)
        self.assertEqual(result210["status"], "MET")
        self.assertEqual(evaluate_course_rules("CS 110", [], result["courses"]["CS 110"]["rule_data"],
                                               catalog_year=YEAR)["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
