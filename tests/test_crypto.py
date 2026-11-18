"""Pruebas unitarias de la verificación HMAC del webhook."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from fenix.utils.crypto import (
    DEFAULT_TOLERANCE_SECONDS,
    WebhookConfigurationError,
    WebhookSignatureError,
    verify,
)

SECRETO = "secreto-de-prueba"
CUERPO = b'{"schema_version":1,"event_id":"evt-node-001"}'
AHORA = 1_700_000_000


def _firmar(secret: str, timestamp: int, body: bytes) -> str:
    """Calcula un header ``X-Webhook-Signature`` válido para el cuerpo dado."""
    mensaje = f"{timestamp}".encode("ascii") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), mensaje, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def test_firma_valida_es_aceptada() -> None:
    """Acepta una firma calculada sobre el cuerpo crudo y el timestamp."""
    header = _firmar(SECRETO, AHORA, CUERPO)

    verify(SECRETO, header, CUERPO, now=AHORA)


def test_header_ausente_es_rechazado() -> None:
    """Rechaza la petición cuando no hay header de firma."""
    with pytest.raises(WebhookSignatureError):
        verify(SECRETO, None, CUERPO, now=AHORA)


def test_header_vacio_es_rechazado() -> None:
    """Rechaza un header presente pero vacío."""
    with pytest.raises(WebhookSignatureError):
        verify(SECRETO, "", CUERPO, now=AHORA)


@pytest.mark.parametrize(
    "header",
    [
        "t=1700000000",
        "v1=" + "a" * 64,
        f"t={AHORA},v1={'a' * 63}",
        f"t={AHORA},v1={'a' * 65}",
        f"t={AHORA},v1={'a' * 64},extra=1",
        f"t={AHORA},v1={'a' * 64},v1={'a' * 64}",
        f"t={AHORA},t={AHORA},v1={'a' * 64}",
        f" t={AHORA},v1={'a' * 64}",
        f"t={AHORA}, v1={'a' * 64}",
        f"t=+{AHORA},v1={'a' * 64}",
        f"t=1e9,v1={'a' * 64}",
        f"t=01,v1={'a' * 64}",
        f"t={AHORA},v1={'z' * 64}",
    ],
)
def test_header_con_formato_invalido_es_rechazado(header: str) -> None:
    """Rechaza headers ambiguos, duplicados, incompletos o con hex inválido."""
    with pytest.raises(WebhookSignatureError):
        verify(SECRETO, header, CUERPO, now=AHORA)


def test_cuerpo_alterado_es_rechazado() -> None:
    """Rechaza cuando el cuerpo cambia después de firmar (caso F05)."""
    header = _firmar(SECRETO, AHORA, CUERPO)

    with pytest.raises(WebhookSignatureError):
        verify(SECRETO, header, CUERPO + b" ", now=AHORA)


def test_firma_sobre_bytes_crudos_con_espacios() -> None:
    """Demuestra que la firma cubre los bytes crudos, no el JSON canónico."""
    cuerpo = b'{ "event_id" : "evt-node-001" }'
    header = _firmar(SECRETO, AHORA, cuerpo)

    verify(SECRETO, header, cuerpo, now=AHORA)
    with pytest.raises(WebhookSignatureError):
        verify(SECRETO, header, b'{"event_id":"evt-node-001"}', now=AHORA)


def test_timestamp_vencido_es_rechazado() -> None:
    """Rechaza un timestamp más viejo que la ventana permitida."""
    header = _firmar(SECRETO, AHORA - DEFAULT_TOLERANCE_SECONDS - 1, CUERPO)

    with pytest.raises(WebhookSignatureError):
        verify(SECRETO, header, CUERPO, now=AHORA)


def test_timestamp_futuro_es_rechazado() -> None:
    """Rechaza un timestamp más nuevo que la ventana permitida."""
    header = _firmar(SECRETO, AHORA + DEFAULT_TOLERANCE_SECONDS + 1, CUERPO)

    with pytest.raises(WebhookSignatureError):
        verify(SECRETO, header, CUERPO, now=AHORA)


def test_timestamp_en_el_limite_pasado_es_aceptado() -> None:
    """Acepta un timestamp exactamente en el borde pasado de la ventana."""
    header = _firmar(SECRETO, AHORA - DEFAULT_TOLERANCE_SECONDS, CUERPO)

    verify(SECRETO, header, CUERPO, now=AHORA)


def test_timestamp_en_el_limite_futuro_es_aceptado() -> None:
    """Acepta un timestamp exactamente en el borde futuro de la ventana."""
    header = _firmar(SECRETO, AHORA + DEFAULT_TOLERANCE_SECONDS, CUERPO)

    verify(SECRETO, header, CUERPO, now=AHORA)


def test_hex_en_mayusculas_es_aceptado() -> None:
    """Acepta el digest hexadecimal en mayúsculas y lo normaliza."""
    header = _firmar(SECRETO, AHORA, CUERPO)
    prefijo, _, digest = header.partition(",v1=")

    verify(SECRETO, f"{prefijo},v1={digest.upper()}", CUERPO, now=AHORA)


def test_secreto_vacio_es_error_de_configuracion() -> None:
    """Distingue un secreto no configurado de una firma inválida."""
    header = _firmar(SECRETO, AHORA, CUERPO)

    with pytest.raises(WebhookConfigurationError):
        verify("", header, CUERPO, now=AHORA)


def test_cuerpo_vacio_con_firma_valida_es_aceptado() -> None:
    """Acepta la firma de un cuerpo vacío; el contrato se valida después."""
    header = _firmar(SECRETO, AHORA, b"")

    verify(SECRETO, header, b"", now=AHORA)


def test_usa_compare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Comprueba que la comparación use ``hmac.compare_digest``."""
    llamadas: list[tuple[str, str]] = []
    original = hmac.compare_digest

    def espia(izquierda: str, derecha: str) -> bool:
        """Registra la comparación y delega en la implementación real."""
        llamadas.append((izquierda, derecha))
        return original(izquierda, derecha)

    monkeypatch.setattr("fenix.utils.crypto.hmac.compare_digest", espia)
    header = _firmar(SECRETO, AHORA, CUERPO)

    verify(SECRETO, header, CUERPO, now=AHORA)

    assert llamadas, "verify debe comparar con hmac.compare_digest"
