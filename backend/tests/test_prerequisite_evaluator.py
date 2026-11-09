"""Standalone evaluator tests: standard library only, no files or database I/O."""

import copy
import itertools
import json
import unittest

from backend.services.prerequisite_evaluator import (
    EvaluationInputError, MET, UNKNOWN, UNMET, evaluate_course_rules,
)
from backend.scripts.parse_prerequisites import parse_rows


YEAR = "2026-2027"


def atom(code="CS 110", grade=None, concurrent=False, **extra):
    return {"course_code": code, "minimum_grade": grade,
            "concurrent_allowed": concurrent, **extra}


def rule(clauses=None, **extra):
    return {"rule_id": 10, "rule_type": "PREREQUISITE", "machine_checkable": True,
            "parse_status": "READY_FOR_SEED_REVIEW", "review_flags": [],
            "clauses": [[atom()]] if clauses is None else clauses, **extra}


def data(*rules, **extra):
    return {"course_code": "CS 210", "catalog_year": YEAR, "data_complete": True,
            "rules": list(rules) if rules else [rule()], **extra}


def evaluate(completed=None, payload=None, concurrent=None):
    return evaluate_course_rules("CS 210", [] if completed is None else completed,
                                 data() if payload is None else payload,
                                 catalog_year=YEAR, concurrent_courses=concurrent)


class CourseworkTests(unittest.TestCase):
    def test_legacy_codes_meet_no_grade_requirement(self):
        result = evaluate(["CS 110", "CS 120"])
        self.assertEqual(result["status"], MET)
        self.assertFalse(result["registration_eligibility_determined"])
        self.assertEqual(result["basis"], "STUDENT_REPORTED_EVIDENCE")

    def test_missing_course_is_explained(self):
        result = evaluate(["MTH 220"])
        self.assertEqual(result["status"], UNMET)
        self.assertEqual(result["missing_courses"], ["CS 110"])
        condition = result["rules"][0]["clauses"][0]["conditions"][0]
        self.assertIn("COURSE_NOT_REPORTED_COMPLETED", condition["reasons"])

    def test_mixed_records_and_codes(self):
        result = evaluate(["CS 110", {"course_code": "CS 120", "grade": "B"}],
                          data(rule([[atom(), atom("CS 120", "C")]])))
        self.assertEqual(result["status"], MET)

    def test_compact_lowercase_and_whitespace(self):
        self.assertEqual(evaluate([" cs110 "])["status"], MET)
        self.assertEqual(evaluate(["cs\t110"])["status"], MET)

    def test_parser_suffixes_are_preserved(self):
        for code in ["BSC 120LH", "NUR 741-B", "MTH 123A", "H2O 120"]:
            with self.subTest(code=code):
                self.assertEqual(evaluate([code], data(rule([[atom(code)]])))["status"], MET)
        self.assertEqual(evaluate(["BSC 120L"], data(rule([[atom("BSC 120LH")]])))["status"], UNMET)

    def test_identical_duplicates_collapse(self):
        for completed in [["CS 110", "cs110", {"course_code": "CS 110", "grade": None}],
                          [{"course_code": "CS 110", "grade": " b "},
                           {"course_code": "cs110", "grade": "B"}]]:
            with self.subTest(completed=completed):
                self.assertEqual(evaluate(completed)["status"], MET)

    def test_conflicting_attempts_do_not_choose_best_last_or_first(self):
        for grades in [("A", "F"), ("F", "A"), ("B", "C"), (None, "A")]:
            completed = [{"course_code": "CS 110", "grade": g} for g in grades]
            for minimum in [None, "C"]:
                with self.subTest(grades=grades, minimum=minimum):
                    result = evaluate(completed, data(rule([[atom(grade=minimum)]])))
                    self.assertEqual(result["status"], UNKNOWN)
                    self.assertIn("REPEATED_COURSE_EVIDENCE_CONFLICT",
                                  result["insufficient_grade_evidence"][0]["reasons"])

    def test_unverified_equivalency_remains_unknown(self):
        result = evaluate([{"course_code": "CS 110", "grade": "A", "equivalency_verified": False}])
        self.assertEqual(result["status"], UNKNOWN)
        self.assertEqual(evaluate(["CS 110"], data(rule([[atom(equivalency_verified=False)]])))["status"], UNKNOWN)

    def test_malformed_student_inputs_raise_clear_error(self):
        cases = ["CS 110", None, {}, [123], [None], [{}], [{"course_code": None}],
                 ["not a course"], [{"course_code": "CS 110", "grade": 4}],
                 [{"course_code": "CS 110", "grade": True}],
                 [{"course_code": "CS 110", "equivalency_verified": "yes"}],
                 [{"course_code": "TRANSFER 1", "equivalent_to": "CS 110"}]]
        for completed in cases:
            with self.subTest(completed=completed), self.assertRaises(EvaluationInputError):
                evaluate_course_rules("CS 210", completed, data(), catalog_year=YEAR)

    def test_invalid_request_context(self):
        for target, year in [(None, YEAR), ("bad", YEAR), ("CS 210", None), ("CS 210", " ")]:
            with self.subTest(target=target, year=year), self.assertRaises(EvaluationInputError):
                evaluate_course_rules(target, [], data(), catalog_year=year)
        for schedule in ["CS 110", {}, [None], [{"course_code": "CS 110"}], ["bad"]]:
            with self.subTest(schedule=schedule), self.assertRaises(EvaluationInputError):
                evaluate(concurrent=schedule)


