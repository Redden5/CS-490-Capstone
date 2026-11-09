"""Create a disposable loopback-only PostgreSQL cluster and test the real migration.

Requires initdb/pg_ctl on PATH and optional requirements-prerequisites.txt.
Never reads any environment database URL or existing PostgreSQL data directory.
Run: python -m backend.tests.run_prerequisite_postgres_tests
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest

from backend.scripts.seed_prerequisites import RuleStore, seed
from backend.scripts.parse_prerequisites import parse_rows
from backend.tests.test_prerequisite_pipeline import CODES, RAW, ROOT, row


ORIGINAL_SCHEMA = """
CREATE TABLE course(course_id bigint PRIMARY KEY, course_code varchar UNIQUE NOT NULL);
CREATE TABLE catalogyear(catalog_year_id bigint PRIMARY KEY,catalog_label varchar,start_year integer,end_year integer,active_status boolean);
CREATE TABLE courserule(rule_id bigint PRIMARY KEY,course_id bigint REFERENCES course,
 rule_type varchar CHECK(rule_type IN ('PREREQUISITE','COREQUISITE')),raw_text text,machine_checkable boolean,
 CONSTRAINT original_rule_unique UNIQUE(course_id,rule_type));
CREATE TABLE ruleclause(clause_id bigint PRIMARY KEY,rule_id bigint REFERENCES courserule,
 clause_number integer CHECK(clause_number>0),condition_text text,UNIQUE(rule_id,clause_number));
CREATE TABLE rulecourse(rule_course_id bigint PRIMARY KEY,clause_id bigint REFERENCES ruleclause,
 required_course_id bigint REFERENCES course,minimum_grade varchar,concurrent_allowed boolean,
 UNIQUE(clause_id,required_course_id));
