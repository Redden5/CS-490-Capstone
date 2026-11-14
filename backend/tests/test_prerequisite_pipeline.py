"""Offline regression tests. No credentials, network, Supabase or external packages."""
import copy
import csv
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

from backend.scripts.parse_prerequisites import (
    PrerequisiteParseError, audit_additional_metadata, parse_expression, parse_rows,
    reference_candidates, to_dnf,
)
from backend.scripts.seed_prerequisites import RuleStore, build_plan, main, seed, validate_artifact
from backend.scripts.seed_prerequisites import validate_snapshot_manifest
from backend.scripts.validate_prerequisites import make_report
from backend.services.prerequisite_evaluator import evaluate_course_rules

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "backend" / "scripts"
with (SCRIPTS / "catalog_prerequisites.csv").open(encoding="utf-8-sig", newline="") as handle:
    RAW = list(csv.DictReader(handle))
with (SCRIPTS / "courses.csv").open(encoding="utf-8-sig", newline="") as handle:
    CATALOG = list(csv.DictReader(handle))
CODES = {r["course_code"] for r in CATALOG}


def row(code="CS 210", text="CS 110", label="pre-req", year="2026-2027"):
    return {"course_code": code, "requirement_text": text, "field_label": label,
            "catalog_year": year, "source_url": "https://catalog.marshall.edu/undergraduate/courses-az/cs/"}


def actual(code):
    return parse_rows([r for r in RAW if r["course_code"] == code], CODES)["courses"][0]


