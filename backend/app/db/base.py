"""Clase base declarativa de SQLAlchemy para todos los modelos."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base común de los modelos ORM."""
