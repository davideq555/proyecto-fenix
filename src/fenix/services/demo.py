"""Preparación idempotente de los recursos demo de Fénix."""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

from botocore.exceptions import ClientError

from fenix.config.settings import get_aws_region, get_ec2_client, get_s3_client
from fenix.services.cloud_support import (
    aws_tags,
    create_bucket,
    create_group,
    create_instance,
    mock_inputs,
)
from fenix.utils.state import load_inventory, save_resource


def _slug(value: str) -> str:
    """Convierte el identificador del grupo en un fragmento DNS seguro."""
    result = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    if not result:
        raise ValueError("GROUP_ID no produce un nombre válido")
    return result[:35].strip("-")


def _tags_to_dict(tags: list[dict[str, str]] | None) -> dict[str, str]:
    """Convierte tags de AWS a un diccionario."""
    return {
        item["Key"]: item["Value"]
        for item in (tags or [])
        if "Key" in item and "Value" in item
    }


def _check_tags(
    actual: dict[str, str],
    expected: dict[str, str],
    *,
    allow_missing: bool = False,
) -> None:
    """Rechaza recursos con etiquetas de propiedad incompatibles."""
    for key, value in actual.items():
        if key in expected and expected[key] != value:
            raise RuntimeError(
                f"Conflicto de propiedad: el tag {key} no coincide"
            )

    missing = [key for key in expected if key not in actual]
    if missing and not allow_missing:
        raise RuntimeError(
            "No se puede verificar la propiedad del recurso: "
            + ", ".join(missing)
        )


def _is_pending(record: Any, resource_id: str) -> bool:
    return (
        isinstance(record, dict)
        and record.get("status") == "pending"
        and record.get("id") == resource_id
    )


def _bucket_exists(client: Any, bucket_name: str) -> bool:
    response = client.list_buckets()
    return any(
        item.get("Name") == bucket_name
        for item in response.get("Buckets", [])
    )


def _ensure_bucket(
    client: Any,
    bucket_name: str,
    tags: dict[str, str],
) -> None:
    inventory = load_inventory()
    previous = inventory["resources"].get("bucket-01")
    pending = _is_pending(previous, bucket_name)

    exists = _bucket_exists(client, bucket_name)

    if exists:
        try:
            response = client.get_bucket_tagging(Bucket=bucket_name)
            actual = _tags_to_dict(response.get("TagSet", []))
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code not in {"NoSuchTagSet", "NoSuchTagSetError"}:
                raise
            actual = {}

        _check_tags(actual, tags, allow_missing=pending)
        merged_tags = {**actual, **tags}

    else:
        save_resource(
            "bucket-01",
            {
                "type": "s3",
                "id": bucket_name,
                "expected_tags": tags,
                "status": "pending",
            },
        )
        create_bucket(
            bucket_name,
            s3_client=client,
            region=get_aws_region(),
        )
        merged_tags = tags.copy()

    client.put_bucket_tagging(
        Bucket=bucket_name,
        Tagging={
            "TagSet": [
                {"Key": key, "Value": value}
                for key, value in merged_tags.items()
            ]
        },
    )

    verified = client.get_bucket_tagging(Bucket=bucket_name)
    actual = _tags_to_dict(verified.get("TagSet", []))
    _check_tags(actual, tags)

    save_resource(
        "bucket-01",
        {
            "type": "s3",
            "id": bucket_name,
            "expected_tags": tags,
        },
    )

def _find_security_group(
    client: Any,
    group_name: str,
    vpc_id: str,
) -> dict[str, Any] | None:
    response = client.describe_security_groups(
        Filters=[
            {"Name": "group-name", "Values": [group_name]},
            {"Name": "vpc-id", "Values": [vpc_id]},
        ]
    )
    groups = response.get("SecurityGroups", [])

    if len(groups) > 1:
        raise RuntimeError(
            "Se encontraron varios grupos de seguridad con el mismo nombre"
        )

    return groups[0] if groups else None


