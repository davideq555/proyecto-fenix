"""
Funciones de validación estricta de tipos, formatos y contratos.
Rol 1 (I1) - Proyecto Fénix.
"""

import re
from datetime import datetime

from fenix.utils.errors import ValidationError

ID_REGEX = re.compile(r"^[a-z0-9-]{3,32}$")


def validate_identifier(value: str, field_name: str) -> str:
    """Valida que el identificador cumpla estrictamente con el formato del contrato."""
    if not isinstance(value, str):
        raise ValidationError(f"El campo '{field_name}' debe ser una cadena de texto.")
    if not ID_REGEX.match(value):
        raise ValidationError(f"El campo '{field_name}' ('{value}') no cumple con el formato [a-z0-9-]{{3,32}}.")
    return value


def validate_utc_timestamp(value: str) -> str:
    """Valida que el timestamp esté en formato ISO 8601 y posea zona horaria UTC explícita."""
    if not isinstance(value, str):
        raise ValidationError("El campo 'occurred_at' debe ser una cadena de texto.")
    
    try:
        normalized_value = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized_value)
        
        if dt.tzinfo is None:
            raise ValidationError("El timestamp 'occurred_at' debe incluir zona horaria UTC.")
    except Exception as e:
        if isinstance(e, ValidationError):
            raise
        raise ValidationError(f"Formato de 'occurred_at' inválido: '{value}'. Debe ser ISO 8601.") from e
        
    return value


def validate_ttl(ttl: int) -> int:
    """Valida que el tiempo de vida (TTL) sea un número entero positivo y no un booleano."""
    if isinstance(ttl, bool) or not isinstance(ttl, int):
        raise ValidationError("El campo 'ttl_seconds' debe ser un número entero (no se aceptan booleanos).")
    if ttl < 0:
        raise ValidationError("El campo 'ttl_seconds' no puede ser un número negativo.")
    return ttl