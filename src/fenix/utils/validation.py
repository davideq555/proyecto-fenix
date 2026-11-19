"""Contratos de validación a completar por el integrante 1."""

import re  # Comprueba el formato de los identificadores.
from datetime import datetime, timedelta  # Trabaja con fechas conscientes de UTC.

from fenix.utils.errors import ValidationError  # Error público del dominio.


def identifier(value: str) -> str:
    """Valida un identificador corto y devuelve su texto sin modificar.

    Args:
        value: Texto entre 3 y 32 caracteres; letras minúsculas, dígitos o guion.
    Returns:
        El identificador original válido.
    Raises:
        ValidationError: Si el tipo o el patrón no cumplen el contrato.
    """
    if not isinstance(value, str):
        raise ValidationError("El identificador debe ser una cadena de texto.")

    if not re.fullmatch(r"[a-z0-9-]{3,32}", value):
        raise ValidationError(
            f"El identificador '{value}' no cumple con el patrón [a-z0-9-]{3, 32}."
        )

    return value


def ttl_seconds(value: str) -> int:
    """Valida el TTL didáctico empleado en la opción de entornos efímeros.

    Args:
        value: Texto con un entero entre 60 y 600 segundos inclusive.
    Returns:
        El número entero de segundos.
    Raises:
        ValidationError: Si no es entero o está fuera de los límites.
    """
    try:
        # Convertimos asegurando que no acepte booleanos ni flotantes encubiertos si vinieran mal
        if isinstance(value, bool):
            raise TypeError("Los booleanos no son válidos como TTL.")
        ttl = int(value)
    except (ValueError, TypeError) as err:
        raise ValidationError("El TTL debe ser un número entero válido.") from err

    if not (60 <= ttl <= 600):
        raise ValidationError(
            f"El TTL {ttl} está fuera del rango permitido (60 - 600)."
        )

    return ttl


def utc_text(value: str) -> datetime:
    """Interpreta una fecha UTC explícita sin aceptar fechas ingenuas.

    Args:
        value: ISO 8601 terminado en Z o con offset +00:00.
    Returns:
        Fecha con zona UTC; por ejemplo 2026-10-22T18:00:00+00:00.
    Raises:
        ValidationError: Si falta zona, hay offset no nulo o formato inválido.
    """
    if not isinstance(value, str):
        raise ValidationError(
            "La fecha debe ser una cadena de texto en formato ISO 8601."
        )

    try:
        # Normalizamos la 'Z' al formato de offset UTC numérico compatible
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
    except Exception as err:
        raise ValidationError(f"Formato de fecha inválido: '{value}'.") from err

    # Exigir zona horaria explícita y que el offset sea exactamente UTC (timedelta(0))
    if dt.tzinfo is None or dt.utcoffset() != timedelta(0):
        raise ValidationError(
            "La fecha debe incluir una zona horaria UTC explícita (terminada en Z o +00:00)."
        )

    return dt