class GradeTests(unittest.TestCase):
    def test_supported_grade_order_above_equal_below(self):
        order = ["F", "D", "C", "B", "A"]
        for earned, minimum in itertools.product(order, repeat=2):
            with self.subTest(earned=earned, minimum=minimum):
                expected = MET if earned != "F" and order.index(earned) >= order.index(minimum) else UNMET
                result = evaluate([{"course_code": "CS 110", "grade": earned}],
                                  data(rule([[atom(grade=minimum)]])))
                self.assertEqual(result["status"], expected)

    def test_grade_case_and_spacing(self):
        self.assertEqual(evaluate([{"course_code": "CS 110", "grade": " b "}],
                                  data(rule([[atom(grade=" c ")]])))["status"], MET)

    def test_missing_grade_for_legacy_and_structured_records(self):
        for completed in [["CS 110"], [{"course_code": "CS 110"}],
                          [{"course_code": "CS 110", "grade": None}]]:
            with self.subTest(completed=completed):
                result = evaluate(completed, data(rule([[atom(grade="C")]])))
                self.assertEqual(result["status"], UNKNOWN)
                self.assertEqual(result["missing_courses"], [])
                self.assertEqual(result["insufficient_grade_evidence"][0], {
                    "rule_index": 1, "rule_id": 10, "course_code": "CS 110",
                    "minimum_grade": "C", "reasons": ["MISSING_REPORTED_GRADE"],
                })

    def test_unsupported_grade_formats_never_imply_completion(self):
        for grade in ["A+", "B-", "C+", "F-", "P", "S", "CR", "TR", "W", "I", "4.0", "", "banana"]:
            for minimum in [None, "C"]:
                with self.subTest(grade=grade, minimum=minimum):
                    result = evaluate([{"course_code": "CS 110", "grade": grade}],
                                      data(rule([[atom(grade=minimum)]])))
                    self.assertEqual(result["status"], UNKNOWN)

    def test_unsupported_minimum_is_not_weakened(self):
        for minimum in ["C+", "B-", "P", "2.0", ""]:
            with self.subTest(minimum=minimum):
                result = evaluate([{"course_code": "CS 110", "grade": "A"}],
                                  data(rule([[atom(grade=minimum)]])))
                self.assertEqual(result["status"], UNKNOWN)
                self.assertIn("UNSUPPORTED_MINIMUM_GRADE", result["insufficient_grade_evidence"][0]["reasons"])

    def test_failing_grade_without_threshold_is_unmet(self):
        self.assertEqual(evaluate([{"course_code": "CS 110", "grade": "F"}])["status"], UNMET)

    def test_low_grade_has_specific_explanation(self):
        result = evaluate([{"course_code": "CS 110", "grade": "D"}], data(rule([[atom(grade="B")]])))
        condition = result["rules"][0]["clauses"][0]["conditions"][0]
        self.assertEqual(condition["reasons"], ["GRADE_BELOW_MINIMUM"])
        self.assertEqual(result["insufficient_grade_evidence"], [])


