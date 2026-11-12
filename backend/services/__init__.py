import hashlib


def reviewed_csv_sha256(data: bytes) -> str:
    """Hash with the CRLF line endings used for the reviewed CSV pins."""
    canonical = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    return hashlib.sha256(canonical).hexdigest()
