"""Pruebas unitarias de la reparación de etiquetas S3."""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from fenix.services.remediation import (
    RepairPermanentError,
    RepairResult,
    RepairTransientError,
    repair,
)

ESPERADAS = {
    "ManagedBy": "ppa2",
    "Project": "fenix",
    "Owner": "grupo-01",
}


class S3Falso:
    """Cliente S3 simulado con etiquetas en memoria."""

    def __init__(
        self,
        tags: dict[str, str] | None = None,
        *,
        error_lectura: Exception | None = None,
        error_escritura: Exception | None = None,
        verificar_con: dict[str, str] | None = None,
        respuesta_lectura: dict | None = None,
    ) -> None:
        """Inicializa las etiquetas y los errores simulados."""
        self.tags = dict(tags or {})
        self.error_lectura = error_lectura
        self.error_escritura = error_escritura
        self.verificar_con = verificar_con
        self.respuesta_lectura = respuesta_lectura
        self.escrituras: list[dict[str, str]] = []
        self.buckets: list[str] = []
        self.lecturas = 0

    def get_bucket_tagging(self, Bucket: str) -> dict:
        """Devuelve las etiquetas actuales o simula ``NoSuchTagSet``."""
        self.lecturas += 1
        if self.error_lectura is not None:
            raise self.error_lectura
        if self.respuesta_lectura is not None:
            return self.respuesta_lectura
        if not self.tags:
            raise _error("NoSuchTagSet", "GetBucketTagging")
        return {
            "TagSet": [{"Key": key, "Value": value} for key, value in self.tags.items()]
        }

    def put_bucket_tagging(self, Bucket: str, Tagging: dict) -> dict:
        """Registra la escritura y actualiza las etiquetas en memoria."""
        self.buckets.append(Bucket)
        if self.error_escritura is not None:
            raise self.error_escritura
        tags = {item["Key"]: item["Value"] for item in Tagging["TagSet"]}
        self.escrituras.append(tags)
        self.tags = dict(self.verificar_con) if self.verificar_con is not None else tags
        return {}


def _error(codigo: str, operacion: str) -> ClientError:
    """Construye un ``ClientError`` de botocore con el código indicado."""
    return ClientError(
        {"Error": {"Code": codigo, "Message": "simulado"}},
        operacion,
    )


def _inventario(**cambios: object) -> dict:
    """Devuelve un inventario con un bucket S3 de prueba."""
    bucket = {
        "type": "s3",
        "id": "bucket-123",
        "expected_tags": dict(ESPERADAS),
    }
    bucket.update(cambios)
    return {"bucket-01": bucket}


def _evento(**cambios: object) -> dict:
    """Devuelve un evento S3 de prueba con los cambios indicados."""
    evento = {
        "event_id": "evt-bucket-001",
        "event_type": "bucket.tags_missing",
        "resource_id": "bucket-01",
    }
    evento.update(cambios)
    return evento


def test_bucket_sin_tags_restaura_las_esperadas() -> None:
    """Restaura las etiquetas cuando el bucket no tiene ninguna."""
    cliente = S3Falso()

    resultado = repair(_evento(), _inventario(), s3_client=cliente)

    assert resultado == RepairResult("bucket-01", "put-tags", True, "tags-ok")
    assert cliente.escrituras == [ESPERADAS]
    assert cliente.lecturas == 2


def test_bucket_con_tags_ajenas_las_preserva() -> None:
    """Conserva las etiquetas ajenas al restaurar las obligatorias."""
    cliente = S3Falso({"Equipo": "otro"})

    repair(_evento(), _inventario(), s3_client=cliente)

    assert cliente.escrituras[0]["Equipo"] == "otro"
    assert cliente.escrituras[0]["Owner"] == "grupo-01"


def test_tag_en_conflicto_gana_la_esperada() -> None:
    """Si una clave ajenas colisiona, gana el valor del inventario."""
    cliente = S3Falso({"Owner": "malo", "Equipo": "otro"})

    repair(_evento(), _inventario(), s3_client=cliente)

    assert cliente.escrituras[0]["Owner"] == "grupo-01"
    assert cliente.escrituras[0]["Equipo"] == "otro"


def test_bucket_ya_completo_no_escribe() -> None:
    """No escribe cuando las etiquetas esperadas ya están presentes."""
    cliente = S3Falso(dict(ESPERADAS))

    resultado = repair(_evento(), _inventario(), s3_client=cliente)

    assert resultado.action == "noop"
    assert resultado.changed is False
    assert cliente.escrituras == []
    assert cliente.lecturas == 1


