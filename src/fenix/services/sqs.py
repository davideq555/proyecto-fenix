
"""Operaciones de colas SQS para el proyecto Fénix."""

from __future__ import annotations

import json
import os
from typing import Any

from botocore.exceptions import ClientError

from fenix.config.settings import get_sqs_client


def _queue_names() -> tuple[str, str]:
    """Obtiene los nombres de la cola principal y de la DLQ."""
    main_name = os.getenv("QUEUE_NAME", "fenix-events").strip()
    dlq_name = os.getenv("DLQ_NAME", "fenix-events-dlq").strip()

    if not main_name or not dlq_name:
        raise ValueError("Los nombres de las colas SQS no pueden estar vacíos")

    if main_name == dlq_name:
        raise ValueError(
            "La cola principal y la DLQ deben tener nombres distintos"
        )

    return main_name, dlq_name


def _get_or_create_queue(
    client: Any,
    name: str,
    attributes: dict[str, str] | None = None,
) -> str:
    """Obtiene una cola existente o la crea si no existe.

    Solo crea la cola cuando SQS informa que no existe.
    Los demás errores se propagan.
    """
    try:
        response = client.get_queue_url(QueueName=name)
        queue_url = response["QueueUrl"]

    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code")

        if error_code != "AWS.SimpleQueueService.NonExistentQueue":
            raise

        response = client.create_queue(
            QueueName=name,
            Attributes=attributes or {},
        )
        queue_url = response["QueueUrl"]

    if attributes:
        client.set_queue_attributes(
            QueueUrl=queue_url,
            Attributes=attributes,
        )

    return queue_url


def ensure_queues() -> dict[str, str]:
    """Crea o verifica las colas SQS de Fénix.

    Crea primero la DLQ y después la cola principal.
    Configura visibility timeout, long polling y RedrivePolicy.

    Returns:
        Diccionario con queue_url y dlq_url.

    Raises:
        ValueError: si la configuración no es válida.
        ClientError: si SQS devuelve un error.
    """
    client = get_sqs_client()
    main_name, dlq_name = _queue_names()

    try:
        visibility_timeout = int(
            os.getenv("VISIBILITY_TIMEOUT", "45")
        )
        max_receive_count = int(
            os.getenv("MAX_RECEIVE_COUNT", "3")
        )
    except ValueError as exc:
        raise ValueError(
            "VISIBILITY_TIMEOUT y MAX_RECEIVE_COUNT "
            "deben ser números enteros"
        ) from exc

    if visibility_timeout <= 0:
        raise ValueError("VISIBILITY_TIMEOUT debe ser positivo")

    if max_receive_count <= 0:
        raise ValueError("MAX_RECEIVE_COUNT debe ser positivo")

    # 1. Crear o recuperar la cola de mensajes fallidos.
    dlq_url = _get_or_create_queue(
        client,
        dlq_name,
    )

    # 2. Obtener el ARN de la DLQ.
    dlq_response = client.get_queue_attributes(
        QueueUrl=dlq_url,
        AttributeNames=["QueueArn"],
    )

    dlq_arn = dlq_response["Attributes"]["QueueArn"]

    # 3. Configurar la política de redirección.
    redrive_policy = json.dumps(
        {
            "deadLetterTargetArn": dlq_arn,
            "maxReceiveCount": str(max_receive_count),
        }
    )

    # 4. Configurar la cola principal.
    main_attributes = {
        "VisibilityTimeout": str(visibility_timeout),
        "ReceiveMessageWaitTimeSeconds": "10",
        "RedrivePolicy": redrive_policy,
    }

    main_url = _get_or_create_queue(
        client,
        main_name,
        main_attributes,
    )

    return {
        "queue_url": main_url,
        "dlq_url": dlq_url,
    }


def send_message(event: dict[str, Any]) -> dict[str, Any]:
    """Publica un evento JSON en la cola principal.

    Returns:
        Respuesta original de Boto3, que incluye MessageId.

    Raises:
        ValueError: si el evento no es válido o SQS no confirma el envío.
        ClientError: si SQS devuelve un error.
    """
    if not isinstance(event, dict):
        raise ValueError("El evento debe ser un diccionario")

    queues = ensure_queues()

    response = get_sqs_client().send_message(
        QueueUrl=queues["queue_url"],
        MessageBody=json.dumps(
            event,
            ensure_ascii=False,
        ),
    )

    message_id = response.get("MessageId")

    if not isinstance(message_id, str) or not message_id.strip():
        raise ValueError("SQS no confirmó el envío del mensaje")

    return response

def retry_later(
    receipt_handle: str,
    receive_count: int,
    queue_url: str | None = None,
) -> int:
    """Retrasa la visibilidad del mensaje original para reintentar."""
    if not isinstance(receipt_handle, str) or not receipt_handle.strip():
        raise ValueError("El ReceiptHandle no es válido")

    if (
        isinstance(receive_count, bool)
        or not isinstance(receive_count, int)
        or receive_count < 1
    ):
        raise ValueError("receive_count debe ser un entero positivo")

    if queue_url is None:
        queue_url = ensure_queues()["queue_url"]

    if not isinstance(queue_url, str) or not queue_url.strip():
        raise ValueError("La URL de la cola no es válida")

    delay = min(30, 2 ** receive_count)

    get_sqs_client().change_message_visibility(
        QueueUrl=queue_url,
        ReceiptHandle=receipt_handle,
        VisibilityTimeout=delay,
    )

    return delay

def receive_dlq_message(
    max_number: int = 1,
    wait_time: int = 1,
) -> dict[str, Any]:
    """Lee mensajes de la DLQ sin eliminarlos."""
    if (
        isinstance(max_number, bool)
        or not isinstance(max_number, int)
        or not 1 <= max_number <= 10
    ):
        raise ValueError("max_number debe ser un entero entre 1 y 10")

    if (
        isinstance(wait_time, bool)
        or not isinstance(wait_time, int)
        or not 0 <= wait_time <= 20
    ):
        raise ValueError("wait_time debe ser un entero entre 0 y 20")

    dlq_url = ensure_queues()["dlq_url"]

    response = get_sqs_client().receive_message(
        QueueUrl=dlq_url,
        MaxNumberOfMessages=max_number,
        WaitTimeSeconds=wait_time,
        AttributeNames=["All"],
        MessageAttributeNames=["All"],
    )

    return response
