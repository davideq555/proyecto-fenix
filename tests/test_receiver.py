"""Pruebas unitarias del receptor HTTP del webhook."""

from __future__ import annotations

import hashlib
import hmac
import json
from unittest.mock import Mock

from fastapi.testclient import TestClient

from fenix.services.receiver import (
    MAX_BODY_BYTES,
    WebhookDependencies,
    create_app,
)

SECRETO = "secreto-de-prueba"
AHORA = 1_700_000_000
EVENTO = {
    "schema_version": 1,
    "event_id": "evt-node-001",
    "event_type": "instance.stopped",
    "resource_id": "node-01",
    "occurred_at": "2026-10-22T18:00:00Z",
    "simulate_failures": 0,
}


class ErrorDeValidacionFake(ValueError):
    """Error de validación simulado para las pruebas del receptor."""


def _cuerpo(evento: dict | None = None) -> bytes:
    """Serializa el evento de prueba como JSON compacto en UTF-8."""
    return json.dumps(evento or EVENTO, separators=(",", ":")).encode("utf-8")


def _firmar(secret: str, timestamp: int, body: bytes) -> str:
    """Calcula un header ``X-Webhook-Signature`` válido para el cuerpo dado."""
    mensaje = f"{timestamp}".encode("ascii") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), mensaje, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def _deps(
    *,
    secret: str = SECRETO,
    inventory: dict | None = None,
    validate: Mock | None = None,
    publish: Mock | None = None,
    now: int = AHORA,
) -> WebhookDependencies:
    """Construye dependencias de prueba con validadores y publicadores simulados."""
    inventario = inventory if inventory is not None else {"node-01": {}}
    return WebhookDependencies(
        secret=secret,
        load_inventory=Mock(return_value=inventario),
        validate_event=validate or Mock(return_value=EVENTO),
        publish_event=publish or Mock(return_value="msg-001"),
        validation_exceptions=(ErrorDeValidacionFake,),
        now=Mock(return_value=now),
    )


def _cliente(deps: WebhookDependencies) -> TestClient:
    """Crea un cliente HTTP de pruebas para la aplicación del receptor."""
    return TestClient(create_app(deps))


def test_evento_valido_responde_202() -> None:
    """Acepta un evento firmado y devuelve event_id y message_id."""
    cuerpo = _cuerpo()
    cliente = _cliente(_deps())

    respuesta = cliente.post(
        "/webhook",
        content=cuerpo,
        headers={"X-Webhook-Signature": _firmar(SECRETO, AHORA, cuerpo)},
    )

    assert respuesta.status_code == 202
    assert respuesta.json() == {"event_id": "evt-node-001", "message_id": "msg-001"}


def test_evento_valido_valida_y_publica_una_vez() -> None:
    """Ejecuta la validación y la publicación exactamente una vez."""
    validador = Mock(return_value=EVENTO)
    publicador = Mock(return_value="msg-001")
    cuerpo = _cuerpo()
    cliente = _cliente(_deps(validate=validador, publish=publicador))

    cliente.post(
        "/webhook",
        content=cuerpo,
        headers={"X-Webhook-Signature": _firmar(SECRETO, AHORA, cuerpo)},
    )

    assert validador.call_count == 1
    assert publicador.call_count == 1


def test_cuerpo_en_el_limite_es_aceptado() -> None:
    """Acepta un cuerpo de exactamente 16 KiB."""
    prefijo = b'{"pad":"'
    sufijo = b'"}'
    relleno = MAX_BODY_BYTES - len(prefijo) - len(sufijo)
    cuerpo = prefijo + b"x" * relleno + sufijo
    assert len(cuerpo) == MAX_BODY_BYTES
    cliente = _cliente(_deps())

    respuesta = cliente.post(
        "/webhook",
        content=cuerpo,
        headers={"X-Webhook-Signature": _firmar(SECRETO, AHORA, cuerpo)},
    )

    assert respuesta.status_code == 202


def test_cuerpo_mayor_a_16_kib_responde_413() -> None:
    """Rechaza un cuerpo que supera los 16 KiB sin validar ni publicar."""
    validador = Mock(return_value=EVENTO)
    publicador = Mock(return_value="msg-001")
    cuerpo = b"x" * (MAX_BODY_BYTES + 1)
    cliente = _cliente(_deps(validate=validador, publish=publicador))

    respuesta = cliente.post(
        "/webhook",
        content=cuerpo,
        headers={"X-Webhook-Signature": _firmar(SECRETO, AHORA, cuerpo)},
    )

    assert respuesta.status_code == 413
    validador.assert_not_called()
    publicador.assert_not_called()


def test_firma_ausente_responde_401() -> None:
    """Rechaza una petición sin header de firma."""
    validador = Mock(return_value=EVENTO)
    publicador = Mock(return_value="msg-001")
    cliente = _cliente(_deps(validate=validador, publish=publicador))

    respuesta = cliente.post("/webhook", content=_cuerpo())

    assert respuesta.status_code == 401
    validador.assert_not_called()
    publicador.assert_not_called()


