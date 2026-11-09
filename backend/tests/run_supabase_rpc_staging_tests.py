"""Real SQL RPC tests using the manually reconstructed disposable schema.

The client adapter below exercises the same RPC parameter contract over local
psycopg. It does NOT claim to test Supabase's hosted HTTP gateway/auth mapping.
Run through run_docker_staging_tests --include-rpc; never accepts a remote DSN.
"""
import copy
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from backend.tests.run_reconstructed_staging_tests import ReconstructedTests
from backend.tests.test_prerequisite_pipeline import ROOT, CODES, row
from backend.scripts.parse_prerequisites import parse_rows
from backend.scripts.seed_prerequisites import build_plan, seed
from backend.scripts.prerequisite_supabase import seed_api


class SQLClient:
    """Local DB adapter for the RPC protocol; only whitelisted function calls."""
    def __init__(self, connection):
        self.connection = connection

    def rpc(self, name, params):
        from psycopg.types.json import Jsonb
        if name == 'prerequisite_snapshot_v1':
            sql, values = 'SELECT public.prerequisite_snapshot_v1()', ()
        elif name == 'prerequisite_import_v1':
            names = ['p_expected_snapshot','p_rules','p_input_sha256','p_snapshot_sha256',
                     'p_approved_input_sha256','p_approved_snapshot_sha256']
            sql = 'SELECT public.prerequisite_import_v1(%s,%s,%s,%s,%s,%s)'
            values = [Jsonb(params[k]) if k in names[:2] else params[k] for k in names]
        else:
            raise AssertionError('Unexpected RPC')
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=self.connection.execute(sql, values).fetchone()[0]))


