"""Pruebas unitarias de la reparación EC2 convergente."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError

from fenix.services.remediation import (
    RepairPermanentError,
    RepairResult,
    RepairTransientError,
    repair,
)


class EC2Falso:
    """Cliente EC2 simulado con una secuencia de estados y errores."""

    def __init__(
        self,
        estados: list[str],
        *,
        error_inicio: Exception | None = None,
        error_descripcion: Exception | None = None,
    ) -> None:
        """Inicializa la secuencia de estados y los errores simulados."""
        self.estados = list(estados)
        self.error_inicio = error_inicio
        self.error_descripcion = error_descripcion
        self.inicios: list[list[str]] = []
        self.descripciones = 0

    def describe_instances(self, InstanceIds: list[str]) -> dict:
        """Devuelve el siguiente estado programado de la instancia."""
        self.descripciones += 1
        if self.error_descripcion is not None:
            raise self.error_descripcion
        estado = self.estados.pop(0) if len(self.estados) > 1 else self.estados[0]
        return {
            "Reservations": [
                {"Instances": [{"State": {"Name": estado}}]},
            ]
        }

    def start_instances(self, InstanceIds: list[str]) -> dict:
        """Registra el inicio de la instancia y devuelve una respuesta mínima."""
        self.inicios.append(InstanceIds)
        if self.error_inicio is not None:
            raise self.error_inicio
        return {"StartingInstances": []}


def _inventario() -> dict:
    """Devuelve un inventario con un nodo EC2 y un bucket S3."""
    return {
        "node-01": {
            "type": "ec2",
            "id": "i-123",
            "security_group_id": "sg-123",
            "expected_tags": {"ManagedBy": "ppa2", "Project": "fenix"},
        },
        "bucket-01": {
            "type": "s3",
            "id": "bucket-123",
            "expected_tags": {"Owner": "grupo-01"},
        },
    }


def _evento(**cambios: object) -> dict:
    """Devuelve un evento EC2 de prueba con los cambios indicados."""
    evento = {
        "event_id": "evt-node-001",
        "event_type": "instance.stopped",
        "resource_id": "node-01",
    }
    evento.update(cambios)
    return evento


def _error(codigo: str, operacion: str) -> ClientError:
    """Construye un ``ClientError`` de botocore con el código indicado."""
    return ClientError(
        {"Error": {"Code": codigo, "Message": "simulado"}},
        operacion,
    )


def test_instancia_detenida_arranca_y_verifica() -> None:
    """Arranca una instancia detenida y confirma el estado ``running``."""
    cliente = EC2Falso(["stopped", "pending", "running"])

    resultado = repair(
        _evento(),
        _inventario(),
        ec2_client=cliente,
        sleep=Mock(),
        max_attempts=3,
        poll_interval=0,
    )

    assert resultado == RepairResult("node-01", "start", True, "running")
    assert cliente.inicios == [["i-123"]]


def test_instancia_ya_corriendo_no_arranca() -> None:
    """No ejecuta ``start`` cuando la instancia ya está ``running``."""
    cliente = EC2Falso(["running"])

    resultado = repair(_evento(), _inventario(), ec2_client=cliente)

    assert resultado.changed is False
    assert resultado.action == "noop"
    assert cliente.inicios == []


def test_instancia_en_pending_espera_sin_arrancar() -> None:
    """Espera a que una instancia ``pending`` llegue a ``running``."""
    cliente = EC2Falso(["pending", "running"])

    resultado = repair(
        _evento(),
        _inventario(),
        ec2_client=cliente,
        sleep=Mock(),
        max_attempts=3,
        poll_interval=0,
    )

    assert resultado.changed is False
    assert resultado.action == "noop"
    assert cliente.inicios == []


def test_instancia_stopping_es_transitoria() -> None:
    """Trata el estado ``stopping`` como fallo recuperable."""
    cliente = EC2Falso(["stopping"])

    with pytest.raises(RepairTransientError):
        repair(_evento(), _inventario(), ec2_client=cliente)


def test_instancia_terminated_es_permanente() -> None:
    """Trata una instancia terminada como fallo permanente."""
    cliente = EC2Falso(["terminated"])

    with pytest.raises(RepairPermanentError):
        repair(_evento(), _inventario(), ec2_client=cliente)


def test_instancia_shutting_down_es_permanente() -> None:
    """Trata una instancia en apagado como fallo permanente."""
    cliente = EC2Falso(["shutting-down"])

    with pytest.raises(RepairPermanentError):
        repair(_evento(), _inventario(), ec2_client=cliente)


def test_estado_desconocido_es_permanente() -> None:
    """Rechaza un estado que no puede repararse."""
    cliente = EC2Falso(["hibernated"])

    with pytest.raises(RepairPermanentError):
        repair(_evento(), _inventario(), ec2_client=cliente)


def test_timeout_de_polling_es_transitorio() -> None:
    """Falla como transitorio si nunca se alcanza ``running``."""
    cliente = EC2Falso(["stopped"] + ["pending"] * 10)

    with pytest.raises(RepairTransientError):
        repair(
            _evento(),
            _inventario(),
            ec2_client=cliente,
            sleep=Mock(),
            max_attempts=3,
            poll_interval=0,
        )

    assert cliente.descripciones == 4


def test_error_al_arrancar_es_transitorio() -> None:
    """Convierte un error de ``start_instances`` en fallo recuperable."""
    cliente = EC2Falso(
        ["stopped"],
        error_inicio=_error("Throttling", "StartInstances"),
    )

    with pytest.raises(RepairTransientError):
        repair(_evento(), _inventario(), ec2_client=cliente)


def test_instancia_inexistente_es_permanente() -> None:
    """Trata una instancia inexistente como fallo permanente."""
    cliente = EC2Falso(
        ["stopped"],
        error_descripcion=_error("InvalidInstanceID.NotFound", "DescribeInstances"),
    )

    with pytest.raises(RepairPermanentError):
        repair(_evento(), _inventario(), ec2_client=cliente)


def test_alias_ausente_es_permanente() -> None:
    """Rechaza un alias que no está en el inventario."""
    inventario = _inventario()
    del inventario["node-01"]

    with pytest.raises(RepairPermanentError):
        repair(_evento(), inventario, ec2_client=EC2Falso(["stopped"]))


def test_tipo_incorrecto_es_permanente() -> None:
    """Rechaza un recurso cuyo tipo no coincide con el evento."""
    inventario = _inventario()
    inventario["node-01"]["type"] = "s3"

    with pytest.raises(RepairPermanentError):
        repair(_evento(), inventario, ec2_client=EC2Falso(["stopped"]))


def test_usa_el_id_del_inventario_y_no_del_evento() -> None:
    """Ignora cualquier ID del evento y usa el identificador del inventario."""
    cliente = EC2Falso(["stopped", "running"])
    evento = _evento(id="i-malicioso", instance_id="i-malicioso")

    repair(
        evento,
        _inventario(),
        ec2_client=cliente,
        sleep=Mock(),
        max_attempts=2,
        poll_interval=0,
    )

    assert cliente.inicios == [["i-123"]]


def test_recurso_sin_id_es_permanente() -> None:
    """Rechaza un recurso EC2 sin identificador."""
    inventario = _inventario()
    del inventario["node-01"]["id"]

    with pytest.raises(RepairPermanentError):
        repair(_evento(), inventario, ec2_client=EC2Falso(["stopped"]))


def test_evento_sin_resource_id_es_permanente() -> None:
    """Rechaza un evento sin alias de recurso."""
    evento = _evento()
    del evento["resource_id"]

    with pytest.raises(RepairPermanentError):
        repair(evento, _inventario(), ec2_client=EC2Falso(["stopped"]))


def test_inventario_anidado_en_resources() -> None:
    """Acepta un inventario envuelto en la clave ``resources``."""
    inventario = {"resources": _inventario()}
    cliente = EC2Falso(["running"])

    resultado = repair(_evento(), inventario, ec2_client=cliente)

    assert resultado.status == "running"


def test_evento_de_bucket_sin_cliente_s3_es_permanente() -> None:
    """Rechaza un evento S3 si falta el cliente inyectado."""
    evento = _evento(event_type="bucket.tags_missing", resource_id="bucket-01")

    with pytest.raises(RepairPermanentError):
        repair(evento, _inventario())


def test_resultado_se_convierte_a_dict() -> None:
    """Convierte el resultado en un diccionario para el ledger."""
    resultado = RepairResult("node-01", "start", True, "running")

    assert resultado.to_dict() == {
        "resource_id": "node-01",
        "action": "start",
        "changed": True,
        "status": "running",
    }


def test_credenciales_rechazadas_al_describir_son_permanentes() -> None:
    """Una credencial rechazada no se recupera reintentando."""
    cliente = EC2Falso(
        ["stopped"],
        error_descripcion=_error("UnauthorizedOperation", "DescribeInstances"),
    )

    with pytest.raises(RepairPermanentError):
        repair(_evento(), _inventario(), ec2_client=cliente, sleep=Mock())


def test_credenciales_rechazadas_al_arrancar_son_permanentes() -> None:
    """Un AccessDenied al arrancar no se recupera reintentando."""
    cliente = EC2Falso(
        ["stopped"],
        error_inicio=_error("AccessDenied", "StartInstances"),
    )

    with pytest.raises(RepairPermanentError):
        repair(_evento(), _inventario(), ec2_client=cliente, sleep=Mock())


def test_espera_excesiva_es_error_de_configuracion() -> None:
    """Rechaza una espera que supera la ventana de visibilidad del worker."""
    cliente = EC2Falso(["stopped"])

    with pytest.raises(ValueError):
        repair(
            _evento(),
            _inventario(),
            ec2_client=cliente,
            sleep=Mock(),
            max_attempts=100,
            poll_interval=1.0,
        )
