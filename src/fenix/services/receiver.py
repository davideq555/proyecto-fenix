"""Receptor HTTP del webhook de Fénix."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from fenix.utils.crypto import (
    WebhookConfigurationError,
    WebhookSignatureError,
    verify,
)

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 16 * 1024
SIGNATURE_HEADER = "x-webhook-signature"


@dataclass(frozen=True)
class WebhookDependencies:
    """Dependencias externas del receptor, inyectables para las pruebas."""

    secret: str
    load_inventory: Callable[[], dict[str, Any]]
    validate_event: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
    publish_event: Callable[[dict[str, Any]], str]
    validation_exceptions: tuple[type[Exception], ...] = (ValueError,)
    now: Callable[[], float] = time.time

    @classmethod
    def from_environment(cls) -> "WebhookDependencies":
        """Construye las dependencias reales desde el entorno y los módulos."""
        from fenix.services import events, sqs
        from fenix.utils import errors

        return cls(
            secret=os.environ.get("WEBHOOK_SECRET", ""),
            load_inventory=_load_inventory,
            validate_event=getattr(events, "validate", _validate_not_implemented),
            publish_event=(
                _publish_with_sqs
                if hasattr(sqs, "send_message")
                else _publish_not_implemented
            ),
            validation_exceptions=_validation_exceptions(errors),
        )


def _validation_exceptions(errors_module: Any) -> tuple[type[Exception], ...]:
    """Resuelve el error de validación del dominio o usa ``ValueError``."""
    candidate = getattr(errors_module, "ValidationError", None)
    if isinstance(candidate, type) and issubclass(candidate, Exception):
        return (candidate,)
    return (ValueError,)


def _load_inventory() -> dict[str, Any]:
    """Lee el inventario local de recursos del laboratorio."""
    path = Path(os.environ.get("FENIX_INVENTORY_PATH", "data/inventory.json"))
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("el inventario debe ser un objeto JSON")
    return data


def _validate_not_implemented(
    event: dict[str, Any], inventory: dict[str, Any]
) -> dict[str, Any]:
    """Señala que la validación del dominio todavía no está implementada."""
    raise NotImplementedError("events.validate no está implementado todavía")


def _publish_not_implemented(event: dict[str, Any]) -> str:
    """Señala que la publicación en SQS todavía no está implementada."""
    raise NotImplementedError("sqs.send_message no está implementado todavía")


def _publish_with_sqs(event: dict[str, Any]) -> str:
    """Publica el evento en SQS y devuelve el ``MessageId`` confirmado."""
    from fenix.services import sqs

    response = sqs.send_message(event)
    if isinstance(response, str):
        return response
    return str(response["MessageId"])


async def _read_limited_body(request: Request, limit: int) -> bytes | None:
    """Lee el cuerpo hasta el límite indicado o devuelve ``None`` si lo supera."""
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > limit:
                return None
        except ValueError:
            pass

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _json_payload(raw_body: bytes) -> Any:
    """Decodifica el cuerpo como UTF-8 y lo convierte desde JSON."""
    return json.loads(raw_body.decode("utf-8"))


def create_app(deps: WebhookDependencies) -> FastAPI:
    """Crea la aplicación FastAPI del receptor con las dependencias dadas."""
    app = FastAPI(title="Fenix Webhook", version="0.1.0")

    @app.post("/webhook", status_code=202)
    async def webhook(request: Request) -> JSONResponse:
        """Autentica, valida y publica un evento de laboratorio."""
        raw_body = await _read_limited_body(request, MAX_BODY_BYTES)
        if raw_body is None:
            logger.warning(
                "webhook rechazado",
                extra={"status": 413, "action": "webhook"},
            )
            raise HTTPException(status_code=413, detail="cuerpo mayor a 16 KiB")

        headers = request.headers.getlist(SIGNATURE_HEADER)
        if len(headers) != 1:
            logger.warning(
                "webhook rechazado",
                extra={"status": 401, "action": "webhook"},
            )
            raise HTTPException(status_code=401, detail="firma ausente o duplicada")

        try:
            verify(deps.secret, headers[0], raw_body, now=deps.now())
        except WebhookSignatureError as exc:
            logger.warning(
                "webhook rechazado",
                extra={"status": 401, "action": "webhook", "reason": exc.motivo},
            )
            raise HTTPException(status_code=401, detail="firma inválida") from exc
        except WebhookConfigurationError as exc:
            logger.error(
                "webhook mal configurado",
                extra={"status": 500, "action": "webhook"},
            )
            raise HTTPException(
                status_code=500, detail="WEBHOOK_SECRET no configurado"
            ) from exc

        try:
            payload = _json_payload(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning(
                "webhook rechazado",
                extra={"status": 400, "action": "webhook"},
            )
            raise HTTPException(status_code=400, detail="JSON inválido") from exc

        if not isinstance(payload, dict):
            logger.warning(
                "webhook rechazado",
                extra={"status": 422, "action": "webhook"},
            )
            raise HTTPException(
                status_code=422, detail="el evento debe ser un objeto JSON"
            )

        try:
            inventory = deps.load_inventory()
        except (OSError, ValueError) as exc:
            logger.error(
                "inventario no disponible",
                extra={"status": 500, "action": "webhook"},
            )
            raise HTTPException(
                status_code=500, detail="inventario no disponible"
            ) from exc

        try:
            event = deps.validate_event(payload, inventory)
        except deps.validation_exceptions as exc:
            logger.warning(
                "webhook rechazado",
                extra={"status": 422, "action": "webhook"},
            )
            raise HTTPException(
                status_code=422, detail="contrato de evento inválido"
            ) from exc

        try:
            message_id = await run_in_threadpool(deps.publish_event, event)
        except Exception as exc:
            logger.error(
                "fallo al publicar en SQS",
                extra={"status": 503, "action": "publish"},
            )
            raise HTTPException(status_code=503, detail="SQS no disponible") from exc

        if not message_id:
            logger.error(
                "SQS no confirmó el mensaje",
                extra={"status": 503, "action": "publish"},
            )
            raise HTTPException(
                status_code=503, detail="SQS no confirmó el mensaje"
            )

        event_id = event.get("event_id") if isinstance(event, dict) else None
        if not event_id:
            logger.error(
                "evento validado sin event_id",
                extra={"status": 500, "action": "publish"},
            )
            raise HTTPException(
                status_code=500, detail="evento validado sin event_id"
            )

        logger.info(
            "webhook aceptado",
            extra={
                "event_id": event_id,
                "message_id": str(message_id),
                "resource_id": event.get("resource_id"),
                "status": "accepted",
                "action": "publish",
            },
        )
        return JSONResponse(
            status_code=202,
            content={"event_id": event_id, "message_id": str(message_id)},
        )

    return app


app = create_app(WebhookDependencies.from_environment())
