"""Evidence-based staging reconstruction, NOT an authoritative pg_dump restore.

Uses only the generic runner's disposable loopback cluster lifecycle. No remote
DSN is accepted. Requires local PostgreSQL tools and psycopg.
"""
import hashlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from unittest.mock import patch

from backend.tests import run_prerequisite_postgres_tests as harness
from backend.tests.test_prerequisite_pipeline import CATALOG, CODES, RAW, ROOT, row
from backend.scripts.parse_prerequisites import parse_rows
from backend.scripts.seed_prerequisites import RuleStore, seed, main as cli


CONSTRAINTS = {
 'catalogyear': [('catalogyear_label_unique','UNIQUE(catalog_label)'), ('catalogyear_valid_years','CHECK(end_year>=start_year)')],
 'course': [('course_code_unique','UNIQUE(course_code)'), ('course_subject_number_unique','UNIQUE(subject_code,course_number)'),
            ('course_department_fkey','FOREIGN KEY(department_id) REFERENCES department ON DELETE SET NULL'),
            ('course_max_credits_valid','CHECK(max_credit_hours>=min_credit_hours)'), ('course_min_credits_nonnegative','CHECK(min_credit_hours>=0)')],
 'courserule': [('courserule_course_fkey','FOREIGN KEY(course_id) REFERENCES course ON DELETE CASCADE'),
                ('courserule_course_type_unique','UNIQUE(course_id,rule_type)'), ('courserule_type_check',"CHECK(rule_type IN ('PREREQUISITE','COREQUISITE'))")],
 'ruleclause': [('ruleclause_rule_fkey','FOREIGN KEY(rule_id) REFERENCES courserule ON DELETE CASCADE'),
                ('ruleclause_number_positive','CHECK(clause_number>0)'), ('ruleclause_unique','UNIQUE(rule_id,clause_number)')],
 'rulecourse': [('rulecourse_clause_fkey','FOREIGN KEY(clause_id) REFERENCES ruleclause ON DELETE CASCADE'),
                ('rulecourse_required_course_fkey','FOREIGN KEY(required_course_id) REFERENCES course ON DELETE CASCADE'),
                ('rulecourse_unique','UNIQUE(clause_id,required_course_id)')],
}
INDEXES = [('course','department_id','idx_course_department'),('course','course_number','idx_course_number'),
           ('course','subject_code','idx_course_subject'),('courserule','course_id','idx_course_rule_course'),
           ('ruleclause','rule_id','idx_rule_clause_rule'),('rulecourse','clause_id','idx_rule_course_clause'),
           ('rulecourse','required_course_id','idx_rule_course_required_course')]


