"""Pruebas unitarias del servicio SQS."""

from __future__ import annotations

import json

import pytest
from botocore.exceptions import ClientError

from fenix.services import sqs


class FakeSQS:
    """Simula las respuestas de un cliente SQS sin conectarse a AWS."""

    def __init__(self):
        self.queues = {}
        self.calls = []
        self.sent_messages = []
        self.visibility_changes = []
        self.received_messages = []

    def get_queue_url(self, QueueName):
        self.calls.append(("get_queue_url", QueueName))
        if QueueName not in self.queues:
            raise ClientError(
                {
                    "Error": {
                        "Code": "AWS.SimpleQueueService.NonExistentQueue"
                    }
                },
                "GetQueueUrl",
            )
        return {"QueueUrl": self.queues[QueueName]}

    def create_queue(self, QueueName, Attributes):
        self.calls.append(("create_queue", QueueName, Attributes))
        url = f"http://localhost:4566/000000000000/{QueueName}"
        self.queues[QueueName] = url
        return {"QueueUrl": url}

    def set_queue_attributes(self, QueueUrl, Attributes):
        self.calls.append(("set_queue_attributes", QueueUrl, Attributes))
        return {}

    def get_queue_attributes(self, QueueUrl, AttributeNames):
        self.calls.append(("get_queue_attributes", QueueUrl, AttributeNames))
        return {
            "Attributes": {
                "QueueArn": "arn:aws:sqs:us-east-1:000000000000:fenix-events-dlq"
            }
        }

    def send_message(self, QueueUrl, MessageBody):
        self.sent_messages.append((QueueUrl, MessageBody))
        return {"MessageId": "mensaje-123"}

    def change_message_visibility(
        self, QueueUrl, ReceiptHandle, VisibilityTimeout
    ):
        self.visibility_changes.append(
            (QueueUrl, ReceiptHandle, VisibilityTimeout)
        )
        return {}

    def receive_message(
        self,
        QueueUrl,
        MaxNumberOfMessages,
        WaitTimeSeconds,
        AttributeNames,
        MessageAttributeNames,
    ):
        self.received_messages.append(
            (
                QueueUrl,
                MaxNumberOfMessages,
                WaitTimeSeconds,
                AttributeNames,
                MessageAttributeNames,
            )
        )
        return {"Messages": [{"Body": '{"evento": "prueba"}'}]}


@pytest.fixture
def cliente(monkeypatch):
    """Instala un cliente SQS falso para cada prueba."""
    fake = FakeSQS()
    monkeypatch.setattr(sqs, "get_sqs_client", lambda: fake)
    return fake


def test_queue_names_usa_valores_por_defecto(monkeypatch):
    monkeypatch.delenv("QUEUE_NAME", raising=False)
    monkeypatch.delenv("DLQ_NAME", raising=False)

    assert sqs._queue_names() == ("fenix-events", "fenix-events-dlq")


@pytest.mark.parametrize(
    ("main_name", "dlq_name"),
    [
        ("", "fenix-events-dlq"),
        ("fenix-events", ""),
        ("misma-cola", "misma-cola"),
    ],
)
def test_queue_names_rechaza_nombres_invalidos(
    monkeypatch, main_name, dlq_name
):
    monkeypatch.setenv("QUEUE_NAME", main_name)
    monkeypatch.setenv("DLQ_NAME", dlq_name)

    with pytest.raises(ValueError):
        sqs._queue_names()


def test_get_or_create_queue_crea_si_no_existe(cliente):
    url = sqs._get_or_create_queue(cliente, "cola-prueba")

    assert url.endswith("/cola-prueba")
    assert ("create_queue", "cola-prueba", {}) in cliente.calls


def test_get_or_create_queue_no_oculta_otros_errores(cliente):
    def error_permiso(QueueName):
        raise ClientError(
            {"Error": {"Code": "AccessDenied"}},
            "GetQueueUrl",
        )

    cliente.get_queue_url = error_permiso

    with pytest.raises(ClientError):
        sqs._get_or_create_queue(cliente, "cola-prueba")


def test_ensure_queues_crea_dlq_y_cola_principal(cliente, monkeypatch):
    monkeypatch.delenv("QUEUE_NAME", raising=False)
    monkeypatch.delenv("DLQ_NAME", raising=False)
    monkeypatch.delenv("VISIBILITY_TIMEOUT", raising=False)
    monkeypatch.delenv("MAX_RECEIVE_COUNT", raising=False)

    resultado = sqs.ensure_queues()

    assert resultado["queue_url"].endswith("/fenix-events")
    assert resultado["dlq_url"].endswith("/fenix-events-dlq")

    nombres_creados = [
        llamada[1]
        for llamada in cliente.calls
        if llamada[0] == "create_queue"
    ]
    assert nombres_creados == ["fenix-events-dlq", "fenix-events"]

    llamada_principal = next(
        llamada
        for llamada in cliente.calls
        if llamada[0] == "set_queue_attributes"
        and llamada[1] == resultado["queue_url"]
    )
    atributos = llamada_principal[2]

    assert atributos["VisibilityTimeout"] == "45"
    assert atributos["ReceiveMessageWaitTimeSeconds"] == "10"

    politica = json.loads(atributos["RedrivePolicy"])
    assert politica["maxReceiveCount"] == "3"
    assert politica["deadLetterTargetArn"].endswith("fenix-events-dlq")


