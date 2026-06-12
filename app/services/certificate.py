import tempfile
import os
from contextlib import contextmanager
from pathlib import Path
from cryptography.hazmat.primitives.serialization import pkcs12, Encoding, PrivateFormat, NoEncryption


def load_pfx(pfx_path: Path, password: str) -> tuple[bytes, bytes]:
    """Load PFX and return (cert_pem, key_pem)."""
    pfx_data = pfx_path.read_bytes()
    private_key, certificate, _ = pkcs12.load_key_and_certificates(
        pfx_data, password.encode("utf-8") if password else None
    )
    cert_pem = certificate.public_bytes(Encoding.PEM)
    key_pem = private_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    return cert_pem, key_pem


@contextmanager
def cert_files(pfx_path: Path, password: str):
    """Context manager that yields (cert_file_path, key_file_path) as temp files."""
    cert_pem, key_pem = load_pfx(pfx_path, password)
    cert_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
    key_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pem")
    try:
        cert_tmp.write(cert_pem)
        cert_tmp.flush()
        key_tmp.write(key_pem)
        key_tmp.flush()
        cert_tmp.close()
        key_tmp.close()
        yield cert_tmp.name, key_tmp.name
    finally:
        os.unlink(cert_tmp.name)
        os.unlink(key_tmp.name)
