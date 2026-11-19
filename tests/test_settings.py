"""Pruebas unitarias de la configuración AWS."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from fenix.config import settings


def test_get_aws_endpoint_usa_valor_predeterminado(monkeypatch):
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)

    assert settings.get_aws_endpoint() == "http://127.0.0.1:4566"


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:4566",
        "http://127.0.0.1:4566",
        "http://localhost:4566/",
    ],
)
def test_get_aws_endpoint_acepta_localstack(monkeypatch, endpoint):
    monkeypatch.setenv("AWS_ENDPOINT_URL", endpoint)

    assert settings.get_aws_endpoint() == endpoint.rstrip("/")


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://localhost:4566",
        "http://amazonaws.com:4566",
        "http://192.168.1.10:4566",
        "http://localhost:4567",
        "http://localhost:4566/ruta",
        "http://usuario:clave@localhost:4566",
        "http://localhost:4566?token=123",
        "http://localhost:4566#fragmento",
        "",
    ],
)
def test_get_aws_endpoint_rechaza_endpoints_no_seguros(
    monkeypatch, endpoint
):
    monkeypatch.setenv("AWS_ENDPOINT_URL", endpoint)

    with pytest.raises(ValueError):
        settings.get_aws_endpoint()


def test_get_aws_region_usa_valor_predeterminado(monkeypatch):
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)

    assert settings.get_aws_region() == "us-east-1"


def test_get_aws_region_usa_region_configurada(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "sa-east-1")

    assert settings.get_aws_region() == "sa-east-1"


def test_create_client_configura_boto3(monkeypatch):
    boto3_falso = MagicMock()
    monkeypatch.setattr(settings, "boto3", boto3_falso)
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "sa-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "clave-prueba")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secreto-prueba")

    cliente = settings._create_client("sqs")

    assert cliente is boto3_falso.client.return_value

    argumentos = boto3_falso.client.call_args.kwargs
    assert boto3_falso.client.call_args.args[0] == "sqs"
    assert argumentos["endpoint_url"] == "http://localhost:4566"
    assert argumentos["region_name"] == "sa-east-1"
    assert argumentos["aws_access_key_id"] == "clave-prueba"
    assert argumentos["aws_secret_access_key"] == "secreto-prueba"

    configuracion = argumentos["config"]
    assert configuracion.connect_timeout == 3
    assert configuracion.read_timeout == 5
    assert configuracion.retries["max_attempts"] == 2


@pytest.mark.parametrize(
    ("funcion", "servicio"),
    [
        (settings.get_sqs_client, "sqs"),
        (settings.get_ec2_client, "ec2"),
        (settings.get_s3_client, "s3"),
    ],
)
def test_get_client_usa_servicio_correcto(
    monkeypatch, funcion, servicio
):
    settings.get_sqs_client.cache_clear()
    settings.get_ec2_client.cache_clear()
    settings.get_s3_client.cache_clear()

    boto3_falso = MagicMock()
    monkeypatch.setattr(settings, "boto3", boto3_falso)
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")

    try:
        cliente = funcion()

        assert cliente is boto3_falso.client.return_value
        assert boto3_falso.client.call_args.args[0] == servicio
    finally:
        settings.get_sqs_client.cache_clear()
        settings.get_ec2_client.cache_clear()
        settings.get_s3_client.cache_clear()


def test_get_sqs_client_reutiliza_cliente_en_cache(monkeypatch):
    settings.get_sqs_client.cache_clear()
    boto3_falso = MagicMock()
    monkeypatch.setattr(settings, "boto3", boto3_falso)
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")

    try:
        primero = settings.get_sqs_client()
        segundo = settings.get_sqs_client()

        assert primero is segundo
        assert boto3_falso.client.call_count == 1
    finally:
        settings.get_sqs_client.cache_clear()


