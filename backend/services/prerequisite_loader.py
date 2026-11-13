"""Read-only REST loader; no client creation, environment loading or file I/O.

Pass a dedicated backend Supabase client and, optionally, the BYTES of the paired
catalog and prerequisite exports. This module only calls table().select(), with
filters, ordering and pagination. It never calls RPCs, auth or mutations.

Completeness is relative to the independently reviewed 2026-2027 source snapshot,
NOT a claim of unrestricted database permissions or a transactionally current
snapshot. There is no visibility_verified/data_complete override. To set
rule_data.data_complete=True ALL of these must hold for the requested course:
  * both CSV hashes match the pinned, reviewed snapshot; source is freshly parsed;
  * two complete, deterministically paginated visible graph reads agree;
  * the target and every required course identity resolve without ambiguity;
  * all expected rule types, parent semantics, source metadata and DNF children
    exactly match that source (no omitted AND atoms or unresolved sibling rules);
  * each stored content_sha256 matches the actual parent/children payload;
  * no relationship, clause-number, condition-text or schema defect was found.

The independent source is the completeness witness: RLS hiding an expected row
cannot be mistaken for a complete graph even when counts are also filtered.
Hidden changes OUTSIDE that reviewed source, and changes after these reads,
cannot be disproved through REST. Before live use, verify the backend role and
deployment still correspond to this source; pause imports during verification.
Repeated SELECTs are drift detection, NOT an atomic snapshot. New source captures
require a reviewed pin update; caller-supplied hashes/approval flags are not used.

No source bytes => useful normalized data with data_complete=False. No rules,
including verified absence of metadata in the source export, never establish
no prerequisites. Unresolved source parents can be completely retrieved while
remaining unevaluable. No corequisite timing policy or equivalency is invented.

Return: {catalog_year, requested_course_codes, source_verification,
         retrieval_status, courses: {code: {load_status, coverage_status,
         evaluability, issues, excluded_legacy_rule_ids, clause_provenance,
         rule_data: <existing evaluator envelope>}}}.
Transport/protocol errors raise sanitized PrerequisiteLoadError; no partial
batch is returned. Data defects remain per-course diagnostics. Invalid request
shapes raise ValueError. Output is detached from all caller-owned data.
"""

from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import csv
import hashlib
import io
import json
import re
from typing import Any

from backend.scripts.parse_prerequisites import canonical_code, parse_rows
from backend.services import reviewed_csv_sha256
# Keep Stage 1 unchanged while sharing its exact request-code normalization.
from backend.services.prerequisite_evaluator import _code


# Reviewed provenance: backend/scripts/data/prerequisites/investigation/snapshot_manifest.json.
# These are trust anchors, not caller-supplied assertions or deployment approval.
_SOURCE_YEAR = "2026-2027"
_CATALOG_SHA256 = "433675cec5f76c9761de6159fa2d7bc54c539ca92745d1a05e252d8a8a694a6d"
_PREREQUISITE_SHA256 = "6b110e9d1f5a202fecbaa68d67f020ce0b0cad7695f2b6c0686dd95a49815ac9"
_PARENT_FIELDS = (
    "course_id", "rule_type", "catalog_year_id", "raw_text", "source_url",
    "machine_checkable", "parse_status", "review_flags", "source_metadata",
)
_COLUMNS = {
    "catalogyear": ("catalog_year_id", "catalog_label"),
    "course": ("course_id", "course_code", "source_url"),
    "courserule": ("rule_id", *_PARENT_FIELDS, "content_sha256"),
    "ruleclause": ("clause_id", "rule_id", "clause_number", "condition_text"),
    "rulecourse": ("rule_course_id", "clause_id", "required_course_id", "minimum_grade", "concurrent_allowed"),
}