class BooleanTests(unittest.TestCase):
    @staticmethod
    def evidence(statuses):
        codes = ["CS 110", "CS 120", "MTH 220"]
        return [{"course_code": code, "grade": "A" if status == MET else None}
                for code, status in zip(codes, statuses) if status != UNMET]

    def test_all_and_and_or_truth_table_pairs(self):
        and_table = {(MET, MET): MET, (MET, UNKNOWN): UNKNOWN, (MET, UNMET): UNMET,
                     (UNKNOWN, MET): UNKNOWN, (UNKNOWN, UNKNOWN): UNKNOWN, (UNKNOWN, UNMET): UNMET,
                     (UNMET, MET): UNMET, (UNMET, UNKNOWN): UNMET, (UNMET, UNMET): UNMET}
        or_table = {(MET, MET): MET, (MET, UNKNOWN): MET, (MET, UNMET): MET,
                    (UNKNOWN, MET): MET, (UNKNOWN, UNKNOWN): UNKNOWN, (UNKNOWN, UNMET): UNKNOWN,
                    (UNMET, MET): MET, (UNMET, UNKNOWN): UNKNOWN, (UNMET, UNMET): UNMET}
        for pair in itertools.product([MET, UNKNOWN, UNMET], repeat=2):
            with self.subTest(pair=pair):
                atoms = [atom("CS 110", "C"), atom("CS 120", "C")]
                self.assertEqual(evaluate(self.evidence(pair), data(rule([atoms])))["status"], and_table[pair])
                self.assertEqual(evaluate(self.evidence(pair), data(rule([[a] for a in atoms])))["status"], or_table[pair])

    def test_nested_dnf_all_27_evidence_combinations(self):
        # A OR (B AND C): a known false in B/C eliminates that branch's uncertainty.
        payload = data(rule([[atom("CS 110", "C")], [atom("CS 120", "C"), atom("MTH 220", "C")]]))
        for a, b, c in itertools.product([MET, UNKNOWN, UNMET], repeat=3):
            with self.subTest(a=a, b=b, c=c):
                if a == MET or (b == MET and c == MET):
                    expected = MET
                elif a == UNMET and (b == UNMET or c == UNMET):
                    expected = UNMET
                else:
                    expected = UNKNOWN
                self.assertEqual(evaluate(self.evidence([a, b, c]), payload)["status"], expected)

    def test_branch_diagnostics_survive_satisfying_alternative(self):
        payload = data(rule([[atom()], [atom("CS 120", "C")], [atom("MTH 220")]]))
        result = evaluate(["CS 110", "CS 120"], payload)
        self.assertEqual(result["status"], MET)
        self.assertEqual([c["status"] for c in result["rules"][0]["clauses"]], [MET, UNKNOWN, UNMET])
        self.assertEqual(result["missing_courses"], ["MTH 220"])
        self.assertEqual(result["insufficient_grade_evidence"][0]["course_code"], "CS 120")

    def test_multiple_rules_are_all_required(self):
        payload = data(rule(), rule([[atom("CS 120", "C")]], rule_id=11))
        self.assertEqual(evaluate(["CS 110", "CS 120"], payload)["status"], UNKNOWN)
        self.assertEqual(evaluate(["CS 110"], payload)["status"], UNMET)

    def test_unresolved_rule_not_hidden_by_met_rule(self):
        payload = data(rule(), rule(machine_checkable=False, rule_id=11, clauses=[]))
        result = evaluate(["CS 110"], payload)
        self.assertEqual(result["status"], UNKNOWN)
        self.assertEqual([r["status"] for r in result["rules"]], [MET, UNKNOWN])

    def test_unmet_does_not_erase_other_unknown_diagnostics(self):
        result = evaluate([], data(rule(), rule(machine_checkable=False, rule_id=11)))
        self.assertEqual(result["status"], UNMET)
        self.assertEqual(result["rules"][1]["status"], UNKNOWN)


