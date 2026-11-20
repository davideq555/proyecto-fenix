"""Consumo de SQS y registro durable por etapas del worker Fénix."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fenix.services import events, notifications, remediation, sqs
from fenix.services.remediation import (
    RepairPermanentError,
    RepairTransientError,
)
from fenix.utils import state
from fenix.utils.errors import ProjectError, ValidationError
from fenix.utils.state import PROJECT_ROOT

logger = logging.getLogger(__name__)

WAIT_TIME_SECONDS = 10
VISIBILITY_TIMEOUT = 45
MAX_MESSAGES = 1
TRANSPORT_RETRY_SECONDS = 2

STATUS_ACKNOWLEDGED = "acknowledged"
STATUS_RETRY = "retry"
STATUS_DUPLICATE = "duplicate"

_RECEIVE_COUNT_ATTRIBUTE = "ApproximateReceiveCount"
_LEDGER_VERSION = 1
_LEDGER_KEYS = (
    "event_id",
    "message_id",
    "resource_id",
    "attempt",
    "action",
    "status",
)


class LedgerError(ProjectError):
    """El ledger de eventos tiene una estructura inválida.

    Args:
        message: Descripción pública del problema detectado.
    """


def ledger_path() -> Path:
    """Devuelve la ruta del ledger de eventos.

    Returns:
        La ruta configurada en ``FENIX_EVENTS_PATH`` o, por defecto,
        ``data/events.json`` dentro de la raíz del proyecto.
    """
    configured_path = os.getenv("FENIX_EVENTS_PATH")

    if configured_path:
        return Path(configured_path).expanduser()

    return PROJECT_ROOT / "data" / "events.json"


def load_ledger(path: Path) -> dict[str, Any]:
    """Carga el ledger o devuelve su estructura vacía si no existe.

    Args:
        path: Ruta del archivo JSON del ledger.

    Returns:
        Diccionario con ``version`` y ``events``.

    Raises:
        LedgerError: Si el archivo no es un objeto con ``events`` como objeto.
        OSError: Si el archivo existe pero no puede leerse.
        json.JSONDecodeError: Si el contenido no es JSON válido.
    """
    if not path.exists():
        return {"version": _LEDGER_VERSION, "events": {}}

    with path.open("r", encoding="utf-8") as file:
        ledger = json.load(file)

    if not isinstance(ledger, dict):
        raise LedgerError("el ledger debe ser un objeto JSON")

    if not isinstance(ledger.get("events"), dict):
        raise LedgerError("la sección events del ledger debe ser un objeto")

    return ledger


def save_ledger(path: Path, ledger: dict[str, Any]) -> None:
    """Guarda el ledger mediante un temporal único y reemplazo atómico.

    Args:
        path: Ruta del archivo JSON del ledger.
        ledger: Estructura completa por etapas a persistir.

    Returns:
        None únicamente después de reemplazar el archivo de forma atómica.

    Raises:
        LedgerError: Si el ledger o su sección ``events`` no son objetos.
        OSError: Si falla la escritura o el reemplazo del archivo.
        TypeError: Si el contenido no es serializable a JSON.
    """
    if not isinstance(ledger, dict):
        raise LedgerError("el ledger debe ser un diccionario")

    if not isinstance(ledger.get("events"), dict):
        raise LedgerError("la sección events del ledger debe ser un objeto")

    path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary_path = Path(file.name)

            json.dump(ledger, file, indent=2, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        os.replace(temporary_path, path)

    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


@dataclass(frozen=True)
class WorkerDependencies:
    """Dependencias externas del worker, inyectables para las pruebas.

    Args:
        queue_url: URL de la cola principal de SQS.
        receive_message: Operación de sondeo de SQS.
        delete_message: Operación de ACK de SQS.
        retry_later: Programación del backoff sobre un ``ReceiptHandle``.
        load_inventory: Lectura del inventario autorizado.
        validate_event: Revalidación del contrato de eventos.
        repair_event: Reparación convergente del recurso.
        notify: Envío del aviso público confirmado.
        ec2_client: Cliente EC2 opcional para la reparación.
        s3_client: Cliente S3 opcional para la reparación.
        events_path: Ruta del ledger durable de eventos.
        sleep: Función de espera, inyectable para las pruebas.
        clock: Generador de marcas temporales UTC.
        logger: Logger estructurado del componente.
    """

    queue_url: str
    receive_message: Callable[..., Any]
    delete_message: Callable[..., Any]
    retry_later: Callable[[str, int], int]
    load_inventory: Callable[[], dict[str, Any]]
    validate_event: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
    repair_event: Callable[..., Any]
    notify: Callable[[str], None]
    ec2_client: Any = None
    s3_client: Any = None
    events_path: Path = field(default_factory=ledger_path)
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], str] = lambda: datetime.now(UTC).isoformat()
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    @classmethod
    def from_environment(cls) -> "WorkerDependencies":
        """Construye las dependencias reales desde el entorno y los módulos.

        Returns:
            Dependencias conectadas a las colas, los clientes y los
            servicios reales del proyecto.

        Raises:
            ValueError: Si la configuración de las colas no es válida.
            ClientError: Si SQS rechaza la preparación de las colas.
        """
        from fenix.config.settings import (
            get_ec2_client,
            get_s3_client,
            get_sqs_client,
        )

        queues = sqs.ensure_queues()
        client = get_sqs_client()

        return cls(
            queue_url=queues["queue_url"],
            receive_message=client.receive_message,
            delete_message=client.delete_message,
            retry_later=lambda receipt, count: sqs.retry_later(
                receipt,
                count,
                queues["queue_url"],
            ),
            load_inventory=state.load_inventory,
            validate_event=events.validate,
            repair_event=remediation.repair,
            notify=notifications.notify,
            ec2_client=get_ec2_client(),
            s3_client=get_s3_client(),
        )


def _extra(
    *,
    event_id: Any = None,
    message_id: Any = None,
    resource_id: Any = None,
    attempt: Any = None,
    action: Any = None,
    status: Any = None,
) -> dict[str, Any]:
    """Construye el contexto de logging permitido por el formateador JSON.

    Returns:
        Diccionario con exactamente las seis claves autorizadas.
    """
    return {
        "event_id": event_id,
        "message_id": message_id,
        "resource_id": resource_id,
        "attempt": attempt,
        "action": action,
        "status": status,
    }


def _receive_count(message: dict[str, Any]) -> int:
    """Lee ``ApproximateReceiveCount`` y lo normaliza a un entero positivo.

    Args:
        message: Entrega SQS con atributos opcionales.

    Returns:
        Cantidad de recepciones, o 1 si falta o es inválida.
    """
    attributes = message.get("Attributes")

    if not isinstance(attributes, dict):
        return 1

    raw_count = attributes.get(_RECEIVE_COUNT_ATTRIBUTE)

    try:
        count = int(raw_count)
    except (TypeError, ValueError):
        return 1

    return count if count >= 1 else 1


def _simulate_failures(event: dict[str, Any]) -> int:
    """Devuelve la cantidad de fallas simuladas del evento validado."""
    value = event.get("simulate_failures")

    if isinstance(value, bool) or not isinstance(value, int):
        return 0

    return value if value > 0 else 0


def _new_record(event: dict[str, Any], now: str) -> dict[str, Any]:
    """Crea el registro inicial por etapas de un evento.

    Args:
        event: Evento validado que se conserva como original.
        now: Marca temporal UTC para creación y actualización.

    Returns:
        Registro con los contadores de idempotencia en cero.
    """
    return {
        "event": event,
        "attempts": 0,
        "status": "pending",
        "repair_done": False,
        "repair_result": None,
        "notified": False,
        "created_at": now,
        "updated_at": now,
        "last_error": None,
    }


def _notification_text(event: dict[str, Any], record: dict[str, Any]) -> str:
    """Redacta el resumen público del aviso, sin cuerpos ni secretos.

    Args:
        event: Evento reparado.
        record: Registro durable con el resultado de la reparación.

    Returns:
        Texto de hasta 1000 caracteres con identificadores y estado.
    """
    result = record.get("repair_result")
    action = result.get("action") if isinstance(result, dict) else None
    status = result.get("status") if isinstance(result, dict) else None

    text = (
        "Fénix reparó el recurso "
        f"{event.get('resource_id')} (evento {event.get('event_id')}): "
        f"acción {action}, estado {status}."
    )

    return text[:1000]


def _persist(
    deps: WorkerDependencies,
    ledger: dict[str, Any],
    context: dict[str, Any],
) -> bool:
    """Guarda una etapa del ledger y confirma si quedó durable.

    Args:
        deps: Dependencias del worker.
        ledger: Estructura completa a persistir.
        context: Extras de logging autorizados del evento actual.

    Returns:
        ``True`` si el reemplazo atómico terminó; ``False`` si falló.
    """
    try:
        save_ledger(deps.events_path, ledger)
    except (LedgerError, OSError, TypeError, ValueError) as exc:
        deps.logger.error(
            f"no se pudo guardar el ledger: {type(exc).__name__}",
            extra={**context, "action": "ledger", "status": "ledger-error"},
        )
        return False

    return True


def _backoff(
    deps: WorkerDependencies,
    receipt_handle: str,
    receive_count: int,
    *,
    context: dict[str, Any] | None = None,
    event_id: str | None = None,
    message_id: str | None = None,
    resource_id: str | None = None,
) -> str:
    """Programa el reintento del mensaje y conserva la entrega sin ACK.

    Args:
        deps: Dependencias del worker.
        receipt_handle: Identificador actual de la recepción.
        receive_count: Cantidad de recepciones previas del mensaje.
        context: Extras de logging ya construidos para el evento actual.
        event_id: Identificador público del evento, si se conoce.
        message_id: Identificador de la entrega SQS, si se conoce.
        resource_id: Alias del recurso, si se conoce.

    Returns:
        ``STATUS_RETRY`` en todos los casos, porque el mensaje no se elimina.
    """
    if context is not None:
        event_id = context.get("event_id")
        message_id = context.get("message_id")
        resource_id = context.get("resource_id")

    extra = _extra(
        event_id=event_id,
        message_id=message_id,
        resource_id=resource_id,
    )

    try:
        delay = deps.retry_later(receipt_handle, receive_count)
    except Exception as exc:
        deps.logger.error(
            f"fallo al cambiar la visibilidad: {type(exc).__name__}",
            extra={**extra, "action": "retry", "status": "visibility-error"},
        )
        return STATUS_RETRY

    deps.logger.debug(
        f"reintento programado en {delay} segundos",
        extra={
            **extra,
            "attempt": receive_count,
            "action": "retry",
            "status": "retry-scheduled",
        },
    )

    return STATUS_RETRY


def _acknowledge(
    message: dict[str, Any],
    deps: WorkerDependencies,
    *,
    event_id: str | None = None,
    resource_id: str | None = None,
    attempt: int | None = None,
) -> str:
    """Elimina la entrega SQS usando exclusivamente su ``ReceiptHandle``.

    Args:
        message: Entrega SQS ya verificada.
        deps: Dependencias del worker.
        event_id: Identificador público del evento, si se conoce.
        resource_id: Alias del recurso, si se conoce.
        attempt: Cantidad de recepciones de la entrega actual.

    Returns:
        ``STATUS_ACKNOWLEDGED`` si SQS confirma; ``STATUS_RETRY`` si no.
    """
    raw_message_id = message.get("MessageId")
    message_id = raw_message_id if isinstance(raw_message_id, str) else None
    context = _extra(
        event_id=event_id,
        message_id=message_id,
        resource_id=resource_id,
        attempt=attempt,
    )

    try:
        deps.delete_message(
            QueueUrl=deps.queue_url,
            ReceiptHandle=message.get("ReceiptHandle"),
        )
    except Exception as exc:
        deps.logger.error(
            f"fallo al confirmar el mensaje: {type(exc).__name__}",
            extra={**context, "action": "ack", "status": "ack-failed"},
        )
        return STATUS_RETRY

    deps.logger.info(
        "mensaje confirmado",
        extra={**context, "action": "ack", "status": STATUS_ACKNOWLEDGED},
    )

    return STATUS_ACKNOWLEDGED


def handle_event(
    event: dict[str, Any],
    inventory: dict[str, Any],
    *,
    deps: WorkerDependencies,
    message_id: str | None = None,
) -> str:
    """Ejecuta el caso de negocio por etapas durables e idempotentes.

    Guarda ``attempts`` antes de reparar, ``repair_done`` y ``repair_result``
    antes de avisar, y ``notified`` antes de permitir el ACK.

    Args:
        event: Evento ya validado y resuelto contra el inventario.
        inventory: Recursos autorizados del laboratorio.
        deps: Dependencias del worker.
        message_id: Identificador de la entrega, solo para trazabilidad.

    Returns:
        ``"completed"``, ``"duplicate"``, ``"conflict"`` o ``"retry"``.

    Notes:
        Esta función no modifica la cola SQS; el ACK y el backoff pertenecen
        a ``process_message``.
    """
    if not isinstance(event, dict):
        deps.logger.error(
            "evento inválido: no es un objeto",
            extra=_extra(
                message_id=message_id,
                action="handle",
                status="invalid-event",
            ),
        )
        return "retry"

    event_id = event.get("event_id")

    if not isinstance(event_id, str) or not event_id.strip():
        deps.logger.error(
            "evento sin event_id; no se registra la etapa",
            extra=_extra(
                message_id=message_id,
                action="handle",
                status="invalid-event",
            ),
        )
        return "retry"

    raw_resource_id = event.get("resource_id")
    context = _extra(
        event_id=event_id,
        message_id=message_id,
        resource_id=raw_resource_id if isinstance(raw_resource_id, str) else None,
    )

    try:
        ledger = load_ledger(deps.events_path)
    except (LedgerError, OSError, ValueError) as exc:
        deps.logger.error(
            f"ledger ilegible: {type(exc).__name__}",
            extra={**context, "action": "ledger", "status": "ledger-error"},
        )
        return "retry"

    records = ledger["events"]
    record = records.get(event_id)

    if record is None:
        record = _new_record(event, deps.clock())
        records[event_id] = record
    elif not isinstance(record, dict):
        deps.logger.error(
            "registro de ledger inválido para el event_id",
            extra={**context, "action": "ledger", "status": "ledger-error"},
        )
        return "retry"
    elif record.get("event") != event:
        deps.logger.warning(
            "event_id reutilizado con otro contenido; se conserva el primero",
            extra={
                **context,
                "attempt": record.get("attempts"),
                "action": "ledger",
                "status": "conflict",
            },
        )
        return "conflict"

    if record.get("notified") is True:
        deps.logger.info(
            "evento ya notificado; no se repite el trabajo",
            extra={
                **context,
                "attempt": record.get("attempts"),
                "action": "handle",
                "status": STATUS_DUPLICATE,
            },
        )
        return "duplicate"

    if record.get("repair_done") is not True:
        try:
            attempts = int(record.get("attempts", 0)) + 1
        except (TypeError, ValueError):
            attempts = 1

        record["attempts"] = attempts
        record["updated_at"] = deps.clock()

        if not _persist(deps, ledger, context):
            return "retry"

        simulate_failures = _simulate_failures(event)
        if attempts <= simulate_failures:
            record["status"] = "retrying"
            record["last_error"] = "falla simulada"
            record["updated_at"] = deps.clock()

            if not _persist(deps, ledger, context):
                return "retry"

            deps.logger.warning(
                f"falla simulada en el intento {attempts}",
                extra={
                    **context,
                    "attempt": attempts,
                    "action": "repair",
                    "status": "simulated-failure",
                },
            )
            return "retry"

        try:
            result = deps.repair_event(
                event,
                inventory,
                ec2_client=deps.ec2_client,
                s3_client=deps.s3_client,
            )
        except RepairPermanentError as exc:
            record["status"] = "failed"
            record["last_error"] = f"{type(exc).__name__}: {exc}"
            record["updated_at"] = deps.clock()
            _persist(deps, ledger, context)

            deps.logger.error(
                "reparación permanente fallida; el mensaje queda para la DLQ",
                extra={
                    **context,
                    "attempt": attempts,
                    "action": "repair",
                    "status": "failed",
                },
            )
            return "retry"
        except RepairTransientError as exc:
            record["status"] = "retrying"
            record["last_error"] = f"{type(exc).__name__}: {exc}"
            record["updated_at"] = deps.clock()
            _persist(deps, ledger, context)

            deps.logger.warning(
                "reparación transitoria fallida; se reintenta",
                extra={
                    **context,
                    "attempt": attempts,
                    "action": "repair",
                    "status": "retrying",
                },
            )
            return "retry"
        except Exception as exc:
            record["status"] = "retrying"
            record["last_error"] = type(exc).__name__
            record["updated_at"] = deps.clock()
            _persist(deps, ledger, context)

            deps.logger.error(
                f"reparación inesperada fallida: {type(exc).__name__}",
                extra={
                    **context,
                    "attempt": attempts,
                    "action": "repair",
                    "status": "retrying",
                },
            )
            return "retry"

        record["repair_done"] = True
        record["repair_result"] = result.to_dict()
        record["status"] = "repair_done"
        record["updated_at"] = deps.clock()

        if not _persist(deps, ledger, context):
            return "retry"

        deps.logger.info(
            "reparación verificada",
            extra={
                **context,
                "attempt": attempts,
                "action": "repair",
                "status": result.status,
            },
        )

    if record.get("notified") is not True:
        try:
            deps.notify(_notification_text(event, record))
        except Exception as exc:
            record["status"] = "retrying"
            record["last_error"] = type(exc).__name__
            record["updated_at"] = deps.clock()
            _persist(deps, ledger, context)

            deps.logger.warning(
                f"aviso pendiente; la reparación no se repite: {type(exc).__name__}",
                extra={
                    **context,
                    "attempt": record.get("attempts"),
                    "action": "notify",
                    "status": "notify-pending",
                },
            )
            return "retry"

        record["notified"] = True
        record["status"] = "completed"
        record["updated_at"] = deps.clock()

        if not _persist(deps, ledger, context):
            return "retry"

        deps.logger.info(
            "aviso confirmado",
            extra={**context, "action": "notify", "status": "notified"},
        )
        return "completed"

    return "duplicate"


def process_message(message: dict[str, Any], deps: WorkerDependencies) -> str:
    """Procesa una entrega SQS y confirma solo el trabajo verificado.

    Args:
        message: Entrega devuelta por ``receive_message``.
        deps: Dependencias del worker.

    Returns:
        ``STATUS_ACKNOWLEDGED`` si el trabajo quedó verificado y SQS confirmó;
        ``STATUS_RETRY`` en cualquier otro caso, sin ACK.
    """
    if not isinstance(message, dict):
        deps.logger.error(
            "entrega SQS inválida: no es un objeto",
            extra=_extra(action="process", status="invalid-message"),
        )
        return STATUS_RETRY

    raw_message_id = message.get("MessageId")
    message_id = raw_message_id if isinstance(raw_message_id, str) else None
    receipt_handle = message.get("ReceiptHandle")
    receive_count = _receive_count(message)

    if not isinstance(receipt_handle, str) or not receipt_handle.strip():
        deps.logger.error(
            "entrega SQS sin ReceiptHandle; no se confirma",
            extra=_extra(
                message_id=message_id,
                action="process",
                status="invalid-receipt",
            ),
        )
        return STATUS_RETRY

    event_id: str | None = None

    try:
        body = message.get("Body")

        try:
            payload = json.loads(body)
        except (TypeError, ValueError):
            deps.logger.warning(
                "cuerpo SQS sin JSON válido; se conserva el mensaje",
                extra=_extra(
                    message_id=message_id,
                    action="process",
                    status="invalid-json",
                ),
            )
            return _backoff(
                deps,
                receipt_handle,
                receive_count,
                message_id=message_id,
            )

        if not isinstance(payload, dict):
            deps.logger.warning(
                "el cuerpo SQS no es un objeto JSON; se conserva el mensaje",
                extra=_extra(
                    message_id=message_id,
                    action="process",
                    status="invalid-contract",
                ),
            )
            return _backoff(
                deps,
                receipt_handle,
                receive_count,
                message_id=message_id,
            )

        if isinstance(payload.get("event_id"), str):
            event_id = payload["event_id"]

        raw_resource_id = payload.get("resource_id")
        resource_id = raw_resource_id if isinstance(raw_resource_id, str) else None
        context = _extra(
            event_id=event_id,
            message_id=message_id,
            resource_id=resource_id,
            attempt=receive_count,
        )

        try:
            ledger = load_ledger(deps.events_path)
        except (LedgerError, OSError, ValueError) as exc:
            deps.logger.error(
                f"ledger ilegible: {type(exc).__name__}",
                extra={
                    **context,
                    "action": "ledger",
                    "status": "ledger-error",
                },
            )
            return _backoff(
                deps,
                receipt_handle,
                receive_count,
                context=context,
            )

        record = ledger["events"].get(event_id) if event_id else None

        if record is not None:
            if not isinstance(record, dict):
                deps.logger.error(
                    "registro de ledger inválido para el event_id",
                    extra={
                        **context,
                        "action": "ledger",
                        "status": "ledger-error",
                    },
                )
                return _backoff(
                    deps,
                    receipt_handle,
                    receive_count,
                    context=context,
                )

            if record.get("event") != payload:
                deps.logger.warning(
                    "conflicto de event_id; no se repara ni se confirma",
                    extra={
                        **context,
                        "action": "ledger",
                        "status": "conflict",
                    },
                )
                return _backoff(
                    deps,
                    receipt_handle,
                    receive_count,
                    context=context,
                )

            if record.get("notified") is True:
                deps.logger.info(
                    "evento ya completado; solo se confirma",
                    extra={
                        **context,
                        "action": "ack",
                        "status": STATUS_DUPLICATE,
                    },
                )
                return _acknowledge(
                    message,
                    deps,
                    event_id=event_id,
                    resource_id=resource_id,
                    attempt=receive_count,
                )

        try:
            inventory = deps.load_inventory()

            if not isinstance(inventory, dict):
                raise ValueError("el inventario debe ser un objeto")

            resources = inventory.get("resources", inventory)

            if not isinstance(resources, dict):
                raise ValueError("la sección resources no es válida")
        except (OSError, TypeError, ValueError) as exc:
            deps.logger.error(
                f"inventario no disponible: {type(exc).__name__}",
                extra={
                    **context,
                    "action": "inventory",
                    "status": "inventory-error",
                },
            )
            return _backoff(
                deps,
                receipt_handle,
                receive_count,
                context=context,
            )

        try:
            validated = deps.validate_event(payload, resources)
        except ValidationError:
            deps.logger.warning(
                "contrato de evento inválido; se conserva el mensaje",
                extra={
                    **context,
                    "action": "validate",
                    "status": "invalid-contract",
                },
            )
            return _backoff(
                deps,
                receipt_handle,
                receive_count,
                context=context,
            )
        except Exception as exc:
            deps.logger.error(
                f"fallo inesperado al validar: {type(exc).__name__}",
                extra={
                    **context,
                    "action": "validate",
                    "status": "validation-error",
                },
            )
            return _backoff(
                deps,
                receipt_handle,
                receive_count,
                context=context,
            )

        if not isinstance(validated, dict):
            deps.logger.error(
                "la validación no devolvió un objeto; se conserva el mensaje",
                extra={
                    **context,
                    "action": "validate",
                    "status": "invalid-contract",
                },
            )
            return _backoff(
                deps,
                receipt_handle,
                receive_count,
                context=context,
            )

        outcome = handle_event(
            validated,
            resources,
            deps=deps,
            message_id=message_id,
        )

        if outcome in {"completed", "duplicate"}:
            return _acknowledge(
                message,
                deps,
                event_id=event_id,
                resource_id=resource_id,
                attempt=receive_count,
            )

        return _backoff(
            deps,
            receipt_handle,
            receive_count,
            context=context,
        )

    except Exception as exc:
        deps.logger.error(
            f"fallo inesperado al procesar la entrega: {type(exc).__name__}",
            extra=_extra(
                event_id=event_id,
                message_id=message_id,
                action="process",
                status="error",
            ),
        )
        return _backoff(
            deps,
            receipt_handle,
            receive_count,
            event_id=event_id,
            message_id=message_id,
        )


def run(
    *,
    once: bool = False,
    deps: WorkerDependencies | None = None,
) -> int:
    """Consume mensajes de SQS y confirma solo los trabajos completados.

    Args:
        once: Si es ``True`` realiza una sola consulta y termina.
        deps: Dependencias inyectables; se construyen del entorno si faltan.

    Returns:
        Cantidad de mensajes confirmados con ``delete_message``.

    Notes:
        ``KeyboardInterrupt`` se propaga: el llamador apaga el listener de
        logging en su propio ``finally``.
    """
    if deps is None:
        deps = WorkerDependencies.from_environment()

    acknowledged = 0

    while True:
        try:
            response = deps.receive_message(
                QueueUrl=deps.queue_url,
                MaxNumberOfMessages=MAX_MESSAGES,
                WaitTimeSeconds=WAIT_TIME_SECONDS,
                VisibilityTimeout=VISIBILITY_TIMEOUT,
                AttributeNames=[_RECEIVE_COUNT_ATTRIBUTE],
            )
        except Exception as exc:
            deps.logger.error(
                f"fallo de transporte al consultar SQS: {type(exc).__name__}",
                extra=_extra(action="receive", status="transport-error"),
            )

            if once:
                return acknowledged

            deps.sleep(TRANSPORT_RETRY_SECONDS)
            continue

        if not isinstance(response, dict):
            deps.logger.error(
                "respuesta de SQS con formato inesperado",
                extra=_extra(action="receive", status="invalid-response"),
            )

            if once:
                return acknowledged

            deps.sleep(TRANSPORT_RETRY_SECONDS)
            continue

        messages = response.get("Messages")

        if messages is None:
            messages = []
        elif not isinstance(messages, list):
            deps.logger.error(
                "la lista Messages de SQS no es válida",
                extra=_extra(action="receive", status="invalid-response"),
            )

            if once:
                return acknowledged

            deps.sleep(TRANSPORT_RETRY_SECONDS)
            continue

        for message in messages:
            if process_message(message, deps) == STATUS_ACKNOWLEDGED:
                acknowledged += 1

        if once:
            return acknowledged