"""


class PostgreSQLTests(unittest.TestCase):
    dsn = None

    def setUp(self):
        import psycopg
        self.connection = psycopg.connect(self.dsn, autocommit=True)
        self.connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        self.connection.execute(ORIGINAL_SCHEMA)
        with self.connection.cursor() as cursor:
            cursor.executemany("INSERT INTO course VALUES(%s,%s)", enumerate(sorted(CODES), 1))
        self.connection.execute("INSERT INTO catalogyear VALUES(42,'2026-2027',2026,2027,true),(86,'2027-2028',2027,2028,false)")
        self.connection.execute((ROOT / "backend/migrations/001_course_rules_catalog_year.sql").read_text(encoding="utf-8"))
        self.store = RuleStore(self.connection)

    def tearDown(self):
        self.connection.close()

    def test_full_catalog_actual_postgresql_roundtrip(self):
        artifact = parse_rows(RAW, CODES)
        plan = seed(self.store, artifact, apply=True)
        self.assertEqual(plan["counts"]["insert"], 2043)
        again = seed(self.store, artifact, apply=True)
        self.assertEqual(again["counts"]["skip"], 2043)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM courserule WHERE NOT machine_checkable").fetchone()[0], 650)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM ruleclause c JOIN courserule r USING(rule_id) WHERE NOT r.machine_checkable").fetchone()[0], 0)

    def test_actual_transaction_rollback(self):
        artifact = parse_rows([row(text="CS 110")], CODES)
        original = self.store.replace
        def injected_failure(payload):
            original(payload)
            raise RuntimeError("rollback test")
        self.store.replace = injected_failure
        with self.assertRaises(RuntimeError):
            seed(self.store, artifact, apply=True)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM courserule").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM ruleclause").fetchone()[0], 0)

    def test_failed_migration_rolls_back_schema_changes(self):
        import psycopg
        self.connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        self.connection.execute(ORIGINAL_SCHEMA)
        # Force failure after column/constraint changes, before COMMIT.
        self.connection.execute("CREATE INDEX courserule_year_status_idx ON courserule(course_id)")
        with self.assertRaises(psycopg.errors.DuplicateTable):
            self.connection.execute((ROOT / "backend/migrations/001_course_rules_catalog_year.sql").read_text(encoding="utf-8"))
        self.connection.execute("ROLLBACK")
        self.assertEqual(self.connection.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='courserule' AND column_name='catalog_year_id'").fetchone()[0], 0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM pg_constraint WHERE conrelid='public.courserule'::regclass AND conname='original_rule_unique'").fetchone()[0], 1)

    def test_actual_rls_filtered_role_is_rejected(self):
        self.connection.execute("CREATE ROLE prerequisite_limited")
        self.connection.execute("GRANT USAGE ON SCHEMA public TO prerequisite_limited")
        self.connection.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO prerequisite_limited")
        self.connection.execute("ALTER TABLE course ENABLE ROW LEVEL SECURITY")
        self.connection.execute("CREATE POLICY hide_courses ON course FOR SELECT TO prerequisite_limited USING(false)")
        self.connection.execute("SET ROLE prerequisite_limited")
        try:
            with self.assertRaisesRegex(ValueError, "unfiltered RLS"):
                seed(self.store, parse_rows([row()], CODES))
        finally:
            self.connection.execute("RESET ROLE")

    def test_database_enforces_read_only_transaction(self):
        import psycopg
        with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):
            with self.store.transaction():
                self.connection.execute("UPDATE course SET course_code=course_code")

    def test_year_separation_and_concurrent_idempotency(self):
        import psycopg
        artifact = parse_rows([row(text="CS 110 and (CS 120 or CS 215)")], CODES)
        def apply_one(_):
            with psycopg.connect(self.dsn, autocommit=True) as connection:
                return seed(RuleStore(connection), artifact, apply=True)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(apply_one, range(2)))
        self.assertEqual(sum(r["counts"]["insert"] for r in results), 1)
        self.assertEqual(sum(r["counts"]["skip"] for r in results), 1)
        seed(self.store, parse_rows([row(text="CS 120", year="2027-2028")], CODES), apply=True)
        self.assertEqual(self.connection.execute("SELECT count(DISTINCT catalog_year_id) FROM courserule").fetchone()[0], 2)

    def test_migration_preserves_legacy_rows_and_initializes_identity(self):
        # Repeat setup with a preexisting undated rule/children and nonstandard constraint name.
        self.connection.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        self.connection.execute(ORIGINAL_SCHEMA)
        self.connection.execute("INSERT INTO course VALUES(1,'CS 210'),(2,'CS 110')")
        self.connection.execute("INSERT INTO catalogyear VALUES(42,'2026-2027',2026,2027,true)")
        self.connection.execute("INSERT INTO courserule VALUES(100,1,'PREREQUISITE','legacy source',true)")
        self.connection.execute("INSERT INTO ruleclause VALUES(200,100,1,NULL)")
        self.connection.execute("INSERT INTO rulecourse VALUES(300,200,2,'C',false)")
        self.connection.execute((ROOT / "backend/migrations/001_course_rules_catalog_year.sql").read_text(encoding="utf-8"))
        plan = seed(self.store, parse_rows([row(text="CS 110")], CODES), apply=True)
        self.assertEqual(plan["counts"]["insert"], 1)
        legacy = self.connection.execute("SELECT raw_text,parse_status,catalog_year_id FROM courserule WHERE rule_id=100").fetchone()
        self.assertEqual(legacy, ("legacy source", "NEEDS_REVIEW", None))
        self.assertEqual(self.connection.execute("SELECT minimum_grade FROM rulecourse WHERE rule_course_id=300").fetchone()[0], "C")
        self.assertGreater(self.connection.execute("SELECT max(rule_id) FROM courserule").fetchone()[0], 100)


def main() -> int:
    import psycopg
    commands = {name: shutil.which(name) for name in ["initdb", "pg_ctl"]}
    if not all(commands.values()):
        raise RuntimeError("Local initdb and pg_ctl are required; no remote database fallback is permitted")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    def run(command):
        # Daemon grandchildren must not inherit output pipes and hold communicate() open.
        return subprocess.run(command, check=True, creationflags=flags, timeout=90)
    # Bind only loopback, use a fresh OS-selected port and fresh temporary data.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="capstone-prerequisite-pg-") as folder:
        # Verify the recursive temporary cleanup target before creating a cluster.
        if not Path(folder).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
            raise RuntimeError("Temporary cluster escaped the system temporary directory")
        data = str(Path(folder) / "data")
        print("Initializing disposable local PostgreSQL cluster...", flush=True)
        run([commands["initdb"], "-D", data, "-U", "pipeline_test", "--auth=trust", "--encoding=UTF8", "--no-locale"])
        started = False
        try:
            print("Starting loopback-only test server...", flush=True)
            run([commands["pg_ctl"], "-D", data, "-l", str(Path(folder) / "server.log"),
                 "-o", f"-h 127.0.0.1 -p {port}", "-w", "start"])
            started = True
            dsn = psycopg.conninfo.make_conninfo(host="127.0.0.1", port=port, user="pipeline_test",
                    password="ephemeral-local-test", passfile=str(Path(folder) / "no-credentials.pgpass"),
                    dbname="postgres", sslmode="disable", gssencmode="disable")
            with psycopg.connect(dsn, autocommit=True) as connection:
                connection.execute("CREATE DATABASE capstone_prerequisite_test")
            PostgreSQLTests.dsn = dsn.replace("dbname=postgres", "dbname=capstone_prerequisite_test")
            result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PostgreSQLTests))
            return 0 if result.wasSuccessful() else 1
        finally:
            if started:
                run([commands["pg_ctl"], "-D", data, "-m", "immediate", "-w", "stop"])


if __name__ == "__main__":
    raise SystemExit(main())
