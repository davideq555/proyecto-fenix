"""Inyección controlada de fallas sobre los recursos demo de Fénix."""

from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from fenix.config.settings import get_ec2_client, get_s3_client
from fenix.utils.state import load_inventory


def _tags_to_dict(tags: list[dict[str, str]] | None) -> dict[str, str]:
    """Convierte tags de AWS a un diccionario."""
    return {
        item["Key"]: item["Value"]
        for item in (tags or [])
        if "Key" in item and "Value" in item
    }


def inject_fault(
    alias: str,
    ec2_client: Any | None = None,
    s3_client: Any | None = None,
) -> dict[str, Any]:
    """Introduce una falla controlada en un recurso ya inventariado.

    Args:
        alias: ``node-01`` para detener la instancia o ``bucket-01`` para
            quitar sus etiquetas obligatorias.
        ec2_client: Cliente EC2 alternativo (pruebas); por defecto, LocalStack.
        s3_client: Cliente S3 alternativo (pruebas); por defecto, LocalStack.

    Returns:
        Resumen con el recurso afectado, la acción aplicada y el estado final.

    Raises:
        ValueError: El alias no existe en el inventario o no es soportado.

    Side Effects:
        Detiene la instancia EC2 o reescribe las tags del bucket. Las tags
        ajenas del bucket se conservan.
    """
    inventory = load_inventory()
    record = inventory["resources"].get(alias)

    if not isinstance(record, dict) or not record.get("id"):
        raise ValueError(f"El recurso {alias} no está en el inventario")

    expected = {
        str(key): str(value)
        for key, value in (record.get("expected_tags") or {}).items()
    }

    if alias == "node-01":
        client = ec2_client or get_ec2_client()
        instance_id = record["id"]

        client.stop_instances(InstanceIds=[instance_id])
        client.get_waiter("instance_stopped").wait(
            InstanceIds=[instance_id],
            WaiterConfig={"Delay": 2, "MaxAttempts": 30},
        )

        described = client.describe_instances(InstanceIds=[instance_id])
        state = described["Reservations"][0]["Instances"][0]["State"]["Name"]

        return {
            "resource_id": alias,
            "id": instance_id,
            "action": "stop",
            "status": state,
        }

    if alias == "bucket-01":
        if not expected:
            raise ValueError(
                "bucket-01 no tiene etiquetas esperadas en el inventario"
            )

        client = s3_client or get_s3_client()
        bucket_name = record["id"]

        try:
            response = client.get_bucket_tagging(Bucket=bucket_name)
            current = _tags_to_dict(response.get("TagSet", []))
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code not in {"NoSuchTagSet", "NoSuchTagSetError"}:
                raise
            current = {}

        remaining = {
            key: value for key, value in current.items() if key not in expected
        }

        if remaining:
            client.put_bucket_tagging(
                Bucket=bucket_name,
                Tagging={
                    "TagSet": [
                        {"Key": key, "Value": value}
                        for key, value in remaining.items()
                    ]
                },
            )
        elif current:
            client.delete_bucket_tagging(Bucket=bucket_name)

        return {
            "resource_id": alias,
            "id": bucket_name,
            "action": "remove_tags",
            "removed": sorted(key for key in current if key in expected),
            "preserved": sorted(remaining),
        }

    raise ValueError(f"Alias de falla no soportado: {alias}")