class PrerequisiteLoadError(RuntimeError):
    """A safe error code/stage, never SDK messages, URLs, headers or credentials."""

    def __init__(self, code: str, table: str):
        self.code = code
        self.table = table
        super().__init__(f"Prerequisite SELECT failed: {code} ({table})")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _positive_id(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _read_rows(client, table: str, page_size: int, *, equals=None, within=None, undated=False) -> list[dict]:
    """Count-aware pagination tolerates a server cap below page_size, not gaps."""
    rows, expected, last_id = [], None, 0
    pk = _COLUMNS[table][0]
    while True:
        try:
            query = client.table(table).select(",".join(_COLUMNS[table]), count="exact")
            for key, value in (equals or {}).items():
                query = query.eq(key, value)
            for key, values in (within or {}).items():
                query = query.in_(key, values)
            if undated:
                query = query.is_("catalog_year_id", "null")
            response = query.order(pk).range(len(rows), len(rows) + page_size - 1).execute()
        except Exception:
            raise PrerequisiteLoadError("QUERY_ERROR", table) from None
        batch, count = getattr(response, "data", None), getattr(response, "count", None)
        if not isinstance(batch, list) or not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise PrerequisiteLoadError("INVALID_RESPONSE_OR_MISSING_COUNT", table)
        if expected is None:
            expected = count
        if count != expected:
            raise PrerequisiteLoadError("COUNT_CHANGED_DURING_READ", table)
        if len(rows) + len(batch) > expected or len(batch) > page_size or (not batch and len(rows) < expected):
            raise PrerequisiteLoadError("INCOMPLETE_OR_INVALID_PAGE", table)
        for row in batch:
            if not isinstance(row, dict) or any(k not in row for k in _COLUMNS[table]):
                raise PrerequisiteLoadError("MALFORMED_ROW", table)
            if not _positive_id(row[pk]) or row[pk] <= last_id:
                raise PrerequisiteLoadError("DUPLICATE_OR_UNORDERED_ID", table)
            if any(row[k] != v for k, v in (equals or {}).items()) or any(
                row[k] not in values for k, values in (within or {}).items()
            ) or (undated and row["catalog_year_id"] is not None):
                raise PrerequisiteLoadError("FILTER_MISMATCH", table)
            last_id = row[pk]
            rows.append(deepcopy(row))
        if len(rows) == expected:
            return rows


def _batched(client, table, key, values, batch_size, page_size, **filters):
    values = sorted(set(values))
    rows = []
    for start in range(0, len(values), batch_size):
        rows.extend(_read_rows(client, table, page_size,
                               within={key: values[start:start + batch_size]}, **filters))
    pk = _COLUMNS[table][0]
    if len({row[pk] for row in rows}) != len(rows):
        raise PrerequisiteLoadError("DUPLICATE_ID_ACROSS_BATCHES", table)
    return sorted(rows, key=lambda row: row[pk])


def _course_map(rows: list[dict]) -> dict[int, dict]:
    by_id, codes = {}, set()
    for row in rows:
        try:
            code = _code(row["course_code"])
        except ValueError:
            raise PrerequisiteLoadError("INVALID_COURSE_IDENTITY", "course") from None
        if code in codes:
            raise PrerequisiteLoadError("AMBIGUOUS_COURSE_CODE", "course")
        codes.add(code)
        by_id[row["course_id"]] = {**row, "course_code": code}
    return by_id


def _read_graph(client, codes, year, batch_size, page_size):
    years = _read_rows(client, "catalogyear", page_size, equals={"catalog_label": year})
    if len(years) != 1:
        return {"years": years, "targets": [], "rules": [], "legacy": [], "clauses": [], "atoms": [], "references": []}
    targets = _batched(client, "course", "course_code", codes, batch_size, page_size)
    _course_map(targets)
    ids = [r["course_id"] for r in targets]
    rules = _batched(client, "courserule", "course_id", ids, batch_size, page_size,
                     equals={"catalog_year_id": years[0]["catalog_year_id"]})
    legacy = _batched(client, "courserule", "course_id", ids, batch_size, page_size, undated=True)
    clauses = _batched(client, "ruleclause", "rule_id", [r["rule_id"] for r in rules], batch_size, page_size)
    atoms = _batched(client, "rulecourse", "clause_id", [r["clause_id"] for r in clauses], batch_size, page_size)
    reference_ids = [r["required_course_id"] for r in atoms if _positive_id(r["required_course_id"])]
    references = _batched(client, "course", "course_id", reference_ids, batch_size, page_size)
    _course_map(references)
    return dict(years=years, targets=targets, rules=rules, legacy=legacy,
                clauses=clauses, atoms=atoms, references=references)


def _source(catalog_csv, prerequisite_csv, year):
    if catalog_csv is None:
        return "SOURCE_NOT_SUPPLIED", set(), {}
    if year != _SOURCE_YEAR:
        return "SOURCE_YEAR_UNSUPPORTED", set(), {}
    if (reviewed_csv_sha256(catalog_csv) != _CATALOG_SHA256
            or reviewed_csv_sha256(prerequisite_csv) != _PREREQUISITE_SHA256):
        return "SOURCE_HASH_MISMATCH", set(), {}
    try:
        catalog = list(csv.DictReader(io.StringIO(catalog_csv.decode("utf-8-sig"))))
        metadata = list(csv.DictReader(io.StringIO(prerequisite_csv.decode("utf-8-sig"))))
        codes = {canonical_code(row["course_code"]) for row in catalog}
        if len(codes) != len(catalog) or not codes or not metadata:
            raise ValueError("Invalid source catalog")
        parsed = parse_rows(metadata, codes)
        if any(c["catalog_year"] != year for c in parsed["courses"]):
            raise ValueError("Source year mismatch")
        return "HASH_VERIFIED_SOURCE", codes, {c["course_code"]: c for c in parsed["courses"]}
    except (ValueError, KeyError, TypeError, csv.Error):
        return "SOURCE_INVALID", set(), {}


def _normalize(code, year, graph):
    """Preserve raw parents and defective relationships, with evaluator-safe flags."""
    targets = {r["course_code"]: r for r in _course_map(graph["targets"]).values()}
    target = targets.get(code)
    issues, normalized, provenance = [], [], []
    year_id = graph["years"][0]["catalog_year_id"] if len(graph["years"]) == 1 else None
    if year_id is None:
        issues.append("CATALOG_YEAR_MISSING_OR_AMBIGUOUS_OR_HIDDEN")
    if target is None:
        issues.append("TARGET_COURSE_MISSING_OR_HIDDEN")
    references = _course_map(graph["references"])
    # Target/reference reads must agree about identities too.
    combined = {r["course_id"]: r for r in graph["targets"]}
    for row in graph["references"]:
        if row["course_id"] in combined and combined[row["course_id"]] != row:
            issues.append("COURSE_IDENTITY_CHANGED")
        combined[row["course_id"]] = row
    _course_map(list(combined.values()))
    clauses_by_rule, atoms_by_clause = defaultdict(list), defaultdict(list)
    for clause in graph["clauses"]:
        clauses_by_rule[clause["rule_id"]].append(clause)
    for atom in graph["atoms"]:
        atoms_by_clause[atom["clause_id"]].append(atom)
    parents = [r for r in graph["rules"] if target and r["course_id"] == target["course_id"]]
    seen_types = []
    for parent in parents:
        rule = {**deepcopy(parent), "course_code": code, "catalog_year": year, "clauses": []}
        defects, stored_clauses = [], []
        kind = parent["rule_type"]
        if kind not in ("PREREQUISITE", "COREQUISITE") or kind in seen_types:
            defects.append("INVALID_OR_DUPLICATE_RULE_TYPE")
        seen_types.append(kind)
        if (not isinstance(parent["machine_checkable"], bool)
                or parent["parse_status"] not in ("READY_FOR_SEED_REVIEW", "NEEDS_REVIEW")
                or not isinstance(parent["review_flags"], list)
                or any(not isinstance(f, str) for f in parent["review_flags"])
                or not isinstance(parent["source_metadata"], dict)):
            defects.append("INVALID_PARENT_FIELDS")
        raw_clauses = clauses_by_rule[parent["rule_id"]]
        if any(not _positive_id(c["clause_number"]) for c in raw_clauses):
            defects.append("INVALID_CLAUSE_NUMBERS")
            raw_clauses = sorted(raw_clauses, key=lambda c: c["clause_id"])
        else:
            raw_clauses = sorted(raw_clauses, key=lambda c: (c["clause_number"], c["clause_id"]))
            if [c["clause_number"] for c in raw_clauses] != list(range(1, len(raw_clauses) + 1)):
                defects.append("NONCONSECUTIVE_CLAUSE_NUMBERS")
        if parent["machine_checkable"] is True and not raw_clauses:
            defects.append("READY_RULE_WITHOUT_CLAUSES")
        for clause in raw_clauses:
            provenance.append(deepcopy(clause))
            if clause["condition_text"] is not None:
                defects.append("UNSUPPORTED_CONDITION_TEXT")
            raw_atoms = sorted(atoms_by_clause[clause["clause_id"]], key=lambda a: a["rule_course_id"])
            if not raw_atoms:
                defects.append("EMPTY_CLAUSE")
            conditions, stored_atoms, required_ids = [], [], []
            for atom in raw_atoms:
                required_id = atom["required_course_id"]
                required = references.get(required_id) if _positive_id(required_id) else None
                if required is None:
                    defects.append("REQUIRED_COURSE_MISSING_OR_HIDDEN")
                if required_id in required_ids:
                    defects.append("DUPLICATE_REQUIRED_COURSE")
                required_ids.append(required_id)
                if not isinstance(atom["concurrent_allowed"], bool) or (
                    atom["minimum_grade"] is not None and not isinstance(atom["minimum_grade"], str)
                ):
                    defects.append("INVALID_ATOM_FIELDS")
                stored = {k: deepcopy(atom[k]) for k in ("required_course_id", "minimum_grade", "concurrent_allowed")}
                stored_atoms.append(stored)
                conditions.append({**stored, "rule_course_id": atom["rule_course_id"],
                                   "course_code": required["course_code"] if required else None})
            stored_clauses.append(stored_atoms)
            rule["clauses"].append(conditions)
        fingerprint_payload = {k: parent[k] for k in _PARENT_FIELDS}
        fingerprint_payload["clauses"] = stored_clauses
        try:
            fingerprint_matches = parent["content_sha256"] == _digest(fingerprint_payload)
        except (TypeError, ValueError):
            fingerprint_matches = False
        if not fingerprint_matches:
            defects.append("CONTENT_FINGERPRINT_MISMATCH_OR_MISSING")
        if defects:
            # Evaluator does not recognize the old snapshot reader's drift keys.
            # Preserve source flags; separately add this recognized blocking field.
            rule["unsupported_conditions"] = sorted(set(defects))
            issues.extend(defects)
        normalized.append(rule)
    legacy = [r["rule_id"] for r in graph["legacy"] if target and r["course_id"] == target["course_id"]]
    return {
        "load_status": "INCOMPLETE", "coverage_status": "UNKNOWN", "evaluability": "UNKNOWN",
        "issues": sorted(set(issues)), "excluded_legacy_rule_ids": legacy,
        "clause_provenance": provenance,
        "rule_data": {"course_code": code, "catalog_year": year, "catalog_year_id": year_id,
                      "source_url": target["source_url"] if target else None,
                      "data_complete": False, "rules": normalized},
    }


def _matches_source(rules, source_course):
    """Compare ALL enrollment fields/branches, not merely a parent checksum."""
    expected = source_course["rules"]
    if len(rules) != len(expected) or {r["rule_type"] for r in rules} != {r["rule_type"] for r in expected}:
        return False
    for actual in rules:
        source_rule = next(r for r in expected if r["rule_type"] == actual["rule_type"])
        for field in ("raw_text", "source_url", "machine_checkable"):
            if actual[field] != source_rule[field]:
                return False
        if (actual["parse_status"] != source_course["status"]
                or actual["review_flags"] != source_course["review_flags"]
                or actual["source_metadata"] != {"parser_schema_version": 2, "course": source_course,
                                                 "missing_database_references": []}):
            return False
        clauses = [[{k: a[k] for k in ("course_code", "minimum_grade", "concurrent_allowed")}
                    for a in clause] for clause in actual["clauses"]]
        if clauses != source_rule["dnf_clauses"]:
            return False
    return True


def load_prerequisite_rules(
    client,
    course_codes: list[str],
    *,
    catalog_year: str = "2026-2027",
    source_catalog_csv: bytes | None = None,
    source_prerequisite_csv: bytes | None = None,
    batch_size: int = 100,
    page_size: int = 500,
) -> dict[str, Any]:
    """Load a batch using SELECT only; see module docstring for completeness limits.

    Source files are read by the caller, not implicitly from the workspace/env.
    Bytes are rehashed on every call. There is no caller-trusted coverage object.
    """
    if not isinstance(course_codes, list):
        raise ValueError("course_codes must be a list")
    codes = sorted({_code(c) for c in course_codes})
    if not isinstance(catalog_year, str) or not re.fullmatch(r"\d{4}-\d{4}", catalog_year):
        raise ValueError("catalog_year must be a YYYY-YYYY label")
    for value in (batch_size, page_size):
        if not _positive_id(value) or value > 1000:
            raise ValueError("batch_size and page_size must be integers from 1 to 1000")
    if (source_catalog_csv is None) != (source_prerequisite_csv is None) or any(
        v is not None and not isinstance(v, bytes) for v in (source_catalog_csv, source_prerequisite_csv)
    ):
        raise ValueError("Supply both source CSVs as bytes, or neither")
    result = {"catalog_year": catalog_year, "requested_course_codes": codes,
              "source_verification": "NOT_CHECKED", "retrieval_status": "NOT_REQUESTED", "courses": {}}
    if not codes:
        return result
    source_status, catalog_codes, source_courses = _source(source_catalog_csv, source_prerequisite_csv, catalog_year)
    first = _read_graph(client, codes, catalog_year, batch_size, page_size)
    second = _read_graph(client, codes, catalog_year, batch_size, page_size)
    stable = first == second
    result["retrieval_status"] = "VISIBLE_READS_AGREE_NOT_ATOMIC" if stable else "READ_CHANGED"
    result["source_verification"] = source_status
    for code in codes:
        item = _normalize(code, catalog_year, first)
        rules = item["rule_data"]["rules"]
        if not stable:
            item["issues"].append("READ_CHANGED")
        if source_status != "HASH_VERIFIED_SOURCE":
            item["issues"].append(source_status)
        elif code not in catalog_codes:
            item["coverage_status"] = "NOT_IN_REVIEWED_CATALOG"
            item["issues"].append("SOURCE_COURSE_NOT_FOUND")
        elif code not in source_courses:
            item["coverage_status"] = "NO_PUBLISHED_METADATA_IN_REVIEWED_EXPORT"
            item["issues"].append("NO_METADATA_IS_NOT_NO_PREREQUISITES")
            if rules:
                item["issues"].append("UNEXPECTED_DATABASE_RULES")
        elif item["issues"] or not _matches_source(rules, source_courses[code]):
            item["coverage_status"] = "EXPECTED_RULES_INCOMPLETE_OR_DIFFERENT"
            item["issues"].append("REVIEWED_SOURCE_GRAPH_NOT_VERIFIED")
        else:
            item["coverage_status"] = "MATCHES_REVIEWED_SOURCE"
        if not rules:
            item["issues"].append("NO_DATED_RULES_VISIBLE")
        complete = item["coverage_status"] == "MATCHES_REVIEWED_SOURCE" and not item["issues"]
        item["rule_data"]["data_complete"] = complete
        item["load_status"] = "SOURCE_VERIFIED" if complete else "INCOMPLETE"
        item["evaluability"] = "COURSE_RULES_REPRESENTABLE" if complete and all(
            r["machine_checkable"] is True and r["parse_status"] == "READY_FOR_SEED_REVIEW"
            and r["review_flags"] == [] and r["rule_type"] == "PREREQUISITE" for r in rules
        ) else "UNKNOWN"
        item["issues"] = sorted(set(item["issues"]))
        result["courses"][code] = item
    return result
