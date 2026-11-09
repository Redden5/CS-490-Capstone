"""Real optional Supabase SDK, intercepted HTTP transport; never contacts a server.

python -m backend.tests.run_supabase_sdk_tests
Requires supabase from requirements-prerequisites.txt. Kept out of the
standard-library discovery suite so offline parser tests need no SDK.
"""
import json
import os
import unittest
from unittest.mock import patch

from backend.scripts.prerequisite_supabase import create_api_client, seed_api
from backend.scripts.parse_prerequisites import parse_rows
from backend.tests.test_prerequisite_pipeline import CODES, row


class SDKTests(unittest.TestCase):
    def test_real_sdk_rpc_serialization_for_both_key_environment_names(self):
        import httpx
        artifact = parse_rows([row()], CODES)
        snapshot = {'courses': [{'course_id': 1, 'course_code':'CS 210'}, {'course_id': 2,'course_code':'CS 110'}],
                    'catalog_years': [{'catalog_year_id': 7,'catalog_label':'2026-2027'}], 'rules': []}
        for envname, key in [('SUPABASE_SECRET_KEY','sb_secret_dummy_test_only'),
                             ('SUPABASE_SERVICE_ROLE_KEY','dummy-legacy-test-only')]:
            calls = []
            def handle(_transport, request):
                self.assertEqual(request.url.host, 'example.invalid')
                self.assertEqual(request.method, 'POST')
                self.assertEqual(request.headers['apikey'], key)
                calls.append((request.url.path, json.loads(request.content)))
                if request.url.path == '/rest/v1/rpc/prerequisite_snapshot_v1':
                    return httpx.Response(200, json=snapshot)
                self.assertEqual(request.url.path, '/rest/v1/rpc/prerequisite_import_v1')
                return httpx.Response(200, json={'insert':1,'update':0,'skip':0})
            with self.subTest(key_environment=envname), patch.dict(os.environ, {'SUPABASE_URL':'https://example.invalid',envname:key}, clear=True), \
                 patch.object(httpx.HTTPTransport, 'handle_request', handle):
                client = create_api_client()
                try:
                    self.assertEqual(seed_api(client,artifact)['counts']['insert'],1)
                    self.assertEqual(len(calls),1)
                    seed_api(client,artifact,apply=True,input_hash='a'*64,snapshot_hash='b'*64,
                             approved_input='a'*64,approved_snapshot='b'*64)
                    self.assertEqual(len(calls),3)
                    self.assertEqual(calls[-1][1]['p_expected_snapshot'],snapshot)
                    self.assertEqual(calls[-1][1]['p_approved_input_sha256'],'a'*64)
                finally:
                    client.postgrest.session.close()

    def test_real_sdk_error_response_is_redacted(self):
        import httpx
        def handle(_transport, request):
            return httpx.Response(403,json={'message':'dummy-secret-never-echo','code':'42501','details':None,'hint':None})
        with patch.dict(os.environ, {'SUPABASE_URL':'https://example.invalid','SUPABASE_SECRET_KEY':'sb_secret_dummy'}, clear=True), \
             patch.object(httpx.HTTPTransport,'handle_request',handle):
            client = create_api_client()
            try:
                with self.assertRaises(RuntimeError) as error:
                    seed_api(client,parse_rows([row()],CODES))
                self.assertNotIn('dummy-secret',str(error.exception))
            finally:
                client.postgrest.session.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