class TimingTests(unittest.TestCase):
    def test_concurrent_permission_is_not_enrollment(self):
        payload = data(rule([[atom(concurrent=True)]]))
        self.assertEqual(evaluate([], payload)["status"], UNKNOWN)
        self.assertEqual(evaluate([], payload, concurrent=[])["status"], UNMET)
        self.assertEqual(evaluate([], payload, concurrent=["CS 120"])["status"], UNMET)
        self.assertEqual(evaluate([], payload, concurrent=["CS 110"])["status"], MET)

    def test_completed_alternative_needs_no_concurrent_evidence(self):
        self.assertEqual(evaluate(["CS 110"], data(rule([[atom(concurrent=True)]])))["status"], MET)

    def test_concurrent_enrollment_cannot_satisfy_prior_completion_only(self):
        self.assertEqual(evaluate([], concurrent=["CS 110"])["status"], UNMET)

    def test_concurrent_enrollment_does_not_invent_grade(self):
        payload = data(rule([[atom(grade="C", concurrent=True)]]))
        result = evaluate([], payload, concurrent=["CS 110"])
        self.assertEqual(result["status"], UNKNOWN)
        self.assertIn("CONCURRENT_GRADE_NOT_ESTABLISHED", result["insufficient_grade_evidence"][0]["reasons"])
        self.assertEqual(evaluate([{"course_code": "CS 110", "grade": "D"}], payload,
                                  concurrent=["CS 110"])["status"], UNKNOWN)
        self.assertEqual(evaluate([{"course_code": "CS 110", "grade": "B"}], payload)["status"], MET)

    def test_corequisite_requires_explicit_timing_policy(self):
        for timing in [None, "", "PRIOR_COMPLETION", []]:
            with self.subTest(timing=timing):
                result = evaluate(["CS 110"], data(rule(rule_type="COREQUISITE", timing_policy=timing)),
                                  concurrent=["CS 110"])
                self.assertEqual(result["status"], UNKNOWN)
                self.assertIn("COREQUISITE_TIMING_UNKNOWN", result["rules"][0]["reasons"])

    def test_concurrent_only_corequisite_does_not_accept_prior_completion(self):
        payload = data(rule(rule_type="COREQUISITE", timing_policy="CONCURRENT_ONLY"))
        self.assertEqual(evaluate(["CS 110"], payload)["status"], UNKNOWN)
        self.assertEqual(evaluate(["CS 110"], payload, concurrent=[])["status"], UNMET)
        self.assertEqual(evaluate([], payload, concurrent=["CS 110"])["status"], MET)

    def test_completed_or_concurrent_corequisite(self):
        payload = data(rule(rule_type="COREQUISITE", timing_policy="COMPLETED_OR_CONCURRENT"))
        self.assertEqual(evaluate(["CS 110"], payload)["status"], MET)
        self.assertEqual(evaluate([], payload, concurrent=["CS 110"])["status"], MET)
        self.assertEqual(evaluate([], payload, concurrent=[])["status"], UNMET)

    def test_corequisite_grade_does_not_come_from_prior_attempt_in_concurrent_only(self):
        payload = data(rule([[atom(grade="C")]], rule_type="COREQUISITE", timing_policy="CONCURRENT_ONLY"))
        self.assertEqual(evaluate([{"course_code": "CS 110", "grade": "A"}], payload,
                                  concurrent=["CS 110"])["status"], UNKNOWN)

    def test_separate_prerequisite_and_corequisite_groups(self):
        result = evaluate(["CS 110"], data(rule(), rule(rule_id=11, rule_type="COREQUISITE")))
        self.assertEqual(result["rule_groups"]["PREREQUISITE"]["status"], MET)
        self.assertEqual(result["rule_groups"]["COREQUISITE"]["status"], UNKNOWN)
        self.assertEqual(result["status"], UNKNOWN)

    def test_absent_type_is_not_reported_met(self):
        self.assertIsNone(evaluate(["CS 110"])["rule_groups"]["COREQUISITE"]["status"])

    def test_corequisite_readiness_cannot_be_overridden_by_timing(self):
        payload = data(rule(rule_type="COREQUISITE", timing_policy="CONCURRENT_ONLY",
                            review_flags=["corequisite_timing_policy_unresolved"]))
        self.assertEqual(evaluate([], payload, concurrent=["CS 110"])["status"], UNKNOWN)