def test_firma_duplicada_responde_401() -> None:
    """Rechaza dos headers de firma en la misma petición."""
    cuerpo = _cuerpo()
    firma = _firmar(SECRETO, AHORA, cuerpo)
    cliente = _cliente(_deps())

    respuesta = cliente.post(
        "/webhook",
        content=cuerpo,
        headers=[
            ("X-Webhook-Signature", firma),
            ("X-Webhook-Signature", firma),
        ],
    )

    assert respuesta.status_code == 401


def test_firma_invalida_responde_401() -> None:
    """Rechaza una firma calculada con otro secreto."""
    cuerpo = _cuerpo()
    cliente = _cliente(_deps())

    respuesta = cliente.post(
        "/webhook",
        content=cuerpo,
        headers={"X-Webhook-Signature": _firmar("otro-secreto", AHORA, cuerpo)},
    )

    assert respuesta.status_code == 401


def test_timestamp_vencido_responde_401() -> None:
    """Rechaza una firma fuera de la ventana de 300 segundos."""
    cuerpo = _cuerpo()
    cliente = _cliente(_deps())

    respuesta = cliente.post(
        "/webhook",
        content=cuerpo,
        headers={"X-Webhook-Signature": _firmar(SECRETO, AHORA - 301, cuerpo)},
    )

    assert respuesta.status_code == 401


def test_json_roto_responde_400() -> None:
    """Rechaza JSON inválido aunque la firma sea correcta."""
    cuerpo = b'{"schema_version": 1,'
    validador = Mock(return_value=EVENTO)
    publicador = Mock(return_value="msg-001")
    cliente = _cliente(_deps(validate=validador, publish=publicador))

    respuesta = cliente.post(
        "/webhook",
        content=cuerpo,
        headers={"X-Webhook-Signature": _firmar(SECRETO, AHORA, cuerpo)},
    )

    assert respuesta.status_code == 400
    validador.assert_not_called()
    publicador.assert_not_called()


def test_utf8_invalido_responde_400() -> None:
    """Rechaza un cuerpo que no decodifica como UTF-8."""
    cuerpo = b"\xff\xfe\xfa"
    cliente = _cliente(_deps())

    respuesta = cliente.post(
        "/webhook",
        content=cuerpo,
        headers={"X-Webhook-Signature": _firmar(SECRETO, AHORA, cuerpo)},
    )

    assert respuesta.status_code == 400


def test_lista_json_responde_422() -> None:
    """Rechaza JSON válido que no sea un objeto."""
    cuerpo = b"[]"
    validador = Mock(return_value=EVENTO)
    publicador = Mock(return_value="msg-001")
    cliente = _cliente(_deps(validate=validador, publish=publicador))

    respuesta = cliente.post(
        "/webhook",
        content=cuerpo,
        headers={"X-Webhook-Signature": _firmar(SECRETO, AHORA, cuerpo)},
    )

    assert respuesta.status_code == 422
    validador.assert_not_called()
    publicador.assert_not_called()


def test_contrato_invalido_responde_422() -> None:
    """Mapea un error de validación del dominio a 422 sin publicar."""
    validador = Mock(side_effect=ErrorDeValidacionFake("contrato inválido"))
    publicador = Mock(return_value="msg-001")
    cuerpo = _cuerpo()
    cliente = _cliente(_deps(validate=validador, publish=publicador))

    respuesta = cliente.post(
        "/webhook",
        content=cuerpo,
        headers={"X-Webhook-Signature": _firmar(SECRETO, AHORA, cuerpo)},
    )

    assert respuesta.status_code == 422
    publicador.assert_not_called()


def test_sqs_caido_responde_503() -> None:
    """Devuelve 503 cuando la publicación en SQS falla."""
    publicador = Mock(side_effect=ConnectionError("SQS no disponible"))
    cuerpo = _cuerpo()
    cliente = _cliente(_deps(publish=publicador))

    respuesta = cliente.post(
        "/webhook",
        content=cuerpo,
        headers={"X-Webhook-Signature": _firmar(SECRETO, AHORA, cuerpo)},
    )

    assert respuesta.status_code == 503


def test_sqs_sin_message_id_responde_503() -> None:
    """No promete aceptación si SQS no confirma un MessageId."""
    publicador = Mock(return_value=None)
    cuerpo = _cuerpo()
    cliente = _cliente(_deps(publish=publicador))

    respuesta = cliente.post(
        "/webhook",
        content=cuerpo,
        headers={"X-Webhook-Signature": _firmar(SECRETO, AHORA, cuerpo)},
    )

    assert respuesta.status_code == 503


def test_secreto_vacio_responde_500() -> None:
    """Distingue un secreto no configurado de una firma inválida."""
    cuerpo = _cuerpo()
    cliente = _cliente(_deps(secret=""))

    respuesta = cliente.post(
        "/webhook",
        content=cuerpo,
        headers={"X-Webhook-Signature": _firmar(SECRETO, AHORA, cuerpo)},
    )

    assert respuesta.status_code == 500


def test_inventario_ilegible_responde_500() -> None:
    """Devuelve 500 si el inventario local no se puede leer."""
    deps = _deps()
    deps.load_inventory.side_effect = FileNotFoundError("sin inventario")
    cuerpo = _cuerpo()
    cliente = _cliente(deps)

    respuesta = cliente.post(
        "/webhook",
        content=cuerpo,
        headers={"X-Webhook-Signature": _firmar(SECRETO, AHORA, cuerpo)},
    )

    assert respuesta.status_code == 500
