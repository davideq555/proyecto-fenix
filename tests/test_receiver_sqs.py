"""Pruebas del adaptador de publicación en SQS del receptor."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import replace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from fenix.services import receiver, sqs
from fenix.services.receiver import (
    WebhookDependencies,
    _publish_with_sqs,
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


def _cuerpo() -> bytes:
    """Serializa el evento de prueba como JSON compacto en UTF-8."""
    return json.dumps(EVENTO, separators=(",", ":")).encode("utf-8")


def _firmar(secret: str, timestamp: int, body: bytes) -> str:
    """Calcula un header ``X-Webhook-Signature`` válido para el cuerpo dado."""
    mensaje = f"{timestamp}".encode("ascii") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), mensaje, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def _deps_con_adaptador_real() -> WebhookDependencies:
    """Arma dependencias reales salvo inventario, validación y reloj."""
    base = WebhookDependencies.from_environment()
    return replace(
        base,
        secret=SECRETO,
        load_inventory=Mock(return_value={"node-01": {}}),
        validate_event=Mock(return_value=EVENTO),
        now=Mock(return_value=AHORA),
    )


def _post(cliente: TestClient, cuerpo: bytes) -> object:
    """Envía el cuerpo firmado al endpoint del webhook."""
    return cliente.post(
        "/webhook",
        content=cuerpo,
        headers={"X-Webhook-Signature": _firmar(SECRETO, AHORA, cuerpo)},
    )


def test_extrae_message_id_de_respuesta_boto3(monkeypatch: pytest.MonkeyPatch) -> None:
    """Extrae ``MessageId`` de la respuesta típica de Boto3."""

    def falso_send(event: dict) -> dict:
        """Simula la respuesta de Boto3."""
        return {"MessageId": "msg-123", "MD5OfMessageBody": "abc"}

    monkeypatch.setattr(sqs, "send_message", falso_send, raising=False)

    assert _publish_with_sqs({"event_id": "evt"}) == "msg-123"


def test_acepta_message_id_como_string(monkeypatch: pytest.MonkeyPatch) -> None:
    """Acepta un helper que ya devuelve el ``MessageId`` como ``str``."""

    def falso_send(event: dict) -> str:
        """Simula un helper que devuelve el ``MessageId``."""
        return "msg-456"

    monkeypatch.setattr(sqs, "send_message", falso_send, raising=False)

    assert _publish_with_sqs({"event_id": "evt"}) == "msg-456"


@pytest.mark.parametrize(
    "respuesta",
    [{}, {"MessageId": None}, {"MessageId": ""}, ["msg"]],
)
def test_respuesta_sin_message_id_falla(
    monkeypatch: pytest.MonkeyPatch, respuesta: object
) -> None:
    """Falla si la respuesta no confirma un ``MessageId``."""

    def falso_send(event: dict) -> object:
        """Simula una respuesta incompleta o inesperada."""
        return respuesta

    monkeypatch.setattr(sqs, "send_message", falso_send, raising=False)

    with pytest.raises(ValueError):
        _publish_with_sqs({"event_id": "evt"})


def test_from_environment_usa_adaptador_real() -> None:
    """Verifica que las dependencias reales usen el adaptador de SQS."""
    deps = WebhookDependencies.from_environment()

    assert deps.publish_event is _publish_with_sqs


def test_receptor_202_con_respuesta_boto3(monkeypatch: pytest.MonkeyPatch) -> None:
    """Devuelve 202 con el ``MessageId`` de una respuesta tipo Boto3."""

    def falso_send(event: dict) -> dict:
        """Simula la respuesta de Boto3."""
        return {"MessageId": "msg-789"}

    monkeypatch.setattr(sqs, "send_message", falso_send, raising=False)
    cliente = TestClient(receiver.create_app(_deps_con_adaptador_real()))

    respuesta = _post(cliente, _cuerpo())

    assert respuesta.status_code == 202
    assert respuesta.json()["message_id"] == "msg-789"


def test_receptor_503_si_sqs_falla(monkeypatch: pytest.MonkeyPatch) -> None:
    """Devuelve 503 cuando el adaptador real no puede publicar."""

    def falso_send(event: dict) -> str:
        """Simula SQS caído."""
        raise ConnectionError("SQS no disponible")

    monkeypatch.setattr(sqs, "send_message", falso_send, raising=False)
    cliente = TestClient(receiver.create_app(_deps_con_adaptador_real()))

    respuesta = _post(cliente, _cuerpo())

    assert respuesta.status_code == 503
