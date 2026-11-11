"""Reparación convergente de recursos EC2 y S3 del laboratorio Fénix."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)

_ESTADOS_TERMINALES = frozenset({"terminated", "shutting-down"})
_ERRORES_INSTANCIA_INVALIDA = frozenset(
    {"InvalidInstanceID.NotFound", "InvalidInstanceID.Malformed"}
)
_ERRORES_SIN_TAGS = frozenset({"NoSuchTagSet", "NoSuchTagSetError"})


class RepairError(Exception):
    """Base de los errores de reparación."""


class RepairPermanentError(RepairError):
    """Falla que no se recupera reintentando; el mensaje va a la DLQ."""


class RepairTransientError(RepairError):
    """Falla recuperable; el worker reintenta con backoff."""


@dataclass(frozen=True)
class RepairResult:
    """Resultado de una reparación convergente."""

    resource_id: str
    action: str
    changed: bool
    status: str

    def to_dict(self) -> dict[str, Any]:
        """Devuelve el resultado como diccionario para el ledger."""
        return asdict(self)


def repair(
    event: dict[str, Any],
    inventory: dict[str, Any],
    *,
    ec2_client: Any | None = None,
    s3_client: Any | None = None,
    sleep: Callable[[float], None] = time.sleep,
    max_attempts: int = 10,
    poll_interval: float = 0.5,
) -> RepairResult:
    """Repara de forma convergente el recurso indicado por el evento.

    Args:
        event: Evento ya validado, con ``event_type`` y ``resource_id``.
        inventory: Inventario de recursos del laboratorio.
        ec2_client: Cliente EC2 de Boto3 (requerido para eventos EC2).
        s3_client: Cliente S3 de Boto3 (requerido para eventos S3).
        sleep: Función de espera, inyectable para las pruebas.
        max_attempts: Cantidad máxima de lecturas de estado al esperar.
        poll_interval: Segundos entre lecturas de estado.

    Returns:
        ``RepairResult`` con la acción realizada y el estado final.

    Raises:
        RepairPermanentError: Si el recurso no existe, no es propio o su
            estado no puede repararse.
        RepairTransientError: Si una falla recuperable impide converger.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts debe ser mayor o igual a 1")
    if not isinstance(event, dict):
        raise RepairPermanentError("evento inválido")
    if not isinstance(inventory, dict):
        raise RepairPermanentError("inventario inválido")

    event_type = event.get("event_type")
    alias = event.get("resource_id")
    if not isinstance(alias, str) or not alias:
        raise RepairPermanentError("evento sin resource_id")

    resource = _resolve_resource(inventory, alias)

    if event_type == "instance.stopped":
        if resource.get("type") != "ec2":
            raise RepairPermanentError(f"el recurso {alias} no es de tipo ec2")
        if ec2_client is None:
            raise RepairPermanentError("cliente EC2 no configurado")
        return _repair_ec2(
            alias=alias,
            resource=resource,
            event=event,
            client=ec2_client,
            sleep=sleep,
            max_attempts=max_attempts,
            poll_interval=poll_interval,
        )

    if event_type == "bucket.tags_missing":
        if resource.get("type") != "s3":
            raise RepairPermanentError(f"el recurso {alias} no es de tipo s3")
        if s3_client is None:
            raise RepairPermanentError("cliente S3 no configurado")
        return _repair_s3(
            alias=alias,
            resource=resource,
            event=event,
            client=s3_client,
        )

    raise RepairPermanentError(f"event_type no soportado: {event_type!r}")


def _resolve_resource(inventory: dict[str, Any], alias: str) -> dict[str, Any]:
    """Busca el recurso del alias en el inventario.

    Admite el inventario plano por alias y el envuelto en ``resources``.

    Raises:
        RepairPermanentError: Si el inventario o el recurso no son válidos.
    """
    resources = inventory.get("resources", inventory)
    if not isinstance(resources, dict):
        raise RepairPermanentError("inventario inválido")

    resource = resources.get(alias)
    if not isinstance(resource, dict):
        raise RepairPermanentError(f"el recurso {alias} no está en el inventario")
    return resource


