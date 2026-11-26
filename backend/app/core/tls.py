"""Conexiones HTTPS salientes (#89): CA de confianza propias y comprobación X.509 estricta.

Lo usan la descarga de documentos web, el modelo de lenguaje y los embeddings. httpx valida
con las CA públicas de `certifi`, no con el almacén del sistema, así que una CA corporativa
solo se tiene en cuenta si se añade aquí (instalarla en el contenedor no basta).

La comprobación estricta (`VERIFY_X509_STRICT`) exige que los certificados cumplan el RFC 5280
(p. ej. que tengan *Authority Key Identifier*). Python la activa por defecto desde la 3.13,
así que se fija siempre de forma explícita para no depender de la versión. Desactivarla no
desactiva la verificación: la cadena, la caducidad y el nombre se siguen comprobando.
"""

import functools
import ssl

import certifi
from cryptography import x509

PEM_BEGIN = "-----BEGIN CERTIFICATE-----"

# Motivos de OpenSSL que indican que el certificado no es de una CA de confianza.
_UNTRUSTED = (
    "unable to get local issuer certificate",
    "unable to get issuer certificate",
    "self-signed certificate",
    "self signed certificate",
    "unable to verify the first certificate",
)
_EXPIRED = ("certificate has expired", "certificate is not yet valid")
_NAME = ("hostname mismatch", "ip address mismatch")

WHERE = "Administración → Configuración → Certificados"


def parse_certificates(pem: str) -> list[x509.Certificate]:
    """Certificados de un texto PEM (uno o varios). Lanza `ValueError` si no son válidos."""
    if PEM_BEGIN not in pem:
        raise ValueError(f"No hay ningún certificado en formato PEM ({PEM_BEGIN} …)")
    try:
        certificates = x509.load_pem_x509_certificates(pem.encode())
        # Lo que se usará al conectar: OpenSSL debe poder cargarlos también.
        ssl.create_default_context().load_verify_locations(cadata=pem)
    except (ValueError, ssl.SSLError) as exc:
        raise ValueError(f"Los certificados no son válidos: {exc}") from exc
    return certificates


@functools.lru_cache(maxsize=8)
def ssl_context(ca_certs: str | None, strict: bool) -> ssl.SSLContext:
    """Contexto con las CA públicas más las indicadas (se reutiliza: crearlo es costoso)."""
    context = ssl.create_default_context(cafile=certifi.where())
    if ca_certs:
        context.load_verify_locations(cadata=ca_certs)
    if strict:
        context.verify_flags |= ssl.VERIFY_X509_STRICT
    else:
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
    return context


def _verify_reason(exc: BaseException) -> str | None:
    """Motivo del fallo de verificación del certificado, si lo es (en `exc` o sus causas)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return str(current.verify_message or current.reason or current)
        text = str(current)
        # httpx y openai envuelven el error de ssl y a veces solo conservan su texto.
        marker = "certificate verify failed: "
        if "CERTIFICATE_VERIFY_FAILED" in text and marker in text:
            return text.split(marker, 1)[1].split(" (_ssl", 1)[0].strip()
        current = current.__cause__ or current.__context__
    return None


def describe_certificate_error(exc: BaseException, strict: bool) -> str | None:
    """Explicación del fallo si se debe al certificado del servidor; `None` si no."""
    reason = _verify_reason(exc)
    if reason is None:
        return None
    lower = reason.lower()
    if any(text in lower for text in _UNTRUSTED):
        return (
            f"El certificado del servidor no es de una CA de confianza ({reason}). "
            f"Si es de una CA propia, añádela en {WHERE}."
        )
    if any(text in lower for text in _EXPIRED):
        return f"El certificado del servidor no está en vigor ({reason})."
    if any(text in lower for text in _NAME):
        return f"El certificado del servidor no corresponde a su dirección ({reason})."
    if strict:
        return (
            f"El certificado del servidor no supera la comprobación X.509 estricta ({reason}). "
            f"Si es de una CA propia, se puede desactivar en {WHERE}."
        )
    return f"El certificado del servidor no es válido ({reason})."
