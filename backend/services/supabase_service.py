"""Server-side Supabase configuration. Configuration is read only on demand."""

import os
from pathlib import Path
from urllib.parse import urlsplit


def create_supabase_client(*, load_env_file: bool = True):
    """Create a synchronous backend client. Callers can inject it into run_degree_plan."""
    if load_env_file:
        from dotenv import load_dotenv

        try:
            load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env", override=False)
        except (OSError, ValueError):
            raise ValueError("Unable to read server-side Supabase configuration") from None

    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    try:
        parsed = urlsplit(url)
        valid_url = (
            parsed.scheme == "https" and parsed.hostname and parsed.username is None
            and parsed.password is None and not parsed.query and not parsed.fragment
            and parsed.port != 0 and not any(c.isspace() or ord(c) < 32 for c in url)
            and "\\" not in url
        )
    except ValueError:
        valid_url = False
    valid_key = key and key.isascii() and key.isprintable() and " " not in key
    if not valid_url or not valid_key:
        raise ValueError("Configure an HTTPS SUPABASE_URL and SUPABASE_SECRET_KEY or SUPABASE_SERVICE_ROLE_KEY")

    from supabase import ClientOptions, create_client

    try:
        return create_client(url, key, options=ClientOptions(
            auto_refresh_token=False, persist_session=False, postgrest_client_timeout=130,
        ))
    except Exception:
        raise RuntimeError("Unable to initialize the server-side Supabase client") from None