class RPCStagingTests(unittest.TestCase):
    dsn = None
    setUp = ReconstructedTests.setUp
    tearDown = ReconstructedTests.tearDown
    migrate = ReconstructedTests.migrate

    def install(self):
        self.migrate()
        inventory = """SELECT c.relname,c.relowner,c.relrowsecurity,c.relforcerowsecurity,c.relacl,
            a.attname,a.attidentity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid WHERE n.nspname='public' AND c.relkind='r'
            AND a.attnum>0 ORDER BY c.relname,a.attnum"""
        before = self.connection.execute(inventory).fetchall()
        self.connection.execute((ROOT/'backend/migrations/002_prerequisite_supabase_rpc.sql').read_text())
        self.assertEqual(before,self.connection.execute(inventory).fetchall())
        # Disposable harness only: emulate PostgREST's role switching. These
        # memberships are not inferred from the user's schema evidence and are
        # NOT granted by migration 002. INHERIT FALSE keeps privilege tests real.
        self.admin.execute('GRANT service_role,anon,authenticated TO postgres WITH INHERIT FALSE, SET TRUE')
        self.connection.execute('SET ROLE service_role')
        self.client = SQLClient(self.connection)

    def params(self, artifact=None):
        snapshot = self.client.rpc('prerequisite_snapshot_v1', {}).execute().data
        plan = build_plan(artifact or parse_rows([row()], CODES), snapshot)
        return {'p_expected_snapshot': snapshot, 'p_rules': [op['payload'] for op in plan['operations']],
                'p_input_sha256': 'a'*64, 'p_snapshot_sha256': 'b'*64,
                'p_approved_input_sha256': 'a'*64, 'p_approved_snapshot_sha256': 'b'*64}

    def call(self, params):
        return self.client.rpc('prerequisite_import_v1', params).execute().data

    def test_rpc_full_import_idempotency_and_direct_store_equivalence(self):
        self.install()
        with self.connection.transaction():
            self.connection.execute('SET TRANSACTION READ ONLY')
            plan = seed_api(self.client, self.artifact)
            self.assertEqual(plan['counts']['insert'], 2043)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM courserule').fetchone()[0], 0)
        options = dict(apply=True, input_hash='a'*64, snapshot_hash='b'*64, approved_input='a'*64, approved_snapshot='b'*64)
        self.assertEqual(seed_api(self.client, self.artifact, **options)['counts']['insert'], 2043)
        ids = self.connection.execute('SELECT rule_course_id FROM rulecourse ORDER BY rule_course_id').fetchall()
        self.assertEqual(seed_api(self.client, self.artifact, **options)['counts']['skip'], 2043)
        self.assertEqual(ids, self.connection.execute('SELECT rule_course_id FROM rulecourse ORDER BY rule_course_id').fetchall())
        self.assertEqual(seed(self.store, self.artifact)['counts']['skip'], 2043)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM courserule WHERE NOT machine_checkable').fetchone()[0], 650)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM ruleclause JOIN courserule USING(rule_id) WHERE NOT machine_checkable').fetchone()[0], 0)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM ruleclause').fetchone()[0], 2655)
        self.assertEqual(len(ids), 3535)

    def test_rpc_permissions_and_rollback_preserves_applied_migration_data(self):
        import psycopg
        self.install()
        self.call(self.params())
        before = self.store.snapshot()
        for role in ['anon', 'authenticated']:
            self.connection.execute('RESET ROLE')
            self.connection.execute('SET ROLE '+role)
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.client.rpc('prerequisite_snapshot_v1', {}).execute()
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                self.call(dict(p_expected_snapshot={},p_rules=[],p_input_sha256='a'*64,p_snapshot_sha256='b'*64,
                               p_approved_input_sha256='a'*64,p_approved_snapshot_sha256='b'*64))
        self.connection.execute('RESET ROLE')
        # Even accidental EXECUTE grants do not bypass the in-function role gate.
        self.connection.execute('GRANT EXECUTE ON FUNCTION public.prerequisite_snapshot_v1() TO authenticated')
        self.connection.execute('SET ROLE authenticated')
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            self.client.rpc('prerequisite_snapshot_v1', {}).execute()
        self.connection.execute('RESET ROLE')
        self.connection.execute((ROOT/'backend/migrations/002_prerequisite_supabase_rpc_rollback.sql').read_text())
        self.assertEqual(self.store.snapshot(), before)
        self.assertIsNone(self.connection.execute("SELECT to_regprocedure('public.prerequisite_snapshot_v1()')").fetchone()[0])
        self.assertEqual(self.connection.execute("SELECT count(*) FROM pg_constraint WHERE conname='courserule_course_type_year_key'").fetchone()[0], 1)
        self.connection.execute((ROOT/'backend/migrations/002_prerequisite_supabase_rpc.sql').read_text())
        self.connection.execute('SET ROLE service_role')
        self.assertEqual(self.call(self.params()), {'insert': 0,'update': 0,'skip': 1})

    def test_rpc_postwrite_verification_failure_and_missing_base_migration(self):
        import psycopg
        migration = (ROOT/'backend/migrations/002_prerequisite_supabase_rpc.sql').read_text()
        with self.assertRaises(psycopg.errors.UndefinedColumn):
            self.connection.execute(migration)
        self.connection.execute('ROLLBACK')
        self.assertIsNone(self.connection.execute("SELECT to_regprocedure('public.prerequisite_snapshot_v1()')").fetchone()[0])
        self.install()
        # A deliberately introduced LOCAL TEST trigger proves final validation
        # aborts the entire statement if stored rows differ from supplied rows.
        self.connection.execute('RESET ROLE')
        self.connection.execute("""CREATE FUNCTION public.rpc_test_drift() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN NEW.minimum_grade := 'F'; RETURN NEW; END $$;
            CREATE TRIGGER rpc_test_drift BEFORE INSERT ON public.rulecourse
            FOR EACH ROW EXECUTE FUNCTION public.rpc_test_drift()""")
        self.connection.execute('SET ROLE service_role')
        with self.assertRaisesRegex(psycopg.Error, 'Stored payload verification failed'):
            self.call(self.params())
        self.assertEqual(self.connection.execute('SELECT count(*) FROM courserule').fetchone()[0],0)
        self.assertEqual(self.connection.execute('SELECT count(*) FROM rulecourse').fetchone()[0],0)

    def test_rpc_failure_after_write_rolls_back_and_rejects_unsafe_payloads(self):
        import psycopg
        self.install()
        params = self.params()
        for mutate in [lambda p: p.update(p_approved_input_sha256=None),
                       lambda p: p['p_rules'].append(copy.deepcopy(p['p_rules'][0])),
                       lambda p: p['p_rules'][0].update(clauses=[[]]),
                       lambda p: p['p_rules'][0]['clauses'][0][0].update(required_course_id=999999999),
                       lambda p: p['p_rules'][0].update(machine_checkable=False,parse_status='NEEDS_REVIEW'),
                       lambda p: p['p_rules'][0]['source_metadata']['course'].update(complete_enrollment_conditions_machine_checkable=False)]:
            with self.subTest(mutate=mutate):
                bad = copy.deepcopy(params)
                mutate(bad)
                with self.assertRaises(psycopg.Error):
                    self.call(bad)
                self.assertEqual(self.connection.execute('SELECT count(*) FROM courserule').fetchone()[0], 0)
                self.assertEqual(self.connection.execute('SELECT count(*) FROM ruleclause').fetchone()[0], 0)
        self.call(params)
        before = self.store.snapshot()
        changed = self.params(parse_rows([row(text='CS 120')], CODES))
        changed['p_rules'][0]['clauses'][0][0]['required_course_id'] = 999999999
        with self.assertRaises(psycopg.Error):
            self.call(changed)
        self.assertEqual(self.store.snapshot(), before)

    def test_rpc_stale_snapshot_drift_years_and_concurrent_call(self):
        import psycopg
        self.install()
        stale = self.params()
        self.call(stale)
        with self.assertRaises(psycopg.errors.SerializationFailure):
            self.call(stale)
        self.connection.execute("UPDATE rulecourse SET minimum_grade='D'")
        self.assertEqual(self.call(self.params())['update'], 1)
        self.connection.execute("UPDATE ruleclause SET condition_text='drift',clause_number=8")
        self.assertEqual(self.call(self.params())['update'], 1)
        self.connection.execute("INSERT INTO catalogyear(catalog_label,start_year,end_year) VALUES('2027-2028',2027,2028)")
        self.call(self.params(parse_rows([row(year='2027-2028')], CODES)))
        self.assertEqual(self.connection.execute('SELECT count(DISTINCT catalog_year_id) FROM courserule').fetchone()[0], 2)
        params = self.params(parse_rows([row(text='CS 120')], CODES))
        def call_one(_):
            with psycopg.connect(self.local_dsn, autocommit=True) as connection:
                connection.execute('SET ROLE service_role')
                try:
                    return SQLClient(connection).rpc('prerequisite_import_v1',params).execute().data
                except psycopg.errors.SerializationFailure:
                    return 'stale'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(call_one, range(2)))
        self.assertEqual(results.count('stale'), 1)
        self.assertEqual([r for r in results if r != 'stale'], [{'insert':0,'update':1,'skip':0}])
        self.assertEqual(seed(self.store,parse_rows([row(year='2027-2028')], CODES))['counts']['skip'],1)
