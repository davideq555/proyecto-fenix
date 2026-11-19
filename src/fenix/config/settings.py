
"""Configuración segura de clientes AWS compatibles con LocalStack."""

from __future__ import annotations

import os
from functools import lru_cache
from urllib.parse import urlparse

import boto3
from botocore.config import Config


def get_aws_endpoint() -> str:
    """Devuelve y valida el endpoint local de LocalStack."""
    endpoint = os.getenv(
        "AWS_ENDPOINT_URL",
        "http://127.0.0.1:4566",
    ).strip()

    parsed = urlparse(endpoint)

    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1"}
        or parsed.port != 4566
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Por seguridad, Fénix solo permite LocalStack "
            "en http://localhost:4566 o http://127.0.0.1:4566"
        )

    return endpoint.rstrip("/")


def get_aws_region() -> str:
    """Devuelve la región configurada para el laboratorio."""
    return os.getenv("AWS_DEFAULT_REGION", "us-east-1").strip()


def _create_client(service_name: str):
    """Crea un cliente Boto3 limitado al endpoint local."""
    return boto3.client(
        service_name,
        endpoint_url=get_aws_endpoint(),
        region_name=get_aws_region(),
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
        config=Config(
            connect_timeout=3,
            read_timeout=5,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
    )


@lru_cache(maxsize=1)
def get_sqs_client():
    """Devuelve el cliente SQS de LocalStack."""
    return _create_client("sqs")


@lru_cache(maxsize=1)
def get_ec2_client():
    """Devuelve el cliente EC2 de LocalStack."""
    return _create_client("ec2")


@lru_cache(maxsize=1)
def get_s3_client():
    """Devuelve el cliente S3 de LocalStack."""
    return _create_client("s3")
