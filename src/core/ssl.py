"""SSL certificate generation with persistent local CA."""

import datetime
import ipaddress
import os
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from src.models.config import OpalConfig
from src.models.instance import InstanceContext
from src.utils.console import console, success, dim


def _write_key(path: Path, key: rsa.RSAPrivateKey) -> None:
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _write_cert(path: Path, cert: x509.Certificate) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _generate_ca() -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "easy-opal Local CA"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "easy-opal"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False, content_commitment=False,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=True, crl_sign=True,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return key, cert


def ensure_ca(ctx: InstanceContext) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """Load existing CA, or generate a new one if it doesn't exist."""
    ca_key_path = ctx.certs_dir / "ca.key"
    ca_cert_path = ctx.certs_dir / "ca.crt"

    if ca_key_path.exists() and ca_cert_path.exists():
        ca_key = serialization.load_pem_private_key(
            ca_key_path.read_bytes(), password=None
        )
        ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
        return ca_key, ca_cert  # type: ignore[return-value]

    ctx.certs_dir.mkdir(parents=True, exist_ok=True)
    ca_key, ca_cert = _generate_ca()
    _write_key(ca_key_path, ca_key)
    _write_cert(ca_cert_path, ca_cert)
    dim(f"Local CA created at {ca_cert_path}")
    return ca_key, ca_cert


def generate_server_cert(ctx: InstanceContext, config: OpalConfig) -> None:
    """Generate a server cert signed by the local CA."""
    hosts = config.hosts or ["localhost", "127.0.0.1"]
    ctx.certs_dir.mkdir(parents=True, exist_ok=True)

    console.print(f"[cyan]Generating certificate for: {', '.join(hosts)}[/cyan]")

    ca_key, ca_cert = ensure_ca(ctx)

    # Build SAN
    san_names: list[x509.GeneralName] = []
    for host in hosts:
        try:
            ip = ipaddress.ip_address(host)
            san_names.append(x509.IPAddress(ip))
        except ValueError:
            san_names.append(x509.DNSName(host))

    # Generate server key + cert
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, hosts[0]),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "easy-opal"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName(san_names), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    _write_key(ctx.certs_dir / "opal.key", server_key)
    _write_cert(ctx.certs_dir / "opal.crt", server_cert)

    success(f"SSL certificate generated in {ctx.certs_dir}")
    dim(f"To avoid browser warnings, import {ctx.certs_dir / 'ca.crt'} into your trust store.")


def get_cert_info(ctx: InstanceContext) -> dict | None:
    """Read server cert metadata. Returns None if no cert exists."""
    cert_path = ctx.certs_dir / "opal.crt"
    if not cert_path.exists():
        return None

    cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    return {
        "subject": cert.subject.rfc4514_string(),
        "issuer": cert.issuer.rfc4514_string(),
        "not_after": cert.not_valid_after_utc.isoformat(),
        "dns_names": san.value.get_values_for_type(x509.DNSName),
        "ip_addresses": [str(ip) for ip in san.value.get_values_for_type(x509.IPAddress)],
    }
