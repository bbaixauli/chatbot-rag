"""Cifrado de los secretos guardados en la base de datos (claves de API, contraseñas).

La clave se deriva de `CHATBOT_SESSION_SECRET`: una copia de la base de datos no basta para
leerlos. Si la clave cambia, los secretos guardados dejan de poder descifrarse y hay que
volver a introducirlos en la administración.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from pydantic import SecretStr

# Separa esta clave de otros usos del mismo secreto (la firma de las cookies).
_CONTEXT = b"chatbot-rag/config-secrets/v1"


class SecretUnreadableError(Exception):
    """El secreto se cifró con otra clave (cambió CHATBOT_SESSION_SECRET) o está dañado."""


class SecretCipher:
    """Cifra y descifra textos con una clave derivada del secreto de sesión."""

    def __init__(self, session_secret: SecretStr) -> None:
        digest = hashlib.sha256(_CONTEXT + session_secret.get_secret_value().encode()).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode()
        except (InvalidToken, ValueError) as exc:
            raise SecretUnreadableError from exc