class ParserTests(unittest.TestCase):
    def test_ordinary_grade_and_concurrency(self):
        result = parse_expression("CS 110 (may be taken concurrently) with a minimum grade of C+.")
        self.assertEqual(result["dnf_clauses"], [[{"course_code": "CS 110", "minimum_grade": "C+", "concurrent_allowed": True}]])
        self.assertEqual(parse_rows([row(label="pre-requisite")], CODES)["summary"]["ready_for_seed_review"], 1)

    def test_annotation_order(self):
        a = parse_expression("CS 110 with a minimum grade of D (may be taken concurrently)")
        b = parse_expression("CS 110 (may be taken concurrently) with a minimum grade of D")
        self.assertEqual(a["ast"], b["ast"])

    def test_suffixes(self):
        for code in ["BSC 120LH", "BSC 120H", "NUR 741-C", "CS 110"]:
            with self.subTest(code=code):
                self.assertEqual(parse_expression(code)["ast"]["course_code"], code)

    def test_catalog_verified_hyphen_spacing(self):
        parsed = parse_expression("NUR 741 -B.", {"NUR 741-B"})
        self.assertEqual(parsed["ast"]["course_code"], "NUR 741-B")
        self.assertEqual(parsed["normalizations"][0]["kind"], "catalog_verified_hyphen_spacing")

    def test_actual_nursing_identity_unresolved(self):
        course = actual("NUR 741-C")
        self.assertFalse(course["complete_enrollment_conditions_machine_checkable"])
        self.assertIn("NUR 741-B", course["rules"][0]["missing_course_references"])
        self.assertIsNone(course["rules"][0]["expression"])
        self.assertIn("NUR 741B", CODES)
        self.assertNotIn("NUR 741-B", CODES)

    def test_boolean_dnf(self):
        parsed = parse_expression("(CS 110 or CS 120) and (CS 130 or (MTH 229 and MTH 132))")
        clauses = {frozenset(a["course_code"] for a in c) for c in parsed["dnf_clauses"]}
        self.assertEqual(clauses, {frozenset(c) for c in [
            ["CS 110", "CS 130"], ["CS 120", "CS 130"],
            ["CS 110", "MTH 229", "MTH 132"], ["CS 120", "MTH 229", "MTH 132"]]})

    def test_dnf_truth_table(self):
        import itertools
        parsed = parse_expression("CS 110 and (CS 120 or CS 130)")
        for a, b, c in itertools.product([False, True], repeat=3):
            completed = {code for code, present in zip(["CS 110", "CS 120", "CS 130"], [a, b, c]) if present}
            relational = any(all(x["course_code"] in completed for x in clause) for clause in parsed["dnf_clauses"])
            self.assertEqual(relational, a and (b or c))

    def test_dnf_cap(self):
        ast = parse_expression("(CS 110 or CS 120) and (MTH 229 or MTH 132)")["ast"]
        with self.assertRaises(PrerequisiteParseError):
            to_dnf(ast, cap=3)

    def test_conflicting_duplicate_grades_not_weakened(self):
        parsed = parse_expression("CS 110 with a minimum grade of C and CS 110 with a minimum grade of D")
        self.assertFalse(parsed["prerequisite_expression_machine_checkable"])
        self.assertEqual(parsed["dnf_clauses"], [])

    def test_concurrent_and_examples(self):
        for code in ["ACC 499", "AVSC 211", "AVSC 340", "COUN 555"]:
            with self.subTest(code=code):
                course = actual(code)
                self.assertTrue(any(c["status"] == "CORROBORATES_INLINE_CONCURRENCY" for c in course["additional_metadata_checks"]))
                self.assertNotIn("concurrent_pr_metadata_requires_review", course["review_flags"])

    def test_concurrent_or_with_external_alternatives(self):
        course = actual("CS 110")
        self.assertEqual(course["additional_metadata_checks"][0]["status"], "CORROBORATES_INLINE_CONCURRENCY")
        self.assertFalse(course["complete_enrollment_conditions_machine_checkable"])

    def test_concurrent_boolean_mismatch_same_codes(self):
        expression = parse_expression("CS 110 (may be taken concurrently) and CS 120 (may be taken concurrently)")
        flags, _ = audit_additional_metadata(expression, [row(text="CS 110 or CS 120", label="concurrent pr")])
        self.assertIn("concurrent_pr_metadata_requires_review", flags)

    def test_genuine_concurrent_mismatches(self):
        for code in ["PHL 250", "RST 250"]:
            self.assertIn("concurrent_pr_metadata_requires_review", actual(code)["review_flags"])

    def test_corequisite_only(self):
        for code, required in [("BIC 621", "BIC 620"), ("BME 465", "BME 405"), ("BSC 104", "BSC 104L"), ("BSC 105", "BSC 105L")]:
            with self.subTest(code=code):
                course = actual(code)
                self.assertIsNone(course["prerequisite_raw_text"])
                self.assertEqual(len(course["rules"]), 1)
                rule = course["rules"][0]
                self.assertEqual(rule["rule_type"], "COREQUISITE")
                self.assertEqual(rule["expression"]["course_code"], required)
                self.assertTrue(rule["syntax_parsed"])
                self.assertFalse(rule["machine_checkable"])
                self.assertNotIn("expected_exactly_one_prerequisite_field", course["review_flags"])

    def test_prerequisite_and_corequisite(self):
        course = actual("BME 310")
        self.assertEqual([r["rule_type"] for r in course["rules"]], ["PREREQUISITE", "COREQUISITE"])
        self.assertEqual([a["minimum_grade"] for a in course["rules"][0]["course_only_dnf"][0]], ["D", "D"])
        self.assertTrue(all(not r["machine_checkable"] for r in course["rules"]))

    def test_corequisite_boolean_syntax(self):
        artifact = parse_rows([row(text="CS 110 and (CS 120 or CS 130)", label="co-req")], CODES)
        rule = artifact["courses"][0]["rules"][0]
        self.assertTrue(rule["course_expression_representable"])
        self.assertEqual(len(rule["course_only_dnf"]), 2)
        self.assertEqual(rule["dnf_clauses"], [])

    def test_comma_list_remains_ambiguous(self):
        course = actual("CLS 270")
        self.assertFalse(course["rules"][1]["syntax_parsed"])
        self.assertEqual(course["rules"][1]["raw_text"], "CLS 271 , CLS 272 , CLS 273")

    def test_scores_preserved_not_verifiable(self):
        parsed = parse_expression("ACT Math with a score of 24 or SAT MATH SECTION SCORE with a score of 580 or CS 110")
        children = parsed["ast"]["children"]
        self.assertEqual([c["op"] for c in children], ["EXTERNAL", "EXTERNAL", "COURSE"])
        self.assertEqual(children[0]["catalog_score"], "24")
        self.assertEqual(children[1]["catalog_score"], "580")
        self.assertEqual(children[0]["verification"], "UNKNOWN")
        self.assertEqual(parsed["dnf_clauses"], [])

    def test_permission_admission_and_placement(self):
        for text, category in [("Consent of instructor.", "PERMISSION"), ("Admission to the Honors College", "ADMISSION"),
                               ("Admitted to Teacher ED with a score of 4", "ADMISSION"),
                               ("Placement Math SP20 with a score of 263", "PLACEMENT_EXAM"),
                               ("Department permission", "PERMISSION"), ("Senior standing", "ACADEMIC_STANDING")]:
            with self.subTest(text=text):
                parsed = parse_expression(text)
                self.assertEqual(parsed["ast"]["condition_type"], category)
                self.assertFalse(parsed["prerequisite_expression_machine_checkable"])

    def test_catalog_permission_and_honors(self):
        self.assertEqual(actual("ACB 660")["rules"][0]["expression"]["condition_type"], "PERMISSION")
        self.assertEqual(actual("ANT 201H")["rules"][0]["expression"]["condition_type"], "ADMISSION")

    def test_mixed_and_or_never_emits_partial_dnf(self):
        for text in ["CS 110 and consent of instructor", "CS 110 or unknown restriction"]:
            parsed = parse_expression(text)
            self.assertFalse(parsed["prerequisite_expression_machine_checkable"])
            self.assertEqual(parsed["dnf_clauses"], [])
            self.assertEqual(len(parsed["ast"]["children"]), 2)

    def test_biology_complex_conditions(self):
        for code in ["BSC 120", "BSC 120H", "BSC 120LH"]:
            course = actual(code)
            self.assertFalse(course["complete_enrollment_conditions_machine_checkable"])
            self.assertTrue(all(not r["dnf_clauses"] for r in course["rules"]))

    def test_known_parenthesis_repairs(self):
        for code in ["CHM 212", "MKT 375", "MKT 445", "MUS 279A", "MUS 279B", "MUS 279C", "MUS 279D", "PSY 799", "UNI 201"]:
            with self.subTest(code=code):
                rule = actual(code)["rules"][0]
                self.assertTrue(rule["syntax_parsed"])
                self.assertTrue(rule["normalizations"])
                self.assertNotEqual(rule["raw_text"], rule["normalized_text"])

    def test_internal_parenthesis_errors_not_repaired(self):
        for text in ["CS 110 and (CS 120 or CS 130", "CS 110) and CS 120", "((CS 110"]:
            with self.assertRaises(PrerequisiteParseError):
                parse_expression(text)

    def test_permission_prose_remains_unsupported(self):
        self.assertFalse(actual("MUS 279A")["rules"][0]["machine_checkable"])

    def test_cr_pr_discrepancies(self):
        course = actual("STA 564")
        self.assertIn("cr_pr_course_discrepancy", course["review_flags"])
        self.assertFalse(course["rules"][0]["machine_checkable"])
        check = next(c for c in course["additional_metadata_checks"] if c["field_label"] == "cr/pr")
        self.assertTrue(check["normalizations"])

    def test_cs120_schedule_review_is_limited_to_the_reviewed_fields(self):
        course = actual("CS 120")
        rule = course["rules"][0]
        self.assertTrue(rule["machine_checkable"])
        self.assertEqual(rule["dnf_clauses"], [
            [{"course_code": code, "minimum_grade": "D", "concurrent_allowed": False}]
            for code in ("CS 110", "CS 110H")
        ])
        check, = course["additional_metadata_checks"]
        self.assertEqual(check["requirement_text"], "CS 110")
        self.assertTrue(check["course_set_discrepancy"])
        self.assertEqual(check["status"], "REVIEWED_SUPPLEMENTARY_METADATA")

        primary = next(r for r in course["source_metadata"] if r["field_label"] == "pre-req")
        extra, = course["additional_metadata"]
        for rows in (
            [{**r, "course_code": "CS 210"} for r in (primary, extra)],
            [{**r, "catalog_year": "2027-2028"} for r in (primary, extra)],
            [{**primary, "requirement_text": primary["requirement_text"].replace("grade of D", "grade of C")}, extra],
            [primary, {**extra, "requirement_text": "CS 110H"}],
            [primary, extra, extra],
        ):
            with self.subTest(rows=rows):
                unreviewed, = parse_rows(rows, CODES)["courses"]
                self.assertIn("cr_pr_semantics_not_interpreted", unreviewed["review_flags"])
                self.assertFalse(unreviewed["rules"][0]["machine_checkable"])

    def test_missing_course_reference(self):
        course = parse_rows([row(text="CS 999")], CODES)["courses"][0]
        self.assertIn("missing_course_references", course["review_flags"])
        self.assertEqual(course["rules"][0]["dnf_clauses"], [])

    def test_duplicate_primary_and_unknown_metadata(self):
        self.assertIn("multiple_requirement_fields", actual("MUS 214")["review_flags"])
        course = parse_rows([row(), row(label="restrictions", text="Senior standing")], CODES)["courses"][0]
        self.assertIn("additional_enrollment_metadata_not_interpreted", course["review_flags"])
        self.assertFalse(course["rules"][0]["machine_checkable"])

    def test_eng354_uses_four_explicit_alternatives_with_minimum_c(self):
        rows = [r for r in RAW if r["course_code"] == "ENG 354"]
        course, = parse_rows(rows, CODES)["courses"]
        rule, = course["rules"]
        alternatives = ["ENG 201", "ENG 200H", "ENG 201H", "SCLA 102"]
        self.assertEqual(course["source_metadata"], rows)
        self.assertEqual(rule["raw_text"], "\n".join(r["requirement_text"] for r in rows))
        self.assertEqual(rule["dnf_clauses"], [
            [{"course_code": code, "minimum_grade": "C", "concurrent_allowed": False}]
            for code in alternatives
        ])
        data = {"course_code": "ENG 354", "catalog_year": course["catalog_year"],
                "data_complete": course["complete_enrollment_conditions_machine_checkable"],
                "rules": [{**rule, "parse_status": rule["status"], "clauses": rule["dnf_clauses"]}]}
        for code in alternatives:
            for grade, expected in (("C", "MET"), ("D", "UNMET"), ("F", "UNMET"),
                                    ("C-", "UNKNOWN"), (None, "UNKNOWN")):
                with self.subTest(code=code, grade=grade):
                    result = evaluate_course_rules("ENG 354", [{"course_code": code, "grade": grade}],
                                                   data, catalog_year=course["catalog_year"])
                    self.assertEqual(result["status"], expected)

    def test_eng354_resolution_is_limited_to_the_reviewed_pair(self):
        explicit, summary = [r for r in RAW if r["course_code"] == "ENG 354"]
        for pair in (
            [{**r, "course_code": "ENG 344"} for r in (explicit, summary)],
            [{**r, "catalog_year": "2027-2028"} for r in (explicit, summary)],
            [{**explicit, "requirement_text": explicit["requirement_text"].replace("of C", "of B")}, summary],
            [explicit, {**summary, "requirement_text": summary["requirement_text"] + " and Senior standing"}],
        ):
            with self.subTest(pair=pair):
                course, = parse_rows(pair, CODES)["courses"]
                self.assertIn("multiple_requirement_fields", course["review_flags"])
                self.assertEqual(course["status"], "NEEDS_REVIEW")

    def test_reviewed_flexible_coursework_alternatives(self):
        composition = ["ENG 203", "ENG 207", "ENG 209", "ENG 210", "ENG 211", "ENG 212",
                       "ENG 213", "ENG 214", "ENG 215", "ENG 220", "ENG 221", "ENG 225",
                       "ENG 231", "ENG 232", "ENG 235", "ENG 236", "ENG 241", "ENG 242"]
        for code in [*composition, "ENG 428", "ENG 429", "ENG 430", "SPN 411"]:
            with self.subTest(code=code):
                rows = [r for r in RAW if r["course_code"] == code]
                course, = parse_rows(rows, CODES)["courses"]
                rule, = course["rules"]
                explicit = next(r["requirement_text"] for r in rows if r["field_label"] == "pre-req")
                self.assertEqual(course["status"], "READY_FOR_SEED_REVIEW")
                self.assertEqual(course["source_metadata"], rows)
                self.assertEqual(rule["raw_text"], "\n".join(r["requirement_text"] for r in rows))
                clauses = rule["dnf_clauses"]
                self.assertEqual(clauses, parse_expression(explicit, CODES)["dnf_clauses"])
                self.assertTrue(all(len(clause) == 1 for clause in clauses))
                alternatives = {atom["course_code"] for atom, in clauses}
                self.assertEqual(set(rule["reference_candidates"]), alternatives)
                if code in composition:
                    self.assertEqual(alternatives, {"ENG 201", "ENG 200H", "ENG 201H", "SCLA 102"})
                elif code == "SPN 411":
                    self.assertEqual(alternatives, {"SPN 315", "SPN 316", "SPN 323", "SPN 324"})
                    self.assertIn("manual verification", course["additional_metadata_checks"][0]["reason"])
                else:
                    self.assertEqual(len(alternatives), 24)
                data = {"course_code": code, "catalog_year": course["catalog_year"],
                        "data_complete": course["complete_enrollment_conditions_machine_checkable"],
                        "rules": [{**rule, "parse_status": rule["status"], "clauses": clauses}]}
                for atom, in clauses:
                    self.assertFalse(atom["concurrent_allowed"])
                    self.assertEqual(atom["minimum_grade"], None if code == "SPN 411" else "C")
                    grades = [(None, "MET")] if code == "SPN 411" else [("C", "MET"), ("D", "UNMET"), (None, "UNKNOWN")]
                    for grade, expected in grades:
                        completed = [{"course_code": atom["course_code"], "grade": grade}]
                        result = evaluate_course_rules(code, completed, data, catalog_year=course["catalog_year"])
                        self.assertEqual(result["status"], expected)
                missing = evaluate_course_rules(code, [], data, catalog_year=course["catalog_year"])
                self.assertEqual(missing["status"], "UNMET")
                self.assertFalse(missing["registration_eligibility_determined"])

    def test_reviewed_literature_fields_require_the_original_sources(self):
        for code in ("ENG 428", "SPN 411"):
            explicit, summary = [r for r in RAW if r["course_code"] == code]
            for pair in ([explicit, {**summary, "requirement_text": summary["requirement_text"] + " and senior standing"}],
                         [{**explicit, "requirement_text": "ENG 201"}, summary],
                         [{**r, "catalog_year": "2027-2028"} for r in (explicit, summary)]):
                with self.subTest(code=code, pair=pair):
                    course, = parse_rows(pair, CODES)["courses"]
                    self.assertIn("multiple_requirement_fields", course["review_flags"])
                    self.assertEqual(course["status"], "NEEDS_REVIEW")

    def test_equivalent_prerequisites_preserve_both_sources(self):
        for first, second in [
            ("BMS 600 .", "BMS 600"),
            ("CS 110 with a minimum grade of C or CS 120 (may be taken concurrently)",
             "CS 120 (may be taken concurrently) or CS 110 with a minimum grade of C"),
        ]:
            with self.subTest(first=first):
                rows = [row(text=first), row(text=second, label="pr")]
                course, = parse_rows(rows, CODES)["courses"]
                rule, = course["rules"]
                self.assertEqual(course["source_metadata"], rows)
                self.assertEqual(rule["raw_text"], first + "\n" + second)
                self.assertEqual(rule["status"], "READY_FOR_SEED_REVIEW")
                self.assertEqual(rule["dnf_clauses"], parse_expression(first, CODES)["dnf_clauses"])

    def test_different_or_unresolved_prerequisite_fields_stay_under_review(self):
        for first, second in [
            ("CS 110 with a minimum grade of C", "CS 110 with a minimum grade of D"),
            ("CS 110 (may be taken concurrently)", "CS 110"),
            ("CS 110 or CS 120", "CS 110 and CS 120"),
            ("CS 110", "CS 110 and permission of instructor"),
            ("CS 110", "CS 110 or"),
            ("Consent", "Consent."),
        ]:
            with self.subTest(first=first, second=second):
                course, = parse_rows([row(text=first), row(text=second, label="pr")], CODES)["courses"]
                self.assertIn("multiple_requirement_fields", course["review_flags"])
                self.assertEqual(course["status"], "NEEDS_REVIEW")
                self.assertEqual(course["rules"][0]["dnf_clauses"], [])

    def test_separate_years(self):
        artifact = parse_rows([row(year="2026-2027"), row(year="2027-2028")], CODES)
        self.assertEqual(len(artifact["courses"]), 2)

    def test_missing_provenance_or_catalog_validation_blocks_readiness(self):
        raw = row()
        raw["source_url"] = ""
        course = parse_rows([raw], CODES)["courses"][0]
        self.assertIn("missing_or_invalid_source_url", course["review_flags"])
        self.assertFalse(course["rules"][0]["machine_checkable"])
        course = parse_rows([row()])["courses"][0]
        self.assertIn("catalog_reference_validation_not_performed", course["review_flags"])

    def test_reference_candidates_include_failed_syntax(self):
        self.assertEqual(reference_candidates("NUR 741 -B and CS 110 , CS 120"), {"NUR 741-B", "CS 110", "CS 120"})