@pytest.mark.parametrize(
    ("variable", "valor"),
    [
        ("VISIBILITY_TIMEOUT", "cero"),
        ("MAX_RECEIVE_COUNT", "incorrecto"),
        ("VISIBILITY_TIMEOUT", "0"),
        ("MAX_RECEIVE_COUNT", "-1"),
    ],
)
def test_ensure_queues_rechaza_configuracion_invalida(
    cliente, monkeypatch, variable, valor
):
    monkeypatch.delenv("QUEUE_NAME", raising=False)
    monkeypatch.delenv("DLQ_NAME", raising=False)
    monkeypatch.delenv("VISIBILITY_TIMEOUT", raising=False)
    monkeypatch.delenv("MAX_RECEIVE_COUNT", raising=False)
    monkeypatch.setenv(variable, valor)

    with pytest.raises(ValueError):
        sqs.ensure_queues()


def test_send_message_publica_evento_json(cliente, monkeypatch):
    monkeypatch.setattr(
        sqs,
        "ensure_queues",
        lambda: {"queue_url": "http://localhost/cola", "dlq_url": "http://localhost/dlq"},
    )

    evento = {"tipo": "prueba", "mensaje": "hola"}

    respuesta = sqs.send_message(evento)

    assert respuesta["MessageId"] == "mensaje-123"
    url, cuerpo = cliente.sent_messages[0]
    assert url == "http://localhost/cola"
    assert json.loads(cuerpo) == evento


@pytest.mark.parametrize("evento", [None, [], "texto", 123])
def test_send_message_rechaza_eventos_que_no_son_diccionarios(evento):
    with pytest.raises(ValueError):
        sqs.send_message(evento)


def test_send_message_rechaza_respuesta_sin_message_id(
    cliente, monkeypatch
):
    monkeypatch.setattr(
        sqs,
        "ensure_queues",
        lambda: {"queue_url": "http://localhost/cola", "dlq_url": "http://localhost/dlq"},
    )
    cliente.send_message = lambda **kwargs: {"MessageId": ""}

    with pytest.raises(ValueError):
        sqs.send_message({"tipo": "prueba"})


@pytest.mark.parametrize(
    ("receive_count", "esperado"),
    [(1, 2), (2, 4), (4, 16), (5, 30), (10, 30)],
)
def test_retry_later_calcula_retraso(
    cliente, monkeypatch, receive_count, esperado
):
    retraso = sqs.retry_later(
        "recibo-123",
        receive_count,
        queue_url="http://localhost/cola",
    )

    assert retraso == esperado
    assert cliente.visibility_changes == [
        ("http://localhost/cola", "recibo-123", esperado)
    ]


@pytest.mark.parametrize(
    ("receipt_handle", "receive_count", "queue_url"),
    [
        ("", 1, "http://localhost/cola"),
        ("recibo", 0, "http://localhost/cola"),
        ("recibo", -1, "http://localhost/cola"),
        ("recibo", True, "http://localhost/cola"),
        ("recibo", 1, ""),
    ],
)
def test_retry_later_rechaza_parametros_invalidos(
    receipt_handle, receive_count, queue_url
):
    with pytest.raises(ValueError):
        sqs.retry_later(receipt_handle, receive_count, queue_url)


def test_retry_later_obtiene_cola_si_no_recibe_url(
    cliente, monkeypatch
):
    monkeypatch.setattr(
        sqs,
        "ensure_queues",
        lambda: {"queue_url": "http://localhost/cola", "dlq_url": "http://localhost/dlq"},
    )

    assert sqs.retry_later("recibo-123", 1) == 2
    assert cliente.visibility_changes[0][0] == "http://localhost/cola"


def test_receive_dlq_message_lee_mensajes(cliente, monkeypatch):
    monkeypatch.setattr(
        sqs,
        "ensure_queues",
        lambda: {"queue_url": "http://localhost/cola", "dlq_url": "http://localhost/dlq"},
    )

    respuesta = sqs.receive_dlq_message(max_number=3, wait_time=5)

    assert "Messages" in respuesta
    assert cliente.received_messages == [
        (
            "http://localhost/dlq",
            3,
            5,
            ["All"],
            ["All"],
        )
    ]


@pytest.mark.parametrize(
    ("max_number", "wait_time"),
    [
        (0, 1),
        (11, 1),
        (True, 1),
        (1, -1),
        (1, 21),
        (1, False),
    ],
)
def test_receive_dlq_message_rechaza_parametros_invalidos(
    max_number, wait_time
):
    with pytest.raises(ValueError):
        sqs.receive_dlq_message(max_number, wait_time)