def test_bucket_con_esperadas_y_ajenas_no_escribe() -> None:
    """No escribe si las esperadas ya están, aunque haya etiquetas ajenas."""
    cliente = S3Falso({**ESPERADAS, "Equipo": "otro"})

    resultado = repair(_evento(), _inventario(), s3_client=cliente)

    assert resultado.changed is False
    assert cliente.escrituras == []


def test_error_de_lectura_es_transitorio() -> None:
    """Trata un fallo al leer etiquetas como recuperable."""
    cliente = S3Falso(error_lectura=_error("Throttling", "GetBucketTagging"))

    with pytest.raises(RepairTransientError):
        repair(_evento(), _inventario(), s3_client=cliente)


def test_error_de_escritura_es_transitorio() -> None:
    """Trata un fallo al escribir etiquetas como recuperable."""
    cliente = S3Falso(error_escritura=_error("SlowDown", "PutBucketTagging"))

    with pytest.raises(RepairTransientError):
        repair(_evento(), _inventario(), s3_client=cliente)


def test_verificacion_fallida_es_transitoria() -> None:
    """Falla como transitorio si la relectura no confirma el cambio."""
    cliente = S3Falso(verificar_con={"Owner": "otro"})

    with pytest.raises(RepairTransientError):
        repair(_evento(), _inventario(), s3_client=cliente)


def test_recurso_sin_expected_tags_es_permanente() -> None:
    """Rechaza un bucket sin etiquetas esperadas en el inventario."""
    inventario = _inventario()
    del inventario["bucket-01"]["expected_tags"]

    with pytest.raises(RepairPermanentError):
        repair(_evento(), inventario, s3_client=S3Falso())


def test_expected_tags_vacias_es_permanente() -> None:
    """Rechaza un conjunto vacío de etiquetas esperadas."""
    with pytest.raises(RepairPermanentError):
        repair(_evento(), _inventario(expected_tags={}), s3_client=S3Falso())


def test_expected_tags_no_str_es_permanente() -> None:
    """Rechaza etiquetas esperadas con valores que no son texto."""
    inventario = _inventario(expected_tags={"Owner": 123})

    with pytest.raises(RepairPermanentError):
        repair(_evento(), inventario, s3_client=S3Falso())


def test_recurso_sin_id_es_permanente() -> None:
    """Rechaza un bucket sin nombre en el inventario."""
    inventario = _inventario()
    del inventario["bucket-01"]["id"]

    with pytest.raises(RepairPermanentError):
        repair(_evento(), inventario, s3_client=S3Falso())


def test_usa_el_bucket_del_inventario_y_no_del_evento() -> None:
    """Ignora cualquier bucket del evento y usa el del inventario."""
    cliente = S3Falso()
    evento = _evento(Bucket="bucket-malicioso", id="bucket-malicioso")

    repair(evento, _inventario(), s3_client=cliente)

    assert cliente.buckets == ["bucket-123"]


def test_tagset_invalido_es_permanente() -> None:
    """Rechaza una respuesta de etiquetas con formato inválido."""
    cliente = S3Falso(respuesta_lectura={"TagSet": "no-lista"})

    with pytest.raises(RepairPermanentError):
        repair(_evento(), _inventario(), s3_client=cliente)


def test_credenciales_rechazadas_al_leer_son_permanentes() -> None:
    """Un AccessDenied al leer etiquetas no se recupera reintentando."""
    cliente = S3Falso(error_lectura=_error("AccessDenied", "GetBucketTagging"))

    with pytest.raises(RepairPermanentError):
        repair(_evento(), _inventario(), s3_client=cliente)


def test_bucket_inexistente_al_leer_es_permanente() -> None:
    """Un bucket que ya no existe no se recupera reintentando."""
    cliente = S3Falso(error_lectura=_error("NoSuchBucket", "GetBucketTagging"))

    with pytest.raises(RepairPermanentError):
        repair(_evento(), _inventario(), s3_client=cliente)


def test_credenciales_rechazadas_al_escribir_son_permanentes() -> None:
    """Un UnauthorizedOperation al escribir etiquetas es permanente."""
    cliente = S3Falso(
        tags={"Owner": "otro-grupo"},
        error_escritura=_error("UnauthorizedOperation", "PutBucketTagging"),
    )

    with pytest.raises(RepairPermanentError):
        repair(_evento(), _inventario(), s3_client=cliente)
