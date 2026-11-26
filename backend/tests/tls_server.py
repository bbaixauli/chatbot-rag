"""Servidor HTTPS local con una CA propia «mal formada» para los tests de certificados (#89).

Como algunas CA corporativas, ni la CA ni el certificado del servidor tienen *Authority Key
Identifier*: la comprobación X.509 estricta los rechaza. El servidor responde a una página
web (GET) y a la API de embeddings de OpenAI (POST /v1/embeddings).
"""

import datetime as dt
import json
import ssl
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

PAGE = (
    "<html><head><title>Normas de la intranet</title></head><body><main>"
    "<h1>Normas de la intranet</h1>"
    "<p>Las normas internas se publican en la intranet corporativa con certificado propio.</p>"
    "</main></body></html>"
)


@dataclass(frozen=True)
class TlsServer:
    url: str
    ca_pem: str


def _name(common_name: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])


def _certificates(directory: Path) -> tuple[str, Path, Path]:
    """CA y certificado para localhost firmado por ella, ambos sin AKI."""
    now = dt.datetime.now(dt.UTC)
    validity = (now - dt.timedelta(days=1), now + dt.timedelta(days=30))
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = (
        x509.CertificateBuilder()
        .subject_name(_name("CA corporativa de prueba"))
        .issuer_name(_name("CA corporativa de prueba"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(validity[0])
        .not_valid_after(validity[1])
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    certificate = (
        x509.CertificateBuilder()
        .subject_name(_name("localhost"))
        .issuer_name(ca.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(validity[0])
        .not_valid_after(validity[1])
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    cert_file, key_file = directory / "servidor.pem", directory / "servidor.key"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return ca.public_bytes(serialization.Encoding.PEM).decode(), cert_file, key_file


class _Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._send(200, "text/html; charset=utf-8", PAGE.encode())

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        request = json.loads(self.rfile.read(length) or b"{}")
        if self.path.endswith("/embeddings"):
            texts = request.get("input") or []
            body = {
                "object": "list",
                "model": request.get("model", ""),
                "data": [
                    {"object": "embedding", "index": i, "embedding": [1.0, 0.0, 0.0]}
                    for i, _ in enumerate(texts)
                ],
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            }
            self._send(200, "application/json", json.dumps(body).encode())
            return
        self._send(404, "application/json", b'{"error": {"message": "no encontrado"}}')

    def log_message(self, *args: object) -> None:
        pass


@contextmanager
def tls_server(directory: Path) -> Iterator[TlsServer]:
    ca_pem, cert_file, key_file = _certificates(directory)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield TlsServer(f"https://localhost:{server.server_address[1]}", ca_pem)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
