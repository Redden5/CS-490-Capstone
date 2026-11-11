"""Optional Supabase transport. No dotenv, connection, or writes on import.

Both functions in migration 002 are required. A single scalar JSON snapshot
avoids REST pagination limits, mixed read snapshots and silently filtered RLS.
Only the validated CLI should call seed_api for an import: this transport does
not replace artifact/source verification performed by seed_prerequisites.main.
"""
import re

from backend.scripts.seed_prerequisites import build_plan
from backend.services.supabase_service import create_supabase_client


def create_api_client():
    """Preserve the prerequisite import CLI's process-environment-only contract."""
    return create_supabase_client(load_env_file=False)


def rpc(client, name, params):
    try:
        return client.rpc(name, params).execute().data
    except Exception:
        # SDK errors can contain URLs/headers/server details: never echo them.
        raise RuntimeError("Supabase RPC failed; check deployment/access securely. Commit outcome may be unknown after a transport failure") from None


def seed_api(client, artifact, *, apply=False, input_hash=None, snapshot_hash=None,
             approved_input=None, approved_snapshot=None):
    if apply and any(not isinstance(actual, str) or not re.fullmatch(r"[0-9a-f]{64}", actual)
                     or actual != approved for actual, approved in
                     [(input_hash, approved_input), (snapshot_hash, approved_snapshot)]):
        raise ValueError("API apply requires both exact reviewed SHA256 acknowledgments")
    snapshot = rpc(client, "prerequisite_snapshot_v1", {})
    if not isinstance(snapshot, dict) or any(not isinstance(snapshot.get(k), list)
                                          for k in ["courses", "catalog_years", "rules"]):
        raise ValueError("Unexpected RPC snapshot; deploy reviewed migration 002")
    plan = build_plan(artifact, snapshot)
    if apply:
        if plan["errors"]:
            raise ValueError("Unmapped targets/catalog years block the entire RPC import")
        expected = {k: plan["counts"][k] for k in ("insert", "update", "skip")}
        result = rpc(client, "prerequisite_import_v1", {
            "p_expected_snapshot": snapshot,
            "p_rules": [op["payload"] for op in plan["operations"]],
            "p_input_sha256": input_hash, "p_snapshot_sha256": snapshot_hash,
            "p_approved_input_sha256": approved_input, "p_approved_snapshot_sha256": approved_snapshot})
        if result != expected:
            # The RPC verifies stored payloads before returning/committing. This
            # response check cannot undo a transaction whose response is lost.
            raise RuntimeError("Unexpected RPC acknowledgment; check database read-only before retrying")
    return plan
