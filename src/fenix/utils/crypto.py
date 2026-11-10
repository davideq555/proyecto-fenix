"""Verificación de firmas HMAC-SHA256 para el webhook de Fénix."""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from typing import Final

# Ventana máxima, en segundos, entre el timestamp firmado y el reloj actual.
DEFAULT_TOLERANCE_SECONDS: Final = 300

_SIGNATURE_PATTERN: Final = re.compile(r"t=(0|[1-9]\d{0,9}),v1=([0-9a-fA-F]{64})")


class WebhookSignatureError(Exception):
    """Señala que la firma del webhook falta, es ambigua o no coincide."""

    def __init__(self, motivo: str) -> None:
        """Inicializa la excepción con un motivo seguro para registrar."""
        super().__init__(motivo)
        self.motivo = motivo


class WebhookConfigurationError(Exception):
    """Señala un error de configuración del receptor, no de la petición."""


def verify(
    secret: str,
    signature_header: str | None,
    raw_body: bytes,
    *,
    now: float | None = None,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
) -> None:
    """Verifica la firma HMAC-SHA256 de una petición de webhook.

    La firma se calcula sobre ``str(timestamp).encode() + b"." + raw_body``
    y se compara en tiempo constante contra el valor ``v1`` del header.

    Args:
        secret: Secreto compartido configurado en ``WEBHOOK_SECRET``.
        signature_header: Valor del header ``X-Webhook-Signature``.
        raw_body: Cuerpo HTTP tal como llegó, sin reserializar.
        now: Reloj de referencia en segundos; por defecto ``time.time()``.
        tolerance_seconds: Ventana permitida hacia pasado y futuro.

    Returns:
        ``None`` cuando la firma es válida.

    Raises:
        WebhookConfigurationError: Si el secreto está vacío.
        WebhookSignatureError: Si el header falta, tiene formato inválido,
            el timestamp está fuera de la ventana o el HMAC no coincide.

    Side Effects:
        No modifica el cuerpo ni el header y no registra secretos.
    """
    if not secret:
        raise WebhookConfigurationError("WEBHOOK_SECRET no configurado")

    if not signature_header:
        raise WebhookSignatureError("header de firma ausente")

    timestamp, provided_digest = _parse_signature_header(signature_header)
    current = time.time() if now is None else now

    if abs(current - timestamp) > tolerance_seconds:
        raise WebhookSignatureError("timestamp fuera de ventana")

    expected_digest = _expected_digest(secret, timestamp, raw_body)
    if not hmac.compare_digest(expected_digest, provided_digest.lower()):
        raise WebhookSignatureError("firma no coincide")


def _parse_signature_header(header: str) -> tuple[int, str]:
    """Extrae el timestamp y el digest de un header ``t=...,v1=...``.

    Args:
        header: Valor completo del header de firma.

    Returns:
        Tupla ``(timestamp, digest)`` con el timestamp como entero.

    Raises:
        WebhookSignatureError: Si el header no cumple el formato estricto.
    """
    match = _SIGNATURE_PATTERN.fullmatch(header)
    if match is None:
        raise WebhookSignatureError("formato de firma inválido")

    timestamp_text, digest = match.groups()
    return int(timestamp_text), digest


def _expected_digest(secret: str, timestamp: int, raw_body: bytes) -> str:
    """Calcula el HMAC-SHA256 esperado en hexadecimal.

    Args:
        secret: Secreto compartido.
        timestamp: Segundos epoch ya validados.
        raw_body: Cuerpo HTTP original.

    Returns:
        Digest hexadecimal en minúsculas.
    """
    message = f"{timestamp}".encode("ascii") + b"." + raw_body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