def _repair_ec2(
    *,
    alias: str,
    resource: dict[str, Any],
    event: dict[str, Any],
    client: Any,
    sleep: Callable[[float], None],
    max_attempts: int,
    poll_interval: float,
) -> RepairResult:
    """Lleva una instancia EC2 al estado ``running`` de forma convergente.

    Raises:
        RepairPermanentError: Si el estado actual no puede repararse.
        RepairTransientError: Si una falla recuperable impide converger.
    """
    instance_id = resource.get("id")
    if not isinstance(instance_id, str) or not instance_id:
        raise RepairPermanentError(f"el recurso {alias} no tiene id")

    state = _describe_state(client, instance_id)
    logger.info(
        "estado EC2 leído",
        extra={
            "event_id": event.get("event_id"),
            "resource_id": alias,
            "action": "describe",
            "status": state,
        },
    )

    if state == "running":
        return RepairResult(alias, "noop", False, state)

    if state in _ESTADOS_TERMINALES:
        raise RepairPermanentError(
            f"la instancia {instance_id} está en estado {state}"
        )

    if state == "stopping":
        raise RepairTransientError(
            f"la instancia {instance_id} está en un estado transitorio: {state}"
        )

    if state == "pending":
        _wait_for_state(
            client,
            instance_id,
            "running",
            sleep=sleep,
            max_attempts=max_attempts,
            poll_interval=poll_interval,
        )
        return RepairResult(alias, "noop", False, "running")

    if state == "stopped":
        _start_instance(client, instance_id)
        _wait_for_state(
            client,
            instance_id,
            "running",
            sleep=sleep,
            max_attempts=max_attempts,
            poll_interval=poll_interval,
        )
        logger.info(
            "instancia EC2 reparada",
            extra={
                "event_id": event.get("event_id"),
                "resource_id": alias,
                "action": "start",
                "status": "running",
            },
        )
        return RepairResult(alias, "start", True, "running")

    raise RepairPermanentError(
        f"la instancia {instance_id} está en un estado no soportado: {state}"
    )