def _ensure_security_group(
    client: Any,
    group_name: str,
    vpc_id: str,
    tags: dict[str, str],
) -> str:
    inventory = load_inventory()
    previous = inventory["resources"].get("node-01")
    if not isinstance(previous, dict):
        previous = {}

    pending = (
        previous.get("status") == "pending"
        and previous.get("security_group_name") == group_name
    )

    group = _find_security_group(client, group_name, vpc_id)

    if group is None:
        if previous.get("security_group_id"):
            raise RuntimeError(
                "El grupo guardado en el inventario ya no existe"
            )

        save_resource(
            "node-01",
            {
                **previous,
                "type": "ec2",
                "security_group_id": None,
                "security_group_name": group_name,
                "expected_tags": tags,
                "status": "pending",
            },
        )

        group_id = create_group(
            group_name,
            vpc_id,
            tags,
            ec2_client=client,
        )

        save_resource(
            "node-01",
            {
                **previous,
                "type": "ec2",
                "security_group_id": group_id,
                "security_group_name": group_name,
                "expected_tags": tags,
                "status": "pending",
            },
        )

        group = _find_security_group(client, group_name, vpc_id)
        if group is None or group.get("GroupId") != group_id:
            raise RuntimeError(
                "No se pudo verificar el grupo de seguridad creado"
            )

    group_id = group["GroupId"]

    old_group_id = previous.get("security_group_id")
    if old_group_id and old_group_id != group_id:
        raise RuntimeError(
            "El grupo de seguridad no coincide con el inventario"
        )

    actual = _tags_to_dict(group.get("Tags", []))
    _check_tags(actual, tags, allow_missing=pending)

    if pending and any(key not in actual for key in tags):
        client.create_tags(
            Resources=[group_id],
            Tags=[
                {"Key": key, "Value": value}
                for key, value in tags.items()
            ],
        )

    verified = client.describe_security_groups(
        GroupIds=[group_id]
    )
    groups = verified.get("SecurityGroups", [])

    if len(groups) != 1:
        raise RuntimeError(
            "No se pudo verificar el grupo de seguridad"
        )

    group = groups[0]

    if group.get("IpPermissions"):
        raise RuntimeError(
            "El grupo de seguridad tiene reglas de entrada inesperadas"
        )

    _check_tags(_tags_to_dict(group.get("Tags", [])), tags)

    resource = dict(previous)
    resource.update(
        {
            "type": "ec2",
            "security_group_id": group_id,
            "security_group_name": group_name,
            "expected_tags": tags,
        }
    )

    if resource.get("id"):
        resource.pop("status", None)
    else:
        resource["id"] = None
        resource["status"] = "pending"

    save_resource("node-01", resource)
    return group_id

def _find_instance(
    client: Any,
    client_token: str,
    instance_id: str | None = None,
) -> dict[str, Any] | None:
    if instance_id:
        response = client.describe_instances(
            InstanceIds=[instance_id]
        )
    else:
        response = client.describe_instances()

    instances = [
        instance
        for reservation in response.get("Reservations", [])
        for instance in reservation.get("Instances", [])
        if instance.get("State", {}).get("Name")
        not in {"terminated", "shutting-down"}
    ]

    if instance_id:
        if len(instances) > 1:
            raise RuntimeError(
                "Se encontraron varias respuestas para el InstanceId guardado"
            )
        return instances[0] if instances else None

    matches = [
        instance
        for instance in instances
        if instance.get("ClientToken") == client_token
    ]

    if len(matches) > 1:
        raise RuntimeError(
            "Se encontraron varias instancias para el mismo ClientToken"
        )

    return matches[0] if matches else None

