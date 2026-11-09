"""Supabase transport/CLI tests with fake clients; no network or credentials."""
import copy
import hashlib
import io
import json
import os
import types
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import Mock, patch

from backend.scripts.prerequisite_supabase import create_api_client, seed_api
from backend.scripts.seed_prerequisites import main
from backend.tests.test_prerequisite_pipeline import SCRIPTS, CODES, row
from backend.scripts.parse_prerequisites import parse_rows


class SupabaseTests(unittest.TestCase):
    def setUp(self):
        self.artifact = parse_rows([row()], CODES)
        self.snapshot = {'courses': [{'course_id': 1, 'course_code': 'CS 210'},
                                     {'course_id': 2, 'course_code': 'CS 110'}],
                         'catalog_years': [{'catalog_year_id': 7, 'catalog_label': '2026-2027'}], 'rules': []}
        self.client = Mock()
        self.client.rpc.return_value.execute.return_value.data = self.snapshot

    def test_read_only_resolves_ids_without_import(self):
        plan = seed_api(self.client, self.artifact)
        self.assertEqual(plan['counts']['insert'], 1)
        self.assertEqual(plan['operations'][0]['payload']['catalog_year_id'], 7)
        self.client.rpc.assert_called_once_with('prerequisite_snapshot_v1', {})
        self.client.table.assert_not_called()

    def test_missing_reference_downgrades_and_missing_target_year_errors(self):
        self.snapshot['courses'].pop()
        plan = seed_api(self.client, self.artifact)
        self.assertEqual(len(plan['missing_references']), 1)
        self.assertFalse(plan['operations'][0]['payload']['machine_checkable'])
        self.assertEqual(plan['operations'][0]['payload']['clauses'], [])
        self.snapshot['catalog_years'] = []
        self.assertTrue(seed_api(self.client, self.artifact)['errors'])
        with self.assertRaisesRegex(ValueError, 'Unmapped'):
            seed_api(self.client, self.artifact, apply=True, input_hash='a'*64, snapshot_hash='b'*64,
                     approved_input='a'*64, approved_snapshot='b'*64)
        self.assertTrue(all(c.args[0] == 'prerequisite_snapshot_v1' for c in self.client.rpc.call_args_list))

    def test_apply_exact_one_rpc_and_approval_gate_before_network(self):
        with self.assertRaises(ValueError):
            seed_api(self.client, self.artifact, apply=True)
        self.client.rpc.assert_not_called()
        self.client.rpc.return_value.execute.side_effect = [types.SimpleNamespace(data=self.snapshot),
            types.SimpleNamespace(data={'insert': 1, 'update': 0, 'skip': 0})]
        seed_api(self.client, self.artifact, apply=True, input_hash='a'*64, snapshot_hash='b'*64,
                 approved_input='a'*64, approved_snapshot='b'*64)
        self.assertEqual(self.client.rpc.call_count, 2)
        name, params = self.client.rpc.call_args.args
        self.assertEqual(name, 'prerequisite_import_v1')
        self.assertEqual(params['p_expected_snapshot'], self.snapshot)
        self.assertEqual(params['p_approved_input_sha256'], 'a'*64)
        self.client.table.assert_not_called()

    def test_idempotent_and_unresolved_plan(self):
        payload = seed_api(self.client, self.artifact)['operations'][0]['payload']
        self.snapshot['rules'] = [payload]
        self.assertEqual(seed_api(self.client, self.artifact)['counts']['skip'], 1)
        unresolved = parse_rows([row(text='Consent of instructor.')], CODES)
        rule = seed_api(self.client, unresolved)['operations'][0]['payload']
        self.assertFalse(rule['machine_checkable'])
        self.assertEqual(rule['clauses'], [])
        self.assertEqual(rule['source_metadata']['course'], unresolved['courses'][0])

    def test_bad_snapshot_and_transport_errors_do_not_leak(self):
        self.client.rpc.return_value.execute.return_value.data = []
        with self.assertRaises(ValueError):
            seed_api(self.client, self.artifact)
        self.client.rpc.side_effect = ValueError('fake-secret-must-not-be-printed')
        with self.assertRaises(RuntimeError) as error:
            seed_api(self.client, self.artifact)
        self.assertNotIn('fake-secret', str(error.exception))

    def test_client_key_precedence_and_no_dotenv(self):
        sdk = types.SimpleNamespace(create_client=Mock(), ClientOptions=Mock())
        for secret, expected in [('test-secret', 'test-secret'), ('', 'test-service')]:
            with patch.dict(os.environ, {'SUPABASE_URL': 'https://example.invalid',
                    'SUPABASE_SECRET_KEY': secret, 'SUPABASE_SERVICE_ROLE_KEY': 'test-service'}, clear=True), \
                    patch.dict('sys.modules', {'supabase': sdk}):
                create_api_client()
                self.assertEqual(sdk.create_client.call_args.args, ('https://example.invalid', expected))
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ValueError):
            create_api_client()

    def test_cli_provenance_and_approval_gates_before_client(self):
        parsed = SCRIPTS/'data/prerequisites/parsed_all_v2.json'
        manifest = SCRIPTS/'data/prerequisites/investigation/snapshot_manifest.json'
        base = ['--input', str(parsed), '--catalog', str(SCRIPTS/'courses.csv')]
        provenance = ['--metadata', str(SCRIPTS/'catalog_prerequisites.csv'), '--snapshot-manifest', str(manifest)]
        with patch('backend.scripts.prerequisite_supabase.create_api_client') as create:
            for extra in [['--api-check'], ['--api-apply']+provenance,
                          ['--api-apply']+provenance+['--approve-reviewed-input', hashlib.sha256(parsed.read_bytes()).hexdigest(),
                                                    '--approve-reviewed-snapshot', '0'*64]]:
                with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                    self.assertEqual(main(base+extra), 1)
                create.assert_not_called()
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(base+['--dry-run']), 0)
            create.assert_not_called()
            self.client.rpc.return_value.execute.return_value.data = {
                'courses': [{'course_id': i, 'course_code': c} for i,c in enumerate(sorted(CODES),1)],
                'catalog_years': self.snapshot['catalog_years'], 'rules': []}
            create.return_value = self.client
            with redirect_stdout(io.StringIO()) as output, redirect_stderr(io.StringIO()):
                self.assertEqual(main(base+provenance+['--api-check']), 0)
            self.assertEqual(json.loads(output.getvalue())['mode'], 'READ_ONLY_API_PLAN')
            self.client.rpc.assert_called_once_with('prerequisite_snapshot_v1', {})
