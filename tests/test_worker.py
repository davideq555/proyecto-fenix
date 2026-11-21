"""Pruebas unitarias del consumo SQS y del ledger por etapas del worker."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from fenix.services import worker
from fenix.services.remediation import (
    RepairPermanentError,
    RepairResult,
    RepairTransientError,
)
from fenix.utils.errors import RetryableError, ValidationError

QUEUE_URL = "http://localhost:4566/000000000000/fenix-events"
CLOCK_TEXT = "2026-11-19T12:00:00+00:00"


class FakeSQS:
    """Registra las operaciones SQS y permite inyectar fallas."""

    def __init__(
        self,
        *,
        responses: list[Any] | None = None,
        receive_error: Exception | None = None,
        delete_error: Exception | None = None,
        retry_error: Exception | None = None,
    ) -> None:
        """Inicializa las respuestas y los errores simulados."""
        self.responses = (
            list(responses) if responses is not None else [{"Messages": []}]
        )
        self.receive_error = receive_error
        self.delete_error = delete_error
        self.retry_error = retry_error
        self.receives: list[dict[str, Any]] = []
        self.deletes: list[dict[str, Any]] = []
        self.retries: list[tuple[str, int]] = []

    def receive_message(self, **parameters: Any) -> Any:
        """Devuelve la siguiente respuesta programada o falla el transporte."""
        self.receives.append(parameters)
        if self.receive_error is not None:
            raise self.receive_error
        if self.responses:
            return self.responses.pop(0)
        return {"Messages": []}

    def delete_message(self, **parameters: Any) -> dict[str, Any]:
        """Registra el ACK y falla si se configuró un error."""
        self.deletes.append(parameters)
        if self.delete_error is not None:
            raise self.delete_error
        return {}

    def retry_later(self, receipt_handle: str, receive_count: int) -> int:
        """Registra el backoff y falla si se configuró un error."""
        self.retries.append((receipt_handle, receive_count))
        if self.retry_error is not None:
            raise self.retry_error
        return min(30, 2**receive_count)


class FakeRepair:
    """Reparación simulada que registra llamadas y errores programados."""

    def __init__(
        self,
        *,
        result: RepairResult | None = None,
        errors: list[Exception | None] | None = None,
    ) -> None:
        """Inicializa el resultado y la secuencia de errores."""
        self.result = result or RepairResult(
            resource_id="nodo-01",
            action="start",
            changed=True,
            status="running",
        )
        self.errors = list(errors) if errors is not None else []
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        event: dict[str, Any],
        inventory: dict[str, Any],
        *,
        ec2_client: Any = None,
        s3_client: Any = None,
    ) -> RepairResult:
        """Registra la llamada y devuelve el resultado o lanza el error."""
        self.calls.append(
            {
                "event": event,
                "inventory": inventory,
                "ec2_client": ec2_client,
                "s3_client": s3_client,
            }
        )
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error
        return self.result


class FakeNotify:
    """Aviso simulado que registra los textos enviados."""

    def __init__(self, *, errors: list[Exception | None] | None = None) -> None:
        """Inicializa la secuencia de errores de aviso."""
        self.errors = list(errors) if errors is not None else []
        self.texts: list[str] = []

    def __call__(self, text: str) -> None:
        """Registra el texto y falla si corresponde."""
        self.texts.append(text)
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error


def _inventory() -> dict[str, Any]:
    """Devuelve un inventario con un nodo EC2 autorizado."""
    return {"nodo-01": {"type": "ec2", "id": "i-123"}}


def _validar_ok(event: dict[str, Any], inventory: dict[str, Any]) -> dict:
    """Valida un evento devolviendo una copia, sin revisar el inventario."""
    return dict(event)


def _event(**cambios: Any) -> dict[str, Any]:
    """Devuelve un evento completo del contrato con los cambios indicados."""
    event = {
        "schema_version": 1,
        "event_id": "evt-001",
        "event_type": "instance.stopped",
        "resource_id": "nodo-01",
        "occurred_at": "2026-11-19T00:00:00Z",
        "simulate_failures": 0,
    }
    event.update(cambios)
    return event


def _message(
    event: dict[str, Any] | None = None,
    *,
    body: str | None = None,
    receipt: str = "recibo-001",
    message_id: str = "msg-001",
    receive_count: int = 1,
) -> dict[str, Any]:
    """Construye una entrega SQS con cuerpo JSON y atributos de recepción."""
    return {
        "MessageId": message_id,
        "ReceiptHandle": receipt,
        "Body": body if body is not None else json.dumps(event or _event()),
        "Attributes": {"ApproximateReceiveCount": str(receive_count)},
    }


def _deps(
    tmp_path: Path,
    *,
    fake_sqs: FakeSQS | None = None,
    repair: FakeRepair | None = None,
    notify: FakeNotify | None = None,
    validate: Any = None,
    **overrides: Any,
) -> worker.WorkerDependencies:
    """Construye dependencias con falsos por defecto y overrides puntuales."""
    fake_sqs = fake_sqs if fake_sqs is not None else FakeSQS()
    repair = repair if repair is not None else FakeRepair()
    notify = notify if notify is not None else FakeNotify()
    base = worker.WorkerDependencies(
        queue_url=QUEUE_URL,
        receive_message=fake_sqs.receive_message,
        delete_message=fake_sqs.delete_message,
        retry_later=fake_sqs.retry_later,
        load_inventory=lambda: {"resources": _inventory()},
        validate_event=validate or _validar_ok,
        repair_event=repair,
        notify=notify,
        ec2_client="ec2-falso",
        s3_client="s3-falso",
        events_path=tmp_path / "events.json",
        sleep=lambda _segundos: None,
        clock=lambda: CLOCK_TEXT,
        logger=logging.getLogger("fenix.pruebas.worker"),
    )
    return replace(base, **overrides)


def _record(event: dict[str, Any], **cambios: Any) -> dict[str, Any]:
    """Devuelve un registro de ledger completo con los cambios indicados."""
    record = {
        "event": event,
        "attempts": 1,
        "status": "repair_done",
        "repair_done": True,
        "repair_result": {
            "resource_id": "nodo-01",
            "action": "start",
            "changed": True,
            "status": "running",
        },
        "notified": False,
        "created_at": CLOCK_TEXT,
        "updated_at": CLOCK_TEXT,
        "last_error": None,
    }
    record.update(cambios)
    return record


def _seed_ledger(
    path: Path, event_id: str, record: dict[str, Any]
) -> None:
    """Escribe un ledger con un único registro para probar reentregas."""
    path.write_text(
        json.dumps(
            {"version": 1, "events": {event_id: record}},
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def test_ledger_path_respeta_variable_de_entorno(monkeypatch, tmp_path):
    destino = tmp_path / "ledger" / "events.json"
    monkeypatch.setenv("FENIX_EVENTS_PATH", str(destino))

    assert worker.ledger_path() == destino


def test_ledger_path_usa_la_raiz_del_proyecto(monkeypatch, tmp_path):
    monkeypatch.delenv("FENIX_EVENTS_PATH", raising=False)
    monkeypatch.setattr(worker, "PROJECT_ROOT", tmp_path)

    assert worker.ledger_path() == tmp_path / "data" / "events.json"


def test_load_ledger_devuelve_estructura_vacia_si_no_existe(tmp_path):
    assert worker.load_ledger(tmp_path / "inexistente.json") == {
        "version": 1,
        "events": {},
    }


def test_load_ledger_rechaza_lista_de_nivel_superior(tmp_path):
    ruta = tmp_path / "events.json"
    ruta.write_text("[]", encoding="utf-8")

    with pytest.raises(worker.LedgerError):
        worker.load_ledger(ruta)


def test_load_ledger_rechaza_events_que_no_es_objeto(tmp_path):
    ruta = tmp_path / "events.json"
    ruta.write_text('{"version": 1, "events": []}', encoding="utf-8")

    with pytest.raises(worker.LedgerError):
        worker.load_ledger(ruta)


def test_save_ledger_ida_y_vuelta_sin_temporales(tmp_path):
    ruta = tmp_path / "sub" / "events.json"
    ledger = {
        "version": 1,
        "events": {"evt-ñ": {"event": {"nota": "ñandú"}}},
    }

    worker.save_ledger(ruta, ledger)

    assert worker.load_ledger(ruta) == ledger
    texto = ruta.read_text(encoding="utf-8")
    assert texto.endswith("\n")
    assert "ñandú" in texto
    assert [elemento.name for elemento in ruta.parent.iterdir()] == [
        "events.json"
    ]


def test_procesa_mensaje_feliz_y_confirma(tmp_path):
    fake = FakeSQS()
    repair = FakeRepair()
    notify = FakeNotify()
    deps = _deps(tmp_path, fake_sqs=fake, repair=repair, notify=notify)

    resultado = worker.process_message(_message(), deps)

    assert resultado == worker.STATUS_ACKNOWLEDGED
    assert len(repair.calls) == 1
    assert len(notify.texts) == 1
    assert "evt-001" in notify.texts[0]
    assert "nodo-01" in notify.texts[0]
    assert len(notify.texts[0]) <= 1000
    assert fake.deletes == [
        {"QueueUrl": QUEUE_URL, "ReceiptHandle": "recibo-001"}
    ]
    assert fake.retries == []

    ledger = worker.load_ledger(deps.events_path)
    registro = ledger["events"]["evt-001"]
    assert registro["status"] == "completed"
    assert registro["repair_done"] is True
    assert registro["repair_result"] == repair.result.to_dict()
    assert registro["notified"] is True
    assert registro["attempts"] == 1


def test_f11_evento_completado_solo_confirma(tmp_path):
    fake = FakeSQS()
    repair = FakeRepair()
    notify = FakeNotify()
    deps = _deps(tmp_path, fake_sqs=fake, repair=repair, notify=notify)

    assert worker.process_message(_message(), deps) == (
        worker.STATUS_ACKNOWLEDGED
    )
    ledger_antes = deps.events_path.read_text(encoding="utf-8")

    resultado = worker.process_message(_message(receipt="recibo-002"), deps)

    assert resultado == worker.STATUS_ACKNOWLEDGED
    assert len(repair.calls) == 1
    assert len(notify.texts) == 1
    assert len(fake.deletes) == 2
    assert deps.events_path.read_text(encoding="utf-8") == ledger_antes


def test_f12_recupera_solo_el_aviso_pendiente(tmp_path):
    fake = FakeSQS()
    repair = FakeRepair()
    notify = FakeNotify()
    event = _event()
    deps = _deps(tmp_path, fake_sqs=fake, repair=repair, notify=notify)
    _seed_ledger(deps.events_path, event["event_id"], _record(event))

    resultado = worker.process_message(_message(event), deps)

    assert resultado == worker.STATUS_ACKNOWLEDGED
    assert repair.calls == []
    assert len(notify.texts) == 1
    assert len(fake.deletes) == 1
    registro = worker.load_ledger(deps.events_path)["events"]["evt-001"]
    assert registro["notified"] is True
    assert registro["status"] == "completed"


def test_f15_conflicto_conserva_el_primer_registro(tmp_path, caplog):
    fake = FakeSQS()
    repair = FakeRepair()
    notify = FakeNotify()
    original = _event(simulate_failures=0)
    distinto = _event(simulate_failures=1)
    deps = _deps(tmp_path, fake_sqs=fake, repair=repair, notify=notify)
    _seed_ledger(deps.events_path, original["event_id"], _record(original))
    ledger_antes = deps.events_path.read_text(encoding="utf-8")
    caplog.set_level(logging.DEBUG)

    resultado = worker.process_message(_message(distinto), deps)

    assert resultado == worker.STATUS_RETRY
    assert repair.calls == []
    assert notify.texts == []
    assert fake.deletes == []
    assert fake.retries == [("recibo-001", 1)]
    assert deps.events_path.read_text(encoding="utf-8") == ledger_antes
    assert any(
        getattr(registro, "status", None) == "conflict"
        for registro in caplog.records
    )


def test_f09_simula_fallas_antes_de_reparar(tmp_path):
    fake = FakeSQS()
    repair = FakeRepair()
    notify = FakeNotify()
    event = _event(simulate_failures=2)
    deps = _deps(tmp_path, fake_sqs=fake, repair=repair, notify=notify)

    primera = worker.process_message(
        _message(event, receipt="r-1", receive_count=1), deps
    )
    segunda = worker.process_message(
        _message(event, receipt="r-2", receive_count=2), deps
    )

    assert primera == worker.STATUS_RETRY
    assert segunda == worker.STATUS_RETRY
    assert repair.calls == []
    assert fake.deletes == []
    assert fake.retries == [("r-1", 1), ("r-2", 2)]
    registro = worker.load_ledger(deps.events_path)["events"]["evt-001"]
    assert registro["attempts"] == 2
    assert registro["status"] == "retrying"
    assert registro["last_error"] == "falla simulada"

    tercera = worker.process_message(
        _message(event, receipt="r-3", receive_count=3), deps
    )

    assert tercera == worker.STATUS_ACKNOWLEDGED
    assert len(repair.calls) == 1
    assert len(notify.texts) == 1
    assert len(fake.deletes) == 1
    registro = worker.load_ledger(deps.events_path)["events"]["evt-001"]
    assert registro["attempts"] == 3
    assert registro["status"] == "completed"


def test_f10_falla_persistente_nunca_repara_ni_confirma(tmp_path):
    fake = FakeSQS()
    repair = FakeRepair()
    notify = FakeNotify()
    event = _event(event_id="evt-f10", simulate_failures=5)
    deps = _deps(tmp_path, fake_sqs=fake, repair=repair, notify=notify)

    for numero in range(1, 6):
        resultado = worker.process_message(
            _message(
                event,
                receipt=f"r-{numero}",
                receive_count=numero,
            ),
            deps,
        )
        assert resultado == worker.STATUS_RETRY

    assert repair.calls == []
    assert notify.texts == []
    assert fake.deletes == []
    assert len(fake.retries) == 5
    registro = worker.load_ledger(deps.events_path)["events"]["evt-f10"]
    assert registro["attempts"] == 5
    assert registro["repair_done"] is False


@pytest.mark.parametrize(
    "body",
    ["{json roto", "[]", '"texto"', "42"],
)
def test_f08_cuerpo_invalido_no_repara_ni_confirma(tmp_path, body):
    fake = FakeSQS()
    repair = FakeRepair()
    notify = FakeNotify()
    deps = _deps(tmp_path, fake_sqs=fake, repair=repair, notify=notify)

    resultado = worker.process_message(_message(body=body), deps)

    assert resultado == worker.STATUS_RETRY
    assert repair.calls == []
    assert notify.texts == []
    assert fake.deletes == []
    assert fake.retries == [("recibo-001", 1)]


def test_revalidacion_fallida_no_repara_ni_confirma(tmp_path):
    fake = FakeSQS()
    repair = FakeRepair()
    deps = _deps(
        tmp_path,
        fake_sqs=fake,
        repair=repair,
        validate=lambda event, inventory: (_ for _ in ()).throw(
            ValidationError("contrato inválido")
        ),
    )

    resultado = worker.process_message(_message(), deps)

    assert resultado == worker.STATUS_RETRY
    assert repair.calls == []
    assert fake.deletes == []
    assert fake.retries == [("recibo-001", 1)]
    assert "evt-001" not in worker.load_ledger(deps.events_path)["events"]


def test_ledger_corrupto_no_tumba_el_servicio(tmp_path):
    fake = FakeSQS()
    repair = FakeRepair()
    deps = _deps(tmp_path, fake_sqs=fake, repair=repair)
    deps.events_path.write_text("{no es json", encoding="utf-8")

    resultado = worker.process_message(_message(), deps)

    assert resultado == worker.STATUS_RETRY
    assert repair.calls == []
    assert fake.deletes == []
    assert fake.retries == [("recibo-001", 1)]


def test_backoff_usa_approximate_receive_count(tmp_path):
    fake = FakeSQS()
    deps = _deps(tmp_path, fake_sqs=fake)

    worker.process_message(
        _message(body="{roto", receive_count=2),
        deps,
    )

    assert fake.retries == [("recibo-001", 2)]


def test_backoff_usa_uno_si_falta_el_atributo(tmp_path):
    fake = FakeSQS()
    deps = _deps(tmp_path, fake_sqs=fake)
    message = _message(body="{roto")
    message.pop("Attributes")

    worker.process_message(message, deps)

    assert fake.retries == [("recibo-001", 1)]


def test_entrega_sin_receipt_handle_no_confirma(tmp_path):
    fake = FakeSQS()
    repair = FakeRepair()
    deps = _deps(tmp_path, fake_sqs=fake, repair=repair)

    resultado = worker.process_message(_message(receipt=""), deps)

    assert resultado == worker.STATUS_RETRY
    assert fake.deletes == []
    assert fake.retries == []
    assert repair.calls == []


def test_fallo_de_visibilidad_mantiene_el_mensaje_sin_ack(tmp_path):
    fake = FakeSQS(retry_error=RuntimeError("visibilidad rechazada"))
    deps = _deps(tmp_path, fake_sqs=fake)

    resultado = worker.process_message(_message(body="{roto"), deps)

    assert resultado == worker.STATUS_RETRY
    assert fake.deletes == []
    assert len(fake.retries) == 1


def test_fallo_de_ack_conserva_el_trabajo_completado(tmp_path):
    fake = FakeSQS(delete_error=RuntimeError("SQS no disponible"))
    deps = _deps(tmp_path, fake_sqs=fake)

    resultado = worker.process_message(_message(), deps)

    assert resultado == worker.STATUS_RETRY
    assert len(fake.deletes) == 1
    registro = worker.load_ledger(deps.events_path)["events"]["evt-001"]
    assert registro["status"] == "completed"
    assert registro["notified"] is True


def test_f14_aviso_transitorio_no_repite_la_reparacion(tmp_path):
    fake = FakeSQS()
    repair = FakeRepair()
    notify = FakeNotify(errors=[RetryableError("429 temporal"), None])
    event = _event()
    deps = _deps(tmp_path, fake_sqs=fake, repair=repair, notify=notify)

    primera = worker.process_message(
        _message(event, receipt="r-1"), deps
    )

    assert primera == worker.STATUS_RETRY
    assert len(repair.calls) == 1
    assert fake.deletes == []
    registro = worker.load_ledger(deps.events_path)["events"]["evt-001"]
    assert registro["repair_done"] is True
    assert registro["notified"] is False
    assert registro["status"] == "retrying"
    assert registro["last_error"] == "RetryableError"

    segunda = worker.process_message(
        _message(event, receipt="r-2", receive_count=2), deps
    )

    assert segunda == worker.STATUS_ACKNOWLEDGED
    assert len(repair.calls) == 1
    assert len(notify.texts) == 2
    assert fake.deletes == [
        {"QueueUrl": QUEUE_URL, "ReceiptHandle": "r-2"}
    ]
    registro = worker.load_ledger(deps.events_path)["events"]["evt-001"]
    assert registro["notified"] is True
    assert registro["status"] == "completed"


def test_error_permanente_de_reparacion_marca_failed(tmp_path):
    fake = FakeSQS()
    repair = FakeRepair(errors=[RepairPermanentError("recurso terminado")])
    notify = FakeNotify()
    deps = _deps(tmp_path, fake_sqs=fake, repair=repair, notify=notify)

    resultado = worker.process_message(_message(), deps)

    assert resultado == worker.STATUS_RETRY
    assert notify.texts == []
    assert fake.deletes == []
    registro = worker.load_ledger(deps.events_path)["events"]["evt-001"]
    assert registro["status"] == "failed"
    assert registro["last_error"] == (
        "RepairPermanentError: recurso terminado"
    )


def test_error_transitorio_de_reparacion_marca_retrying(tmp_path):
    fake = FakeSQS()
    repair = FakeRepair(errors=[RepairTransientError("estado detenido")])
    notify = FakeNotify()
    deps = _deps(tmp_path, fake_sqs=fake, repair=repair, notify=notify)

    resultado = worker.process_message(_message(), deps)

    assert resultado == worker.STATUS_RETRY
    assert notify.texts == []
    assert fake.deletes == []
    registro = worker.load_ledger(deps.events_path)["events"]["evt-001"]
    assert registro["status"] == "retrying"
    assert registro["last_error"] == (
        "RepairTransientError: estado detenido"
    )


def test_run_once_con_cola_vacia(tmp_path):
    fake = FakeSQS(responses=[{"Messages": []}])
    deps = _deps(tmp_path, fake_sqs=fake)

    assert worker.run(once=True, deps=deps) == 0
    assert len(fake.receives) == 1


def test_run_once_procesa_los_parametros_de_sondeo(tmp_path):
    fake = FakeSQS(responses=[{"Messages": []}])
    deps = _deps(tmp_path, fake_sqs=fake)

    worker.run(once=True, deps=deps)

    assert fake.receives == [
        {
            "QueueUrl": QUEUE_URL,
            "MaxNumberOfMessages": 1,
            "WaitTimeSeconds": 10,
            "VisibilityTimeout": 45,
            "AttributeNames": ["ApproximateReceiveCount"],
        }
    ]


def test_run_once_con_un_mensaje(tmp_path):
    fake = FakeSQS(responses=[{"Messages": [_message()]}])
    deps = _deps(tmp_path, fake_sqs=fake)

    assert worker.run(once=True, deps=deps) == 1
    assert len(fake.receives) == 1
    assert len(fake.deletes) == 1


def test_run_once_con_dos_mensajes_en_una_respuesta(tmp_path):
    primero = _message(_event(event_id="evt-a"), receipt="r-a")
    segundo = _message(_event(event_id="evt-b"), receipt="r-b")
    fake = FakeSQS(responses=[{"Messages": [primero, segundo]}])
    deps = _deps(tmp_path, fake_sqs=fake)

    assert worker.run(once=True, deps=deps) == 2
    assert len(fake.deletes) == 2
    assert set(worker.load_ledger(deps.events_path)["events"]) == {
        "evt-a",
        "evt-b",
    }


def test_run_once_con_fallo_de_transporte(tmp_path):
    fake = FakeSQS(receive_error=RuntimeError("SQS caído"))
    deps = _deps(tmp_path, fake_sqs=fake)

    assert worker.run(once=True, deps=deps) == 0
    assert len(fake.receives) == 1
    assert fake.deletes == []


def test_run_once_con_respuesta_que_no_es_objeto(tmp_path):
    fake = FakeSQS(responses=[["no", "es", "un", "objeto"]])
    deps = _deps(tmp_path, fake_sqs=fake)

    assert worker.run(once=True, deps=deps) == 0
    assert len(fake.receives) == 1
    assert fake.deletes == []


def test_run_once_con_messages_que_no_es_lista(tmp_path):
    fake = FakeSQS(responses=[{"Messages": "no-es-lista"}])
    deps = _deps(tmp_path, fake_sqs=fake)

    assert worker.run(once=True, deps=deps) == 0
    assert len(fake.receives) == 1
    assert fake.deletes == []


def test_run_espera_dos_segundos_tras_fallo_de_transporte(tmp_path):
    esperas: list[float] = []

    def dormir(segundos: float) -> None:
        esperas.append(segundos)
        raise RuntimeError("corte de prueba")

    fake = FakeSQS(receive_error=RuntimeError("SQS caído"))
    deps = _deps(tmp_path, fake_sqs=fake, sleep=dormir)

    with pytest.raises(RuntimeError, match="corte de prueba"):
        worker.run(deps=deps)

    assert esperas == [worker.TRANSPORT_RETRY_SECONDS]


def test_ack_usa_receipt_handle_y_nunca_message_id(tmp_path):
    fake = FakeSQS()
    deps = _deps(tmp_path, fake_sqs=fake)

    worker.process_message(
        _message(receipt="recibo-unico", message_id="msg-unico"),
        deps,
    )

    assert fake.deletes == [
        {"QueueUrl": QUEUE_URL, "ReceiptHandle": "recibo-unico"}
    ]
    assert "MessageId" not in fake.deletes[0]


def test_ack_registra_estado_y_event_id(tmp_path, caplog):
    deps = _deps(tmp_path, logger=logging.getLogger("fenix.pruebas.worker"))
    caplog.set_level(logging.DEBUG)

    worker.process_message(_message(), deps)

    confirmaciones = [
        registro
        for registro in caplog.records
        if getattr(registro, "status", None) == "acknowledged"
    ]
    assert confirmaciones
    assert confirmaciones[0].action == "ack"
    assert confirmaciones[0].event_id == "evt-001"
    assert confirmaciones[0].attempt == 1