def _describe_state(client: Any, instance_id: str) -> str:
    """Lee el estado actual de una instancia EC2.

    Raises:
        RepairPermanentError: Si la instancia no existe o la respuesta es inválida.
        RepairTransientError: Si la API falla de forma recuperable.
    """
    try:
        response = client.describe_instances(InstanceIds=[instance_id])
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in _ERRORES_INSTANCIA_INVALIDA:
            raise RepairPermanentError(
                f"la instancia {instance_id} no existe"
            ) from exc
        raise RepairTransientError(
            f"no se pudo describir la instancia {instance_id}"
        ) from exc
    except BotoCoreError as exc:
        raise RepairTransientError(
            f"no se pudo describir la instancia {instance_id}"
        ) from exc

    try:
        return response["Reservations"][0]["Instances"][0]["State"]["Name"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RepairPermanentError(
            f"respuesta inválida al describir la instancia {instance_id}"
        ) from exc


def _start_instance(client: Any, instance_id: str) -> None:
    """Solicita el arranque de una instancia EC2.

    Raises:
        RepairTransientError: Si la API rechaza o falla el arranque.
    """
    try:
        client.start_instances(InstanceIds=[instance_id])
    except ClientError as exc:
        raise RepairTransientError(
            f"no se pudo arrancar la instancia {instance_id}"
        ) from exc
    except BotoCoreError as exc:
        raise RepairTransientError(
            f"no se pudo arrancar la instancia {instance_id}"
        ) from exc


def _wait_for_state(
    client: Any,
    instance_id: str,
    target: str,
    *,
    sleep: Callable[[float], None],
    max_attempts: int,
    poll_interval: float,
) -> str:
    """Espera de forma acotada a que la instancia alcance el estado objetivo.

    Raises:
        RepairTransientError: Si se agotan los intentos sin alcanzar ``target``.
    """
    for attempt in range(max_attempts):
        state = _describe_state(client, instance_id)
        if state == target:
            return state
        if attempt < max_attempts - 1:
            sleep(poll_interval)
    raise RepairTransientError(
        f"timeout esperando {target} para la instancia {instance_id}"
    )


def _repair_s3(
    *,
    alias: str,
    resource: dict[str, Any],
    event: dict[str, Any],
    client: Any,
) -> RepairResult:
    """Restaura las etiquetas obligatorias de un bucket S3.

    Lee las etiquetas actuales, mezcla las esperadas del inventario con las
    ajenas y escribe solo si hay cambios. Después verifica por relectura.

    Raises:
        RepairPermanentError: Si el recurso no define bucket o etiquetas válidas.
        RepairTransientError: Si la API falla o la verificación no converge.
    """
    bucket = resource.get("id")
    if not isinstance(bucket, str) or not bucket:
        raise RepairPermanentError(f"el recurso {alias} no tiene id")

    expected = resource.get("expected_tags")
    if not isinstance(expected, dict) or not expected:
        raise RepairPermanentError(
            f"el recurso {alias} no define etiquetas esperadas"
        )
    for key, value in expected.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise RepairPermanentError(
                f"el recurso {alias} tiene etiquetas esperadas inválidas"
            )

    current = _read_tags(client, bucket)
    merged = {**current, **expected}

    if merged == current:
        logger.info(
            "tags S3 ya convergentes",
            extra={
                "event_id": event.get("event_id"),
                "resource_id": alias,
                "action": "tags",
                "status": "tags-ok",
            },
        )
        return RepairResult(alias, "noop", False, "tags-ok")

    _write_tags(client, bucket, merged)
    verified = _read_tags(client, bucket)
    if any(verified.get(key) != value for key, value in merged.items()):
        raise RepairTransientError(
            f"las etiquetas del bucket {bucket} no convergieron"
        )

    logger.info(
        "tags S3 reparadas",
        extra={
            "event_id": event.get("event_id"),
            "resource_id": alias,
            "action": "put-tags",
            "status": "tags-ok",
        },
    )
    return RepairResult(alias, "put-tags", True, "tags-ok")


def _read_tags(client: Any, bucket: str) -> dict[str, str]:
    """Lee las etiquetas de un bucket y trata ``NoSuchTagSet`` como vacío.

    Raises:
        RepairPermanentError: Si la respuesta tiene un formato inválido.
        RepairTransientError: Si la API falla de forma recuperable.
    """
    try:
        response = client.get_bucket_tagging(Bucket=bucket)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in _ERRORES_SIN_TAGS:
            return {}
        raise RepairTransientError(
            f"no se pudieron leer las etiquetas del bucket {bucket}"
        ) from exc
    except BotoCoreError as exc:
        raise RepairTransientError(
            f"no se pudieron leer las etiquetas del bucket {bucket}"
        ) from exc

    tag_set = response.get("TagSet", [])
    if not isinstance(tag_set, list):
        raise RepairPermanentError(
            f"respuesta inválida al leer etiquetas del bucket {bucket}"
        )

    tags: dict[str, str] = {}
    for item in tag_set:
        if not isinstance(item, dict):
            raise RepairPermanentError(
                f"respuesta inválida al leer etiquetas del bucket {bucket}"
            )
        key = item.get("Key")
        value = item.get("Value")
        if not isinstance(key, str) or not isinstance(value, str):
            raise RepairPermanentError(
                f"respuesta inválida al leer etiquetas del bucket {bucket}"
            )
        tags[key] = value
    return tags


def _write_tags(client: Any, bucket: str, tags: dict[str, str]) -> None:
    """Escribe el conjunto completo de etiquetas de un bucket.

    Raises:
        RepairTransientError: Si la API rechaza o falla la escritura.
    """
    tagging = {
        "TagSet": [
            {"Key": key, "Value": value} for key, value in tags.items()
        ]
    }
    try:
        client.put_bucket_tagging(Bucket=bucket, Tagging=tagging)
    except ClientError as exc:
        raise RepairTransientError(
            f"no se pudieron escribir las etiquetas del bucket {bucket}"
        ) from exc
    except BotoCoreError as exc:
        raise RepairTransientError(
            f"no se pudieron escribir las etiquetas del bucket {bucket}"
        ) from exc