def _ensure_instance(
    client: Any,
    image_id: str,
    security_group_id: str,
    group_id: str,
    tags: dict[str, str],
) -> dict[str, str]:
    client_token = hashlib.sha256(
        f"fenix:{group_id}:node-01".encode("utf-8")
    ).hexdigest()

    inventory = load_inventory()
    previous = inventory["resources"].get("node-01")

    if isinstance(previous, dict):
        old_token = previous.get("client_token")
        if old_token and old_token != client_token:
            raise RuntimeError(
                "El ClientToken guardado no coincide con GROUP_ID"
            )

        old_group_id = previous.get("security_group_id")
        if old_group_id and old_group_id != security_group_id:
            raise RuntimeError(
                "El grupo de seguridad guardado no coincide con el esperado"
            )

    instance = _find_instance(
        client,
        client_token,
        previous.get("id") if isinstance(previous, dict) else None,
    )

    if instance is None:
        save_resource(
            "node-01",
            {
                **(previous if isinstance(previous, dict) else {}),
                "type": "ec2",
                "id": None,
                "security_group_id": security_group_id,
                "client_token": client_token,
                "expected_tags": tags,
                "status": "pending",
            },
        )

        created = create_instance(
            image_id=image_id,
            security_group_id=security_group_id,
            client_token=client_token,
            tags=tags,
            ec2_client=client,
        )
        instance_id = created["InstanceId"]
        state = created["State"]

    else:
        instance_id = instance["InstanceId"]
        state = instance.get("State", {}).get("Name", "unknown")
        actual = _tags_to_dict(instance.get("Tags", []))

        pending = (
            isinstance(previous, dict)
            and previous.get("status") == "pending"
            and previous.get("client_token") == client_token
        )
        _check_tags(actual, tags, allow_missing=pending)

        if pending and any(key not in actual for key in tags):
            client.create_tags(
                Resources=[instance_id],
                Tags=[
                    {"Key": key, "Value": value}
                    for key, value in tags.items()
                ],
            )

    # Verificación final de la instancia y de su grupo de seguridad.
    response = client.describe_instances(
        InstanceIds=[instance_id]
    )
    found = [
        item
        for reservation in response.get("Reservations", [])
        for item in reservation.get("Instances", [])
    ]

    if len(found) != 1:
        raise RuntimeError(
            "No se pudo verificar la instancia EC2 creada"
        )

    verified_instance = found[0]

    associated_groups = verified_instance.get("SecurityGroups", [])
    if not any(
        item.get("GroupId") == security_group_id
        for item in associated_groups
    ):
        raise RuntimeError(
            "La instancia no está asociada al grupo de seguridad esperado"
        )

    actual = _tags_to_dict(verified_instance.get("Tags", []))
    _check_tags(actual, tags)

    state = verified_instance.get("State", {}).get("Name", state)

    save_resource(
        "node-01",
        {
            "type": "ec2",
            "id": instance_id,
            "security_group_id": security_group_id,
            "client_token": client_token,
            "expected_tags": tags,
        },
    )

    return {"InstanceId": instance_id, "State": state}

def setup_demo(
    ec2_client: Any | None = None,
    s3_client: Any | None = None,
) -> dict[str, Any]:
    """Crea o recupera los recursos demo de Fénix sin duplicarlos.

    No inicializa las colas SQS: esa responsabilidad corresponde
    a ensure_queues() en el módulo sqs.
    """
    group_id = os.getenv("GROUP_ID", "").strip()
    tags = aws_tags(group_id)

    ec2 = ec2_client or get_ec2_client()
    s3 = s3_client or get_s3_client()

    inputs = mock_inputs(ec2_client=ec2)
    slug = _slug(group_id)

    bucket_name = f"fenix-{slug}-bucket-01"
    group_name = f"fenix-{slug}-node-01-sg"

    _ensure_bucket(s3, bucket_name, tags)

    security_group_id = _ensure_security_group(
        ec2,
        group_name,
        inputs["vpc_id"],
        tags,
    )

    instance = _ensure_instance(
        ec2,
        inputs["image_id"],
        security_group_id,
        group_id,
        tags,
    )

    return {
        "bucket-01": bucket_name,
        "node-01": instance["InstanceId"],
        "security_group_id": security_group_id,
        "instance_state": instance["State"],
    }
