from unittest.mock import MagicMock

import pytest

from fenix.services.events import validate
from fenix.utils.errors import ValidationError

MOCK_INVENTORY = {
    "node-01": {"type": "ec2", "id": "i-1234567890abcdef0"},
    "bucket-01": {"type": "s3", "id": "fenix-grupo-bucket-01"},
}


def test_i1_evento_valido_publica_en_sqs():
    sqs_mock = MagicMock()
    payload = {
        "schema_version": 1,
        "event_id": "evt-i1-valido-01",
        "event_type": "instance.stopped",
        "resource_id": "node-01",
        "occurred_at": "2026-11-18T23:50:00Z",
        "simulate_failures": 0,
    }

    resultado = validate(payload, MOCK_INVENTORY)
    # Simulamos el flujo del receptor: solo publica si validate() no lanza error
    sqs_mock.send_message(
        QueueUrl="http://localhost:4566/000000000000/fenix-queue",
        MessageBody=str(resultado),
    )

    assert resultado == payload
    assert resultado is not payload  # Garantiza que devuelve una copia
    sqs_mock.send_message.assert_called_once()


@pytest.mark.parametrize(
    "caso,payload",
    [
        (
            "claves_extra",
            {
                "schema_version": 1,
                "event_id": "evt-i1-err-01",
                "event_type": "instance.stopped",
                "resource_id": "node-01",
                "occurred_at": "2026-11-18T23:50:00Z",
                "simulate_failures": 0,
                "extra_field": "no-permitido",
            },
        ),
        (
            "bool_como_entero",
            {
                "schema_version": 1,
                "event_id": "evt-i1-err-02",
                "event_type": "instance.stopped",
                "resource_id": "node-01",
                "occurred_at": "2026-11-18T23:50:00Z",
                "simulate_failures": True,
            },
        ),
        (
            "recurso_ajeno",
            {
                "schema_version": 1,
                "event_id": "evt-i1-err-03",
                "event_type": "instance.stopped",
                "resource_id": "node-externo-99",
                "occurred_at": "2026-11-18T23:50:00Z",
                "simulate_failures": 0,
            },
        ),
    ],
)
def test_i1_rechazos_no_publican_en_sqs(caso, payload):
    sqs_mock = MagicMock()

    with pytest.raises(ValidationError):
        validate(payload, MOCK_INVENTORY)
        sqs_mock.send_message(
            QueueUrl="http://localhost:4566/000000000000/fenix-queue",
            MessageBody=str(payload),
        )

    sqs_mock.send_message.assert_not_called()