SQLITE_SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE course(course_id INTEGER PRIMARY KEY,course_code TEXT UNIQUE NOT NULL);
CREATE TABLE catalogyear(catalog_year_id INTEGER PRIMARY KEY,catalog_label TEXT NOT NULL);
CREATE TABLE courserule(rule_id INTEGER PRIMARY KEY,course_id INTEGER NOT NULL REFERENCES course,
 rule_type TEXT NOT NULL CHECK(rule_type IN ('PREREQUISITE','COREQUISITE')),
 catalog_year_id INTEGER REFERENCES catalogyear,raw_text TEXT,source_url TEXT,machine_checkable BOOLEAN,
 parse_status TEXT,review_flags TEXT,source_metadata TEXT,content_sha256 TEXT,
 UNIQUE(course_id,rule_type,catalog_year_id));
CREATE TABLE ruleclause(clause_id INTEGER PRIMARY KEY,rule_id INTEGER REFERENCES courserule,
 clause_number INTEGER CHECK(clause_number>0),condition_text TEXT,UNIQUE(rule_id,clause_number));
CREATE TABLE rulecourse(rule_course_id INTEGER PRIMARY KEY,clause_id INTEGER REFERENCES ruleclause,
 required_course_id INTEGER REFERENCES course,minimum_grade TEXT,concurrent_allowed BOOLEAN,
 UNIQUE(clause_id,required_course_id));
