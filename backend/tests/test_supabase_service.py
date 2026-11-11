"""Server-side client configuration tests with synthetic credentials and a fake SDK."""

import os
from pathlib import Path
import runpy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from backend.services import supabase_service


class SupabaseServiceTests(unittest.TestCase):
    def setUp(self):
        self.sdk = SimpleNamespace(ClientOptions=Mock(), create_client=Mock())
        self.dotenv = SimpleNamespace(load_dotenv=Mock())
        for context in (
            patch.dict("sys.modules", {"supabase": self.sdk, "dotenv": self.dotenv}),
            patch.dict(os.environ, {"SUPABASE_URL": "https://fixture.invalid"}, clear=True),
        ):
            context.start()
            self.addCleanup(context.stop)

    def test_import_reads_no_configuration_and_creates_no_client(self):
        runpy.run_path(supabase_service.__file__)
        self.dotenv.load_dotenv.assert_not_called()
        self.sdk.create_client.assert_not_called()

    def test_planning_key_precedence_and_server_options(self):
        for secret, role, expected in (
            ("primary", "", "primary"), ("primary", "legacy", "primary"),
            ("", "legacy", "legacy"),
        ):
            with self.subTest(secret=secret, role=role), patch.dict(os.environ, {
                "SUPABASE_SECRET_KEY": secret, "SUPABASE_SERVICE_ROLE_KEY": role,
            }):
                client = supabase_service.create_supabase_client()
                self.assertIs(client, self.sdk.create_client.return_value)
                self.sdk.create_client.assert_called_with(
                    "https://fixture.invalid", expected, options=self.sdk.ClientOptions.return_value,
                )
                self.sdk.ClientOptions.assert_called_with(
                    auto_refresh_token=False, persist_session=False, postgrest_client_timeout=130,
                )
                self.dotenv.load_dotenv.assert_called_with(
                    dotenv_path=Path(__file__).resolve().parents[2] / ".env", override=False,
                )

    def test_missing_or_invalid_settings_fail_before_sdk_creation(self):
        for url, key in (
            ("", "fixture-private"), ("https://fixture.invalid", ""), ("https://fixture.invalid", "   "),
            ("http://fixture.invalid", "fixture-private"), ("https://user:fixture-private@host", "k"),
            ("https://host/?key=fixture-private", "k"), ("https://host/#fixture-private", "k"),
            ("https://host:fixture-private", "k"), ("https://[fixture-private", "k"),
        ):
            with self.subTest(url=url), patch.dict(os.environ, {"SUPABASE_URL": url, "SUPABASE_SECRET_KEY": key}):
                with self.assertRaises(ValueError) as caught:
                    supabase_service.create_supabase_client(load_env_file=False)
                self.assertNotIn("fixture-private", str(caught.exception))
        self.dotenv.load_dotenv.assert_not_called()
        self.sdk.create_client.assert_not_called()

    def test_sdk_and_dotenv_errors_are_sanitized(self):
        os.environ["SUPABASE_SECRET_KEY"] = "fixture-private"
        for dependency, expected in ((self.sdk.create_client, RuntimeError), (self.dotenv.load_dotenv, ValueError)):
            with self.subTest(dependency=dependency):
                dependency.side_effect = ValueError("fixture-private")
                with self.assertRaises(expected) as caught:
                    supabase_service.create_supabase_client()
                self.assertNotIn("fixture-private", str(caught.exception))
                self.assertTrue(caught.exception.__suppress_context__)
                dependency.side_effect = None
