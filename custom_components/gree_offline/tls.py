"""TLS server context for the discovery socket, accepting the weak ciphers Gree firmware uses."""

import datetime
from pathlib import Path
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

WEAK_CIPHERS = "ALL:@SECLEVEL=0"


def server_context(cert_dir: str) -> ssl.SSLContext:
    """Blocking: may generate the certificate on first use."""
    cert, key = _ensure_cert(Path(cert_dir))
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.set_ciphers(WEAK_CIPHERS)
    ctx.load_cert_chain(cert, key)
    return ctx


def _ensure_cert(cert_dir: Path) -> tuple[Path, Path]:
    """Self-signed and cached; the unit does not pin the certificate."""
    cert_path, key_path = cert_dir / "dis.crt", cert_dir / "dis.key"
    if cert_path.exists() and key_path.exists():
        return cert_path, key_path

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "dis.gree.com")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )

    cert_dir.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path
