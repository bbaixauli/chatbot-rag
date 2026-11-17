"""Identidad del usuario actual.

De momento la aplicación es monousuario y anónima. Todo el código obtiene el usuario
a través de `get_current_user`, de modo que añadir LDAP solo requiere sustituir esta
dependencia.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends

ANONYMOUS_USER_ID = "anonymous"


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """Usuario autenticado (o anónimo)."""

    id: str
    display_name: str


ANONYMOUS_USER = CurrentUser(id=ANONYMOUS_USER_ID, display_name="Anónimo")


def get_current_user() -> CurrentUser:
    """Devuelve el usuario de la petición (fijo mientras no haya autenticación)."""
    return ANONYMOUS_USER


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
