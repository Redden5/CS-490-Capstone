"""Offline validation by default; explicit PostgreSQL or Supabase RPC modes.

Never loads .env. PREREQUISITE_DATABASE_DSN is consulted only with --db-check
or --apply. Supabase credentials are consulted only in --api-check/--api-apply.
API imports use one transactional RPC, never separate REST table mutations.
Importing this module has no I/O.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
import hashlib
import json
import os
import re
from pathlib import Path
import sys
from typing import Any

from backend.scripts.parse_prerequisites import canonical_code, parse_rows


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def validate_artifact(artifact: dict, catalog_codes: set[str]) -> None:
    """Reparse preserved source, so hand-edited readiness/DNF flags are not trusted."""
    if artifact.get("schema_version") != 2 or not artifact.get("courses"):
        raise ValueError("Expected nonempty schema_version=2 parser artifact")
    rows = [r for c in artifact["courses"] for r in c["source_metadata"]]
    expected = parse_rows(rows, catalog_codes)
    if expected["courses"] != artifact["courses"] or expected["summary"] != artifact["summary"]:
        raise ValueError("Artifact differs from a fresh parse of its source; regenerate it with --catalog")


def validate_snapshot_manifest(manifest: dict, catalog_bytes: bytes, metadata_bytes: bytes,
                               artifact: dict) -> None:
    """A review acknowledgment binds the actual inputs; it does not certify itself."""
    import csv
    import io
    if manifest.get("review_status") != "APPROVED" or not manifest.get("review_evidence", "").strip():
        raise ValueError("Catalog snapshot is not approved: reconcile dated source provenance first")
    for name, raw in [("catalog_sha256", catalog_bytes), ("metadata_sha256", metadata_bytes)]:
        if manifest.get(name) != hashlib.sha256(raw).hexdigest():
            raise ValueError(f"Snapshot manifest {name} does not match the supplied file")
    years = {c["catalog_year"] for c in artifact["courses"]}
    if years != {manifest.get("catalog_year")}:
        raise ValueError("Snapshot manifest catalog year differs from parsed data")
    source_rows = list(csv.DictReader(io.StringIO(metadata_bytes.decode("utf-8-sig"))))
    preserved = [r for c in artifact["courses"] for r in c["source_metadata"]]
    if Counter(digest(r) for r in source_rows) != Counter(digest(r) for r in preserved):
        raise ValueError("Parsed source rows differ from the approved raw metadata file")


def unique_map(rows: list[dict], key: str, value: str) -> dict:
    result = {}
    for row in rows:
        name = row[key]
        if name in result:
            raise ValueError(f"Ambiguous {key}: {name}")
        result[name] = row[value]
    return result


def build_plan(artifact: dict, snapshot: dict) -> dict:
    """Resolve all IDs and compare complete payloads without performing writes."""
    courses = unique_map(snapshot["courses"], "course_code", "course_id")
    years = unique_map(snapshot["catalog_years"], "catalog_label", "catalog_year_id")
    existing = {}
    for row in snapshot["rules"]:
        key = (row["course_id"], row["rule_type"], row["catalog_year_id"])
        if key in existing:
            raise ValueError(f"Duplicate rule key: {key}")
        existing[key] = row
    operations, errors, missing, counts = [], [], [], Counter()
    seen = set()
    for course in artifact["courses"]:
        code, label = course["course_code"], course["catalog_year"]
        if code not in courses or label not in years:
            errors.append(f"Cannot resolve target/year: {code} / {label}")
            counts["unmapped_courses"] += 1
            continue
        db_missing = sorted({ref for r in course["rules"] for ref in r["reference_candidates"] if ref not in courses})
        missing.extend({"course_code": code, "required_course_code": ref} for ref in db_missing)
        unresolved = not course["complete_enrollment_conditions_machine_checkable"] or bool(db_missing)
        counts["review_courses"] += unresolved
        for rule in course["rules"]:
            key = (courses[code], rule["rule_type"], years[label])
            if key in seen:
                raise ValueError(f"Repeated incoming rule: {key}")
            seen.add(key)
            machine = rule["machine_checkable"] and not unresolved
            flags = list(course["review_flags"])
            if db_missing:
                flags.append("missing_database_course_references")
            payload = {"course_id": key[0], "rule_type": key[1], "catalog_year_id": key[2],
                       "raw_text": rule["raw_text"], "source_url": rule["source_url"],
                       "machine_checkable": machine,
                       "parse_status": "READY_FOR_SEED_REVIEW" if machine else "NEEDS_REVIEW",
                       "review_flags": flags,
                       "source_metadata": {"parser_schema_version": 2, "course": course,
                                           "missing_database_references": db_missing},
                       "clauses": [[{"required_course_id": courses[a["course_code"]],
                                     "minimum_grade": a["minimum_grade"],
                                     "concurrent_allowed": a["concurrent_allowed"]}
                                    for a in clause] for clause in rule["dnf_clauses"]] if machine else []}
            payload["content_sha256"] = digest(payload)
            previous = existing.get(key)
            # Compare actual children too: a stale fingerprint must not conceal drift.
            action = "insert" if previous is None else "skip" if previous == payload else "update"
            counts[action] += 1
            operations.append({"action": action, "course_code": code, "catalog_year": label,
                               "previous_summary": {k: previous.get(k) for k in ["raw_text", "machine_checkable",
                                     "parse_status", "review_flags", "clauses", "content_sha256"]} if previous else None,
                               "payload": payload})
    incoming_years = {years[c["catalog_year"]] for c in artifact["courses"] if c["catalog_year"] in years}
    retained = [list(key) for key in existing if key[2] in incoming_years and key not in seen]
    return {"counts": {**{k: counts[k] for k in ["insert", "update", "skip", "review_courses", "unmapped_courses"]},
                       "unmentioned_rules_retained": len(retained)},
            "errors": errors, "missing_references": missing, "unmentioned_rules_retained": retained,
            "operations": operations}


class RuleStore:
    """Small DB-API adapter. SQLite dialect exists only for isolated local tests."""
    def __init__(self, connection, dialect: str = "postgres"):
        self.connection = connection
        self.dialect = dialect

    def execute(self, sql: str, params=()):
        if self.dialect == "sqlite":
            sql = sql.replace("%s", "?")
        else:
            # Only static SQL identifiers; all values remain parameterized.
            sql = re.sub(r"'(?:''|[^'])*'|(?<![\w.])(?:courserule|ruleclause|rulecourse|catalogyear|course)\b",
                         lambda m: m[0] if m[0].startswith("'") else "public." + m[0], sql)
        cursor = self.connection.cursor()
        cursor.execute(sql, params)
        return cursor

    def rows(self, sql: str) -> list[dict]:
        cursor = self.execute(sql)
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]

    @contextmanager
    def transaction(self, write: bool = False):
        if self.dialect == "postgres":
            with self.connection.transaction():
                self.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ" + ("" if write else " READ ONLY"))
                self.execute("SET LOCAL lock_timeout = '10s'")
                self.execute("SET LOCAL statement_timeout = '120s'")
                self.execute("SET LOCAL row_security = off")
                if write:
                    # Serialize concurrent imports before the snapshot is read.
                    self.execute("LOCK TABLE courserule, ruleclause, rulecourse IN SHARE ROW EXCLUSIVE MODE")
                self.check_access(write)
                yield
            return
        self.execute("BEGIN")
        try:
            yield
        except BaseException:
            self.connection.rollback()
            raise
        else:
            if write:
                self.connection.commit()
            else:
                self.connection.rollback()

    def check_access(self, write: bool):
        """Reject filtered snapshots; this never grants privileges or bypasses RLS."""
        blocked = self.rows("""SELECT c.relname FROM pg_catalog.pg_class c
            JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_catalog.pg_roles r ON r.rolname=current_user
            WHERE n.nspname='public' AND c.relname IN
              ('course','catalogyear','courserule','ruleclause','rulecourse')
            AND c.relrowsecurity AND NOT (r.rolsuper OR r.rolbypassrls OR
              (c.relowner=r.oid AND NOT c.relforcerowsecurity))""")
        if blocked:
            raise ValueError("Import role cannot guarantee an unfiltered RLS snapshot")
        privileges = [(table, "SELECT") for table in ["course", "catalogyear", "courserule", "ruleclause", "rulecourse"]]
        if write:
            privileges += [(table, privilege) for table, ops in [
                ("courserule", ["INSERT", "UPDATE"]), ("ruleclause", ["INSERT", "DELETE"]),
                ("rulecourse", ["INSERT", "DELETE"])] for privilege in ops]
        for table, privilege in privileges:
            if not self.execute("SELECT pg_catalog.has_table_privilege(current_user,%s,%s)",
                                ("public." + table, privilege)).fetchone()[0]:
                raise ValueError(f"Import role lacks {privilege} on public.{table}")

    def snapshot(self) -> dict:
        rules = self.rows("SELECT course_id,rule_type,catalog_year_id,raw_text,source_url,machine_checkable,parse_status,review_flags,source_metadata,content_sha256,rule_id FROM courserule")
        clauses = self.rows("SELECT clause_id,rule_id,clause_number,condition_text FROM ruleclause ORDER BY clause_number")
        atoms = self.rows("SELECT clause_id,required_course_id,minimum_grade,concurrent_allowed FROM rulecourse ORDER BY rule_course_id")
        by_clause = {}
        for atom in atoms:
            clause_id = atom.pop("clause_id")
            if atom["concurrent_allowed"] is not None:
                atom["concurrent_allowed"] = bool(atom["concurrent_allowed"])
            by_clause.setdefault(clause_id, []).append(atom)
        for rule in rules:
            rule_id = rule.pop("rule_id")
            if rule["machine_checkable"] is not None:
                rule["machine_checkable"] = bool(rule["machine_checkable"])
            for name in ["review_flags", "source_metadata"]:
                if isinstance(rule[name], str):
                    rule[name] = json.loads(rule[name])
            rule["clauses"] = [by_clause.get(c["clause_id"], []) for c in clauses if c["rule_id"] == rule_id]
            # Unexpected condition_text is data drift; never silently mark it unchanged.
            if any(c["condition_text"] is not None for c in clauses if c["rule_id"] == rule_id):
                rule["unexpected_condition_text"] = True
            numbers = [c["clause_number"] for c in clauses if c["rule_id"] == rule_id]
            if numbers != list(range(1, len(numbers) + 1)):
                rule["unexpected_clause_numbers"] = numbers
        return {"courses": self.rows("SELECT course_id,course_code FROM course"),
                "catalog_years": self.rows("SELECT catalog_year_id,catalog_label FROM catalogyear"), "rules": rules}

    def replace(self, payload: dict):
        columns = ["course_id", "rule_type", "catalog_year_id", "raw_text", "source_url", "machine_checkable",
                   "parse_status", "review_flags", "source_metadata", "content_sha256"]
        values = [payload[c] for c in columns]
        for i, name in enumerate(columns):
            if name in {"review_flags", "source_metadata"}:
                if self.dialect == "sqlite":
                    values[i] = json.dumps(values[i], sort_keys=True)
                else:
                    from psycopg.types.json import Jsonb
                    values[i] = Jsonb(values[i])
        assignments = ",".join(f"{c}=excluded.{c}" for c in columns[3:])
        row = self.execute(f"INSERT INTO courserule ({','.join(columns)}) VALUES ({','.join(['%s'] * len(columns))}) "
                           f"ON CONFLICT(course_id,rule_type,catalog_year_id) DO UPDATE SET {assignments} RETURNING rule_id", values).fetchone()
        rule_id = row[0]
        # Explicit deletes work regardless of the existing FK's ON DELETE behavior.
        self.execute("DELETE FROM rulecourse WHERE clause_id IN (SELECT clause_id FROM ruleclause WHERE rule_id=%s)", (rule_id,))
        self.execute("DELETE FROM ruleclause WHERE rule_id=%s", (rule_id,))
        for number, atoms in enumerate(payload["clauses"], 1):
            clause_id = self.execute("INSERT INTO ruleclause(rule_id,clause_number,condition_text) VALUES(%s,%s,NULL) RETURNING clause_id", (rule_id, number)).fetchone()[0]
            for atom in atoms:
                self.execute("INSERT INTO rulecourse(clause_id,required_course_id,minimum_grade,concurrent_allowed) VALUES(%s,%s,%s,%s)",
                             (clause_id, atom["required_course_id"], atom["minimum_grade"], atom["concurrent_allowed"]))


def seed(store: RuleStore, artifact: dict, *, apply: bool = False) -> dict:
    with store.transaction(write=apply):
        plan = build_plan(artifact, store.snapshot())
        if apply:
            if plan["errors"]:
                raise ValueError("Unmapped targets/catalog years block the entire transaction")
            for operation in plan["operations"]:
                if operation["action"] != "skip":
                    store.replace(operation["payload"])
            verify = build_plan(artifact, store.snapshot())
            if verify["errors"] or verify["counts"]["insert"] or verify["counts"]["update"]:
                raise ValueError("Post-write verification failed; transaction rolled back")
        return plan


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Offline validation (default)")
    mode.add_argument("--db-check", action="store_true", help="Explicit read-only database plan")
    mode.add_argument("--apply", action="store_true", help="Explicit atomic database write")
    mode.add_argument("--api-check", action="store_true", help="Read-only Supabase RPC database plan (requires migration 002)")
    mode.add_argument("--api-apply", action="store_true", help="Explicit atomic Supabase RPC import")
    parser.add_argument("--approve-reviewed-input", help="SHA256 of the exact reviewed input file; required with either apply mode")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--metadata", type=Path, help="Exact raw prerequisite CSV; required for database modes")
    parser.add_argument("--snapshot-manifest", type=Path, help="Reviewed source provenance; required for database modes")
    parser.add_argument("--approve-reviewed-snapshot", help="Manifest SHA256 acknowledgment; required with either apply mode")
    args = parser.parse_args(argv)
    writing = args.apply or args.api_apply
    try:
        import csv
        raw = args.input.read_bytes()
        artifact = json.loads(raw)
        with args.catalog.open(encoding="utf-8-sig", newline="") as handle:
            codes = {canonical_code(r["course_code"]) for r in csv.DictReader(handle)}
        validate_artifact(artifact, codes)
        file_hash = hashlib.sha256(raw).hexdigest()
        if writing and args.approve_reviewed_input != file_hash:
            raise ValueError("Apply requires --approve-reviewed-input matching the reviewed file SHA256")
        if writing or args.db_check or args.api_check:
            if not args.snapshot_manifest or not args.metadata:
                raise ValueError("Database modes require --metadata and --snapshot-manifest with reviewed provenance")
            manifest_bytes = args.snapshot_manifest.read_bytes()
            validate_snapshot_manifest(json.loads(manifest_bytes), args.catalog.read_bytes(),
                                       args.metadata.read_bytes(), artifact)
            manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
            if writing and args.approve_reviewed_snapshot != manifest_hash:
                raise ValueError("Apply requires --approve-reviewed-snapshot matching the reviewed manifest SHA256")
            if args.api_check or args.api_apply:
                from backend.scripts.prerequisite_supabase import create_api_client, seed_api
                plan = seed_api(create_api_client(), artifact, apply=args.api_apply,
                                input_hash=file_hash, snapshot_hash=manifest_hash,
                                approved_input=args.approve_reviewed_input,
                                approved_snapshot=args.approve_reviewed_snapshot)
            else:
                import psycopg
                dsn = os.environ.get("PREREQUISITE_DATABASE_DSN")
                if not dsn:
                    raise ValueError("Explicit database mode requires PREREQUISITE_DATABASE_DSN in process environment")
                with psycopg.connect(dsn, autocommit=True) as connection:
                    plan = seed(RuleStore(connection), artifact, apply=args.apply)
            result = {"mode": ("API_APPLIED" if args.api_apply else "READ_ONLY_API_PLAN") if
                      (args.api_check or args.api_apply) else ("APPLIED" if args.apply else "READ_ONLY_DATABASE_PLAN"),
                      **{k: v for k, v in plan.items() if k != "operations"}}
            result["rule_changes"] = [{**{k: op[k] for k in ["action", "course_code", "catalog_year", "previous_summary"]},
                                       "incoming": {k: val for k, val in op["payload"].items() if k != "source_metadata"}}
                                      for op in plan["operations"] if op["action"] != "skip"]
            result["snapshot_manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
        else:
            result = {"mode": "OFFLINE_VALIDATION_NO_CONNECTION", "summary": artifact["summary"],
                      "catalog_provenance": "NOT_VERIFIED_BY_OFFLINE_PARSE"}
            if args.snapshot_manifest:
                if not args.metadata:
                    raise ValueError("Snapshot verification requires --metadata")
                manifest_bytes = args.snapshot_manifest.read_bytes()
                validate_snapshot_manifest(json.loads(manifest_bytes), args.catalog.read_bytes(),
                                           args.metadata.read_bytes(), artifact)
                result["catalog_provenance"] = "VERIFIED_REVIEWED_SNAPSHOT_MANIFEST"
                result["snapshot_manifest_sha256"] = hashlib.sha256(manifest_bytes).hexdigest()
        result["input_sha256"] = file_hash
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 1 if result.get("errors") else 0
    except Exception as error:
        # Connection exceptions may include DSN credentials. Never echo them.
        if isinstance(error, (ValueError, OSError, KeyError, ImportError)):
            print(f"Validation failed: {type(error).__name__}", file=sys.stderr)
            if isinstance(error, ValueError):
                print(str(error), file=sys.stderr)
            elif isinstance(error, ImportError):
                print("Database modes require requirements-prerequisites.txt; offline validation needs no driver.", file=sys.stderr)
        else:
            print(f"Database operation failed ({type(error).__name__}). A transport failure can leave the commit outcome unknown; run a read-only check before retrying. Inspect schema and connection settings securely.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
