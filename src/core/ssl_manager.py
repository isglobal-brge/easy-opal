import datetime
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.x509.oid import NameOID
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from rich.console import Console

from src.core.config_manager import CERTS_DIR, load_config

console = Console()

CA_CERT_PATH = CERTS_DIR / "ca.crt"
CA_KEY_PATH = CERTS_DIR / "ca.key"


def _generate_ca():
    """Generates a local Certificate Authority (CA) key and certificate."""
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    ca_name = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "easy-opal Local CA"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "easy-opal"),
    ])

    now = datetime.datetime.now(datetime.timezone.utc)
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
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
        .sign(ca_key, hashes.SHA256())
    )

    return ca_key, ca_cert


def _build_san(hosts: list) -> x509.SubjectAlternativeName:
    """Builds a SubjectAlternativeName extension from a list of hosts/IPs."""
    names = []
    for host in hosts:
        try:
            ip = ipaddress.ip_address(host)
            names.append(x509.IPAddress(ip))
        except ValueError:
            names.append(x509.DNSName(host))
    return x509.SubjectAlternativeName(names)


def generate_self_signed_cert(cert_path: Path, key_path: Path):
    """
    Generates a self-signed SSL certificate using a local CA.

    Creates a local CA (if needed) and a server certificate signed by it.
    The CA cert is saved separately so users can optionally trust it.
    """
    config = load_config()
    hosts = config.get("hosts", ["localhost", "127.0.0.1"])

    CERTS_DIR.mkdir(parents=True, exist_ok=True)

    console.print(f"[cyan]Generating self-signed certificate for: {', '.join(hosts)}[/cyan]")

    # Generate (or regenerate) the local CA
    ca_key, ca_cert = _generate_ca()

    CA_KEY_PATH.write_bytes(
        ca_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())
    )
    CA_CERT_PATH.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))

    # Generate server key
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    # Build server certificate signed by the CA
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
        .add_extension(_build_san(hosts), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    # Write server cert and key
    cert_path.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        server_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption())
    )

    console.print(f"[green]SSL certificate generated successfully in {CERTS_DIR}[/green]")
    console.print(f"[dim]To avoid browser warnings, you can import [cyan]{CA_CERT_PATH}[/cyan] into your browser or system trust store.[/dim]")