"""


class SeederTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.executescript(SQLITE_SCHEMA)
        self.connection.executemany("INSERT INTO course(course_id,course_code) VALUES(?,?)", enumerate(sorted(CODES), 1))
        self.connection.executemany("INSERT INTO catalogyear VALUES(?,?)", [(42, "2026-2027"), (86, "2027-2028")])
        self.connection.commit()
        self.store = RuleStore(self.connection, "sqlite")
        self.artifact = parse_rows([row(text="CS 110 with a minimum grade of C and (CS 120 or CS 215 (may be taken concurrently))")], CODES)

    def tearDown(self):
        self.connection.close()

    def test_default_seed_does_not_write(self):
        statements = []
        self.connection.set_trace_callback(statements.append)
        plan = seed(self.store, self.artifact)
        self.assertEqual(plan["counts"]["insert"], 1)
        self.assertFalse(any(s.upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in statements))
        self.assertEqual(self.store.snapshot()["rules"], [])

    def test_dnf_database_and_idempotency(self):
        first = seed(self.store, self.artifact, apply=True)
        second = seed(self.store, self.artifact, apply=True)
        self.assertEqual(first["counts"]["insert"], 1)
        self.assertEqual(second["counts"]["skip"], 1)
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["rules"][0]["catalog_year_id"], 42)
        self.assertEqual([len(c) for c in snapshot["rules"][0]["clauses"]], [2, 2])
        atoms = self.connection.execute("SELECT minimum_grade,concurrent_allowed FROM rulecourse").fetchall()
        self.assertEqual(sum(a[0] == "C" for a in atoms), 2)
        self.assertEqual(sum(a[1] for a in atoms), 1)

    def test_update_replaces_children(self):
        seed(self.store, self.artifact, apply=True)
        updated = parse_rows([row(text="CS 120 with a minimum grade of B")], CODES)
        plan = seed(self.store, updated, apply=True)
        self.assertEqual(plan["counts"]["update"], 1)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM rulecourse").fetchone()[0], 1)
        self.assertEqual(self.connection.execute("SELECT minimum_grade FROM rulecourse").fetchone()[0], "B")

    def test_update_to_unresolved_clears_old_children(self):
        seed(self.store, self.artifact, apply=True)
        seed(self.store, parse_rows([row(text="CS 110 or consent of instructor")], CODES), apply=True)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM ruleclause").fetchone()[0], 0)
        self.assertFalse(self.store.snapshot()["rules"][0]["machine_checkable"])

    def test_transaction_rolls_back_failure(self):
        original = self.store.replace
        def fail_after_write(payload):
            original(payload)
            raise RuntimeError("injected failure after child insertion")
        with patch.object(self.store, "replace", side_effect=fail_after_write):
            with self.assertRaises(RuntimeError):
                seed(self.store, self.artifact, apply=True)
        self.assertEqual(self.store.snapshot()["rules"], [])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM rulecourse").fetchone()[0], 0)

    def test_preserves_other_catalog_year(self):
        seed(self.store, self.artifact, apply=True)
        later = parse_rows([row(text="CS 120", year="2027-2028")], CODES)
        seed(self.store, later, apply=True)
        self.assertEqual({r["catalog_year_id"] for r in self.store.snapshot()["rules"]}, {42, 86})
        self.assertEqual(seed(self.store, self.artifact)["counts"]["skip"], 1)

    def test_missing_target_or_year_blocks_entire_apply(self):
        for artifact in [parse_rows([row(code="CS 999")], CODES), parse_rows([row(year="2030-2031")], CODES)]:
            with self.assertRaises(ValueError):
                seed(self.store, artifact, apply=True)
        self.assertEqual(self.store.snapshot()["rules"], [])

    def test_missing_db_reference_becomes_unresolved(self):
        artifact = parse_rows([row(text="CS 999")], CODES | {"CS 999"})
        plan = seed(self.store, artifact, apply=True)
        self.assertEqual(plan["missing_references"][0]["required_course_code"], "CS 999")
        rule = self.store.snapshot()["rules"][0]
        self.assertFalse(rule["machine_checkable"])
        self.assertEqual(rule["clauses"], [])

    def test_ambiguous_year_or_course_resolution(self):
        snapshot = self.store.snapshot()
        snapshot["catalog_years"].append({"catalog_label": "2026-2027", "catalog_year_id": 9})
        with self.assertRaises(ValueError):
            build_plan(self.artifact, snapshot)

    def test_corequisite_only_and_combined_persist_source(self):
        artifact = parse_rows([r for r in RAW if r["course_code"] in {"BIC 621", "BME 310"}], CODES)
        seed(self.store, artifact, apply=True)
        rules = self.store.snapshot()["rules"]
        self.assertEqual(len(rules), 3)
        self.assertEqual(sum(r["rule_type"] == "COREQUISITE" for r in rules), 2)
        self.assertTrue(all(not r["clauses"] and not r["machine_checkable"] for r in rules))
        self.assertTrue(all(r["source_metadata"]["course"]["source_metadata"] for r in rules))

    def test_detects_child_drift(self):
        seed(self.store, self.artifact, apply=True)
        self.connection.execute("UPDATE rulecourse SET minimum_grade='F'")
        self.connection.commit()
        self.assertEqual(seed(self.store, self.artifact)["counts"]["update"], 1)

    def test_detects_clause_number_and_null_boolean_drift(self):
        seed(self.store, self.artifact, apply=True)
        self.connection.execute("UPDATE ruleclause SET clause_number=clause_number+10")
        self.connection.commit()
        self.assertEqual(seed(self.store, self.artifact)["counts"]["update"], 1)
        seed(self.store, self.artifact, apply=True)
        self.connection.execute("UPDATE rulecourse SET concurrent_allowed=NULL WHERE concurrent_allowed=0")
        self.connection.commit()
        self.assertEqual(seed(self.store, self.artifact)["counts"]["update"], 1)

    def test_snapshot_manifest_binds_source_and_requires_review(self):
        import hashlib
        catalog = b"test catalog bytes"
        out = io.StringIO(newline="")
        raw = self.artifact["courses"][0]["source_metadata"]
        writer = csv.DictWriter(out, fieldnames=list(raw[0]))
        writer.writeheader()
        writer.writerows(raw)
        metadata = out.getvalue().encode()
        manifest = {"review_status": "APPROVED", "review_evidence": "test-only evidence",
                    "catalog_year": "2026-2027", "catalog_sha256": hashlib.sha256(catalog).hexdigest(),
                    "metadata_sha256": hashlib.sha256(metadata).hexdigest()}
        validate_snapshot_manifest(manifest, catalog, metadata, self.artifact)
        for change in [{"review_status": "UNVERIFIED"}, {"catalog_year": "2025-2026"},
                       {"catalog_sha256": "wrong"}, {"review_evidence": ""}]:
            with self.assertRaises(ValueError):
                validate_snapshot_manifest({**manifest, **change}, catalog, metadata, self.artifact)
        with self.assertRaises(ValueError):
            validate_snapshot_manifest(manifest, catalog, metadata, parse_rows([row(text="CS 120")], CODES))

    def test_db_check_requires_provenance_before_driver_import(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.json"
            path.write_text(json.dumps(self.artifact), encoding="utf-8")
            with patch.dict("sys.modules", {"psycopg": None}), redirect_stderr(io.StringIO()) as err:
                result = main(["--input", str(path), "--catalog", str(SCRIPTS / "courses.csv"), "--db-check"])
            self.assertEqual(result, 1)
            self.assertIn("reviewed provenance", err.getvalue())

    def test_public_schema_qualification_preserves_literals(self):
        from unittest.mock import Mock
        connection = Mock()
        store = RuleStore(connection)
        store.execute("SELECT * FROM course WHERE course_code=%s AND 'courserule'='courserule'", ("CS 110",))
        connection.cursor.return_value.execute.assert_called_once_with(
            "SELECT * FROM public.course WHERE course_code=%s AND 'courserule'='courserule'", ("CS 110",))

    def test_filtered_rls_snapshot_and_missing_grants_rejected(self):
        from unittest.mock import Mock
        store = RuleStore(Mock())
        with patch.object(store, "rows", return_value=[{"relname": "courserule"}]):
            with self.assertRaisesRegex(ValueError, "unfiltered RLS"):
                store.check_access(False)
        with patch.object(store, "rows", return_value=[]), patch.object(store, "execute") as execute:
            execute.return_value.fetchone.return_value = (False,)
            with self.assertRaisesRegex(ValueError, "lacks SELECT"):
                store.check_access(False)

    def test_unmentioned_rules_retained(self):
        seed(self.store, self.artifact, apply=True)
        other = parse_rows([row(code="CS 215")], CODES)
        result = seed(self.store, other, apply=True)
        self.assertEqual(result["counts"]["unmentioned_rules_retained"], 1)
        self.assertEqual(len(self.store.snapshot()["rules"]), 2)

    def test_artifact_tampering_rejected(self):
        validate_artifact(self.artifact, CODES)
        modified = copy.deepcopy(self.artifact)
        modified["courses"][0]["rules"][0]["dnf_clauses"] = []
        with self.assertRaises(ValueError):
            validate_artifact(modified, CODES)

    def test_offline_cli_without_database_driver(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.json"
            path.write_text(json.dumps(self.artifact), encoding="utf-8")
            with patch.dict("sys.modules", {"psycopg": None, "supabase": None, "dotenv": None}), redirect_stdout(io.StringIO()):
                result = main(["--input", str(path), "--catalog", str(SCRIPTS / "courses.csv")])
            self.assertEqual(result, 0)

    def test_apply_requires_exact_review_hash_before_driver_import(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "input.json"
            path.write_text(json.dumps(self.artifact), encoding="utf-8")
            with patch.dict("sys.modules", {"psycopg": None}), redirect_stderr(io.StringIO()):
                result = main(["--input", str(path), "--catalog", str(SCRIPTS / "courses.csv"), "--apply"])
            self.assertEqual(result, 1)

    def test_full_catalog_local_roundtrip(self):
        artifact = parse_rows(RAW, CODES)
        first = seed(self.store, artifact, apply=True)
        second = seed(self.store, artifact, apply=True)
        total = sum(len(c["rules"]) for c in artifact["courses"])
        self.assertEqual(first["counts"]["insert"], total)
        self.assertEqual(second["counts"]["skip"], total)
        self.assertFalse(first["errors"])


class CatalogReportTests(unittest.TestCase):
    def test_full_catalog_invariants_and_degree_scope(self):
        artifact = parse_rows(RAW, CODES)
        degree = json.loads((SCRIPTS / "data/degree_requirements/resolved/computer_science_bs_resolved.json").read_text(encoding="utf-8"))
        report = make_report(CATALOG, RAW, artifact, degree)
        self.assertEqual(len(CODES), 6692)
        self.assertEqual(len(RAW), 2630)
        self.assertEqual(len(artifact["courses"]), 2168)
        self.assertEqual(report["after"]["machine_checkable_rules"], 1306)
        self.assertEqual(report["after"]["unresolved_parent_rules_without_children"], 970)
        self.assertEqual(report["after"]["corequisite_only_courses"], 73)
        self.assertEqual(report["after"]["corroborated_concurrent_pr_courses"], 164)
        self.assertEqual(report["after"]["unmatched_target_courses"], [])
        coverage = {c["course_code"]: c for c in report["computer_science_bs"]["courses"]}
        self.assertIn("BSC 120L", coverage)
        self.assertIn("PHY 201", coverage)
        self.assertEqual(coverage["CS 110"]["category"], "UNRESOLVED_CONDITIONS")
        self.assertTrue(any(c["category"] == "VERIFIED_CATALOG_NO_PUBLISHED_PREREQUISITE_METADATA" for c in coverage.values()))


if __name__ == "__main__":
    unittest.main()