class ReconstructedTests(unittest.TestCase):
    dsn = None

    def setUp(self):
        import psycopg
        self.admin = psycopg.connect(self.dsn, autocommit=True)
        self.addCleanup(self.admin.close)
        for name, attrs in [('postgres',"LOGIN BYPASSRLS NOSUPERUSER PASSWORD 'ephemeral-local-test'"),('service_role','NOLOGIN BYPASSRLS NOSUPERUSER'),
                            ('anon','NOLOGIN'),('authenticated','NOLOGIN')]:
            if not self.admin.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',(name,)).fetchone():
                self.admin.execute(f'CREATE ROLE {name} {attrs}')
        self.admin.execute('DROP SCHEMA public CASCADE; CREATE SCHEMA public AUTHORIZATION postgres')
        self.admin.execute('SET ROLE postgres')
        self.admin.execute((ROOT/'backend/tests/fixtures/supabase_prerequisite_reconstructed.sql').read_text())
        for table, constraints in CONSTRAINTS.items():
            for name, definition in constraints:
                self.admin.execute(f'ALTER TABLE public.{table} ADD CONSTRAINT {name} {definition}')
        for table, column, name in INDEXES:
            self.admin.execute(f'CREATE INDEX {name} ON public.{table}({column})')
        for table in CONSTRAINTS:
            self.admin.execute(f'ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY')
            self.admin.execute(f'GRANT ALL ON public.{table} TO service_role')
            self.admin.execute(f'GRANT SELECT ON public.{table} TO authenticated')
            if table != 'course':
                self.admin.execute(f'GRANT SELECT ON public.{table} TO anon')
        self.admin.execute('GRANT USAGE ON SCHEMA public TO service_role,anon,authenticated')
        self.admin.execute('CREATE POLICY "Authenticated users can view courses" ON public.course FOR SELECT TO authenticated USING(true)')
        self.admin.execute('GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO service_role')
        columns = ['course_code','subject_code','course_number','course_title','description','min_credit_hours','max_credit_hours','active_status','source_url']
        with self.admin.cursor() as cur:
            cur.executemany('INSERT INTO public.course('+','.join(columns)+') VALUES('+','.join(['%s']*len(columns))+')',
                [[(r[c].lower()=='true' if c=='active_status' else r[c]) for c in columns] for r in CATALOG])
        self.admin.execute("INSERT INTO public.catalogyear(catalog_label,start_year,end_year,active_status) VALUES('2026-2027',2026,2027,true)")
        self.admin.execute('RESET ROLE')
        self.local_dsn = psycopg.conninfo.make_conninfo(self.dsn, user='postgres')
        self.connection = psycopg.connect(self.local_dsn, autocommit=True)
        self.addCleanup(self.connection.close)
        self.store = RuleStore(self.connection)
        self.artifact = parse_rows(RAW,CODES)

    def tearDown(self):
        self.connection.close()
        self.admin.close()

    def migrate(self):
        self.connection.execute((ROOT/'backend/migrations/001_course_rules_catalog_year.sql').read_text())

    def test_evidence_configuration_and_migration_preservation(self):
        self.assertEqual(self.connection.execute('SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user').fetchone(),(False,True))
        before = self.connection.execute("SELECT c.relname,c.relowner,c.relrowsecurity,c.relforcerowsecurity,c.relacl FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname IN ('course','catalogyear','courserule','ruleclause','rulecourse') ORDER BY c.relname").fetchall()
        identities = self.connection.execute("SELECT table_name,identity_generation FROM information_schema.columns WHERE table_schema='public' AND is_identity='YES' ORDER BY table_name").fetchall()
        self.assertEqual(len(identities),6)
        self.assertTrue(all(x[1]=='ALWAYS' for x in identities))
        self.migrate()
        self.assertEqual(before,self.connection.execute("SELECT c.relname,c.relowner,c.relrowsecurity,c.relforcerowsecurity,c.relacl FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname IN ('course','catalogyear','courserule','ruleclause','rulecourse') ORDER BY c.relname").fetchall())
        self.assertEqual(identities,self.connection.execute("SELECT table_name,identity_generation FROM information_schema.columns WHERE table_schema='public' AND is_identity='YES' ORDER BY table_name").fetchall())

    def test_actual_cli_full_import_dry_run_idempotency_and_unresolved(self):
        self.migrate()
        base = ROOT/'backend/scripts'
        parsed=base/'data/prerequisites/parsed_all_v2.json'
        manifest=base/'data/prerequisites/investigation/snapshot_manifest.json'
        args=['--input',str(parsed),'--catalog',str(base/'courses.csv'),'--metadata',str(base/'catalog_prerequisites.csv'),'--snapshot-manifest',str(manifest)]
        def invoke(extra):
            with patch.dict(os.environ,{'PREREQUISITE_DATABASE_DSN':self.local_dsn}), redirect_stdout(io.StringIO()) as out, redirect_stderr(io.StringIO()) as err:
                status=cli(args+extra)
            self.assertEqual(status,0,err.getvalue())
            return json.loads(out.getvalue())
        self.assertEqual(invoke(['--dry-run'])['mode'],'OFFLINE_VALIDATION_NO_CONNECTION')
        self.assertEqual(invoke(['--db-check'])['counts']['insert'],2043)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM courserule').fetchone()[0],0)
        apply=['--apply','--approve-reviewed-input',hashlib.sha256(parsed.read_bytes()).hexdigest(),'--approve-reviewed-snapshot',hashlib.sha256(manifest.read_bytes()).hexdigest()]
        self.assertEqual(invoke(apply)['counts']['insert'],2043)
        ids=self.connection.execute('SELECT rule_course_id FROM rulecourse ORDER BY rule_course_id').fetchall()
        self.assertEqual(invoke(apply)['counts']['skip'],2043)
        self.assertEqual(ids,self.connection.execute('SELECT rule_course_id FROM rulecourse ORDER BY rule_course_id').fetchall())
        self.assertEqual(self.connection.execute('SELECT count(*) FROM courserule WHERE NOT machine_checkable').fetchone()[0],650)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM ruleclause JOIN courserule USING(rule_id) WHERE NOT machine_checkable').fetchone()[0],0)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM ruleclause').fetchone()[0],2655)
        self.assertEqual(len(ids),3535)
        self.assertEqual(seed(self.store,self.artifact)['counts']['skip'],2043)
        self.assertEqual(self.connection.execute('SELECT DISTINCT catalog_year_id FROM courserule').fetchall(),[(1,)])

    def test_seed_failure_and_year_separation(self):
        self.migrate()
        original=self.store.replace
        def fail(payload):
            original(payload)
            raise RuntimeError('injected')
        with patch.object(self.store,'replace',side_effect=fail), self.assertRaises(RuntimeError):
            seed(self.store,parse_rows([row()],CODES),apply=True)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM courserule').fetchone()[0],0)
        self.connection.execute("INSERT INTO catalogyear(catalog_label,start_year,end_year) VALUES('2027-2028',2027,2028)")
        seed(self.store,parse_rows([row()],CODES),apply=True)
        seed(self.store,parse_rows([row(year='2027-2028')],CODES),apply=True)
        self.assertEqual(self.connection.execute('SELECT count(DISTINCT catalog_year_id) FROM courserule').fetchone()[0],2)

    def test_migration_failure_rolls_back_ddl(self):
        import psycopg
        self.connection.execute('CREATE INDEX courserule_year_status_idx ON courserule(course_id)')
        with self.assertRaises(psycopg.errors.DuplicateTable):
            self.migrate()
        self.connection.execute('ROLLBACK')
        self.assertEqual(self.connection.execute("SELECT count(*) FROM information_schema.columns WHERE table_schema='public' AND table_name='courserule' AND column_name='catalog_year_id'").fetchone()[0],0)
        self.assertEqual(self.connection.execute("SELECT count(*) FROM pg_constraint WHERE conname='courserule_course_type_unique'").fetchone()[0],1)

    def test_rls_policy_visibility_and_import_role(self):
        self.migrate()
        seed(self.store,parse_rows([row()],CODES),apply=True)
        self.admin.execute('SET ROLE authenticated')
        try:
            self.assertEqual(self.admin.execute('SELECT count(*) FROM public.course').fetchone()[0],6692)
            self.assertEqual(self.admin.execute('SELECT count(*) FROM public.courserule').fetchone()[0],0)
        finally:
            self.admin.execute('RESET ROLE')
        self.assertEqual(self.connection.execute('SELECT count(*) FROM public.courserule').fetchone()[0],1)


if __name__=='__main__':
    harness.PostgreSQLTests=ReconstructedTests
    raise SystemExit(harness.main())