class RuleDataTests(unittest.TestCase):
    def test_missing_malformed_empty_and_partial_rule_data(self):
        for payload in [None, [], "bad", {}, data(rules=[]), data(rules=None), data(rules={}),
                        data(data_complete=False), data(data_complete=None), data(data_complete=1)]:
            with self.subTest(payload=payload):
                result = evaluate_course_rules("CS 210", ["CS 110"], payload, catalog_year=YEAR)
                self.assertEqual(result["status"], UNKNOWN)
                self.assertEqual(result["rules"], [])

    def test_completeness_must_be_explicit(self):
        payload = data()
        del payload["data_complete"]
        self.assertEqual(evaluate(["CS 110"], payload)["status"], UNKNOWN)

    def test_mismatched_or_undated_context_never_evaluates(self):
        for payload in [data(course_code="CS 220"), data(course_code=None), data(catalog_year=None),
                        data(catalog_year="2025-2026")]:
            with self.subTest(payload=payload):
                result = evaluate(["CS 110"], payload)
                self.assertEqual(result["status"], UNKNOWN)
                self.assertEqual(result["rules"], [])

    def test_rule_specific_target_and_year_validation(self):
        for extra in [{"catalog_year": None}, {"catalog_year": "2025-2026"},
                      {"catalog_year_id": None}, {"course_code": "CS 220"}, {"course_code": None}]:
            with self.subTest(extra=extra):
                self.assertEqual(evaluate(["CS 110"], data(rule(**extra)))["status"], UNKNOWN)

    def test_nonready_and_missing_readiness_state(self):
        for extra in [{"machine_checkable": False}, {"machine_checkable": 1},
                      {"parse_status": "NEEDS_REVIEW"}, {"review_flags": ["external_condition"]},
                      {"review_flags": None}, {"review_flags": ""}]:
            with self.subTest(extra=extra):
                self.assertEqual(evaluate(["CS 110"], data(rule(**extra)))["status"], UNKNOWN)
        for key in ["machine_checkable", "parse_status", "review_flags"]:
            r = rule()
            del r[key]
            self.assertEqual(evaluate(["CS 110"], data(r))["status"], UNKNOWN)

    def test_unresolved_parent_without_clauses_never_means_no_requirements(self):
        for clauses in [[], [[]], [[atom()], []], None, {}, "CS 110"]:
            with self.subTest(clauses=clauses):
                r = rule()
                r["clauses"] = clauses
                self.assertEqual(evaluate(["CS 110"], data(r))["status"], UNKNOWN)

    def test_malformed_and_unsupported_rules(self):
        for r in [None, "bad", [], {}, rule(rule_type="PERMISSION"),
                  rule(condition_text="permission required"), rule(unsupported_conditions=["admission"])]:
            with self.subTest(rule=r):
                self.assertEqual(evaluate(["CS 110"], data(r))["status"], UNKNOWN)

    def test_malformed_unmapped_and_unsupported_atoms(self):
        invalid = [None, "CS 110", {}, atom(code=None), atom(code="bad"),
                   atom(grade=3), atom(concurrent=None), atom(concurrent=1),
                   atom(op="EXTERNAL"), atom(condition_text="permission"),
                   atom(equivalency_verified="yes")]
        for a in invalid:
            with self.subTest(atom=a):
                # Even a satisfied OR branch cannot hide structurally invalid rule data.
                result = evaluate(["CS 110"], data(rule([[atom()], [a]])))
                self.assertEqual(result["status"], UNKNOWN)
        for key in ["minimum_grade", "concurrent_allowed"]:
            a = atom()
            del a[key]
            self.assertEqual(evaluate(["CS 110"], data(rule([[a]])))["status"], UNKNOWN)

    def test_duplicate_atoms_are_not_silently_weakened(self):
        self.assertEqual(evaluate(["CS 110"], data(rule([[atom(), atom(grade="C")]])))["status"], UNKNOWN)

    def test_legacy_parser_dnf_is_not_consumed(self):
        r = rule()
        del r["clauses"]
        r["dnf_clauses"] = [[atom()]]
        self.assertEqual(evaluate(["CS 110"], data(r))["status"], UNKNOWN)

    def test_identifiers_and_provenance_survive(self):
        payload = data(rule([[atom(required_course_id=42, rule_course_id=99)]],
                            source_url="https://example.invalid/catalog", raw_text="CS 110",
                            content_sha256="a" * 64), source_url="https://example.invalid/program")
        result = evaluate(["CS 110"], payload)
        self.assertEqual(result["source_url"], payload["source_url"])
        actual = result["rules"][0]
        for key in ["rule_id", "source_url", "raw_text", "content_sha256"]:
            self.assertEqual(actual[key], payload["rules"][0][key])
        self.assertEqual(actual["clauses"][0]["conditions"][0]["required_course_id"], 42)
        json.dumps(result, allow_nan=False)

    def test_inputs_are_not_mutated_and_outputs_do_not_alias_them(self):
        payload = data(rule(review_flags=["needs_review"]))
        completed = [{"course_code": "cs110", "grade": "a"}]
        schedule = ["cs120"]
        before = copy.deepcopy((payload, completed, schedule))
        result = evaluate(completed, payload, schedule)
        result["rules"][0]["review_flags"].append("consumer_edit")
        self.assertEqual((payload, completed, schedule), before)

    def test_normalized_current_parser_rules(self):
        parsed = parse_rows([{
            "course_code": "CS 210", "field_label": "pre-req",
            "requirement_text": "CS 110 and (CS 120 or MTH 220)",
            "catalog_year": YEAR,
            "source_url": "https://catalog.marshall.edu/undergraduate/courses-az/cs/",
        }], {"CS 210", "CS 110", "CS 120", "MTH 220"})
        course = parsed["courses"][0]
        normalized = [{**r, "parse_status": r["status"], "clauses": r["dnf_clauses"]}
                      for r in course["rules"]]
        payload = data(*normalized)
        self.assertEqual(evaluate(["CS 110", "MTH 220"], payload)["status"], MET)
        self.assertEqual(evaluate(["CS 110"], payload)["status"], UNMET)


if __name__ == "__main__":
    unittest.main()
