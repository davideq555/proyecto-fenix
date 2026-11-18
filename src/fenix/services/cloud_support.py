"""Recetas Boto3 para los recursos EC2 y S3 del laboratorio Fénix."""

from __future__ import annotations

import os
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from fenix.config.settings import (
    get_aws_region,
    get_ec2_client,
    get_s3_client,
)


def aws_tags(group_id: str | None = None) -> dict[str, str]:
    """Construye las etiquetas obligatorias de propiedad.

    Args:
        group_id: Identificador del grupo. Si no se indica, usa GROUP_ID.

    Returns:
        Diccionario con las cuatro etiquetas obligatorias.

    Raises:
        ValueError: Si el identificador del grupo está vacío o es inválido.

    Side Effects:
        Ninguno. No realiza llamadas de red.
    """
    group = group_id if group_id is not None else os.getenv("GROUP_ID", "")
    group = group.strip()

    if not group:
        raise ValueError("GROUP_ID no está configurado")

    return {
        "ManagedBy": "ppa2",
        "Project": "fenix",
        "GroupId": group,
        "Owner": group,
    }


def mock_inputs(ec2_client: Any | None = None) -> dict[str, str]:
    """Descubre una VPC disponible y una AMI mock para EC2.

    Args:
        ec2_client: Cliente EC2 opcional. Si falta, usa el cliente local.

    Returns:
        Diccionario con vpc_id e image_id.

    Raises:
        RuntimeError: Si no se encuentra una VPC o una AMI disponible.
        ClientError: Si falla una consulta EC2.
        BotoCoreError: Si ocurre un error de comunicación.

    Side Effects:
        Solo consulta EC2. No crea ni modifica recursos.
    """
    client = ec2_client or get_ec2_client()

    vpc_response = client.describe_vpcs(
        Filters=[{"Name": "state", "Values": ["available"]}]
    )

    vpcs = vpc_response.get("Vpcs", [])
    default_vpcs = [
        vpc for vpc in vpcs
        if vpc.get("IsDefault") is True
    ]
    candidates = default_vpcs or vpcs

    if not candidates:
        raise RuntimeError(
            "No se encontró una VPC disponible en LocalStack"
        )

    vpc_id = candidates[0].get("VpcId")
    if not vpc_id:
        raise RuntimeError("La VPC descubierta no contiene VpcId")

    image_response = client.describe_images(
        Filters=[
            {"Name": "state", "Values": ["available"]},
            {"Name": "architecture", "Values": ["x86_64"]},
            {"Name": "root-device-type", "Values": ["ebs"]},
        ]
    )

    images = image_response.get("Images", [])
    if not images:
        raise RuntimeError(
            "No se encontró una AMI mock disponible para EC2"
        )

    # Se prefiere la imagen Amazon Linux 2 ECS observada en el laboratorio.
    images.sort(
        key=lambda image: (
            "amzn2-ami-ecs-hvm" not in image.get("Name", "").lower(),
            image.get("ImageId", ""),
        )
    )

    image_id = images[0].get("ImageId")
    if not image_id:
        raise RuntimeError("La AMI descubierta no contiene ImageId")

    return {
        "vpc_id": vpc_id,
        "image_id": image_id,
    }


def create_bucket(
    bucket_name: str,
    s3_client: Any | None = None,
    region: str | None = None,
) -> str:
    """Crea un bucket S3 sin aplicar etiquetas.

    Args:
        bucket_name: Nombre físico del bucket.
        s3_client: Cliente S3 opcional.
        region: Región del bucket. Si falta, usa AWS_DEFAULT_REGION.

    Returns:
        Nombre del bucket creado.

    Raises:
        ValueError: Si el nombre está vacío.
        ClientError: Si falla la creación del bucket.
        BotoCoreError: Si ocurre un error de comunicación.

    Side Effects:
        Crea un bucket. El etiquetado debe realizarse en otra llamada.
    """
    if not isinstance(bucket_name, str) or not bucket_name.strip():
        raise ValueError("El nombre del bucket no es válido")

    client = s3_client or get_s3_client()
    bucket_region = region or get_aws_region()

    params: dict[str, Any] = {"Bucket": bucket_name.strip()}

    if bucket_region != "us-east-1":
        params["CreateBucketConfiguration"] = {
            "LocationConstraint": bucket_region
        }

    client.create_bucket(**params)
    return bucket_name.strip()


def create_group(
    group_name: str,
    vpc_id: str,
    tags: dict[str, str],
    ec2_client: Any | None = None,
) -> str:
    """Crea un grupo de seguridad exclusivo sin reglas de entrada.

    Args:
        group_name: Nombre del grupo de seguridad.
        vpc_id: Identificador de la VPC.
        tags: Etiquetas de propiedad.
        ec2_client: Cliente EC2 opcional.

    Returns:
        Identificador GroupId del grupo creado.

    Raises:
        ValueError: Si faltan datos obligatorios.
        ClientError: Si falla la creación o el etiquetado.
        BotoCoreError: Si ocurre un error de comunicación.

    Side Effects:
        Crea un grupo de seguridad y aplica sus etiquetas.
        No agrega reglas de ingreso ni de salida.
    """
    if not group_name or not vpc_id:
        raise ValueError("group_name y vpc_id son obligatorios")
    if not isinstance(tags, dict) or not tags:
        raise ValueError("Se requieren etiquetas válidas")

    client = ec2_client or get_ec2_client()

    response = client.create_security_group(
        GroupName=group_name,
        Description="Grupo exclusivo del laboratorio Fénix",
        VpcId=vpc_id,
    )

    group_id = response.get("GroupId")
    if not group_id:
        raise RuntimeError(
            "EC2 no devolvió GroupId al crear el grupo de seguridad"
        )

    client.create_tags(
        Resources=[group_id],
        Tags=[
            {"Key": key, "Value": value}
            for key, value in tags.items()
        ],
    )

    return group_id


def create_instance(
    image_id: str,
    security_group_id: str,
    client_token: str,
    tags: dict[str, str],
    ec2_client: Any | None = None,
    instance_type: str = "t2.micro",
) -> dict[str, str]:
    """Solicita una instancia EC2 mock con ClientToken estable.

    Args:
        image_id: Identificador de la AMI descubierta.
        security_group_id: Grupo de seguridad exclusivo.
        client_token: Token estable para evitar duplicados.
        tags: Etiquetas de propiedad.
        ec2_client: Cliente EC2 opcional.
        instance_type: Tipo de instancia solicitado.

    Returns:
        Diccionario con InstanceId y estado inicial.

    Raises:
        ValueError: Si faltan parámetros obligatorios.
        RuntimeError: Si EC2 no devuelve una instancia.
        ClientError: Si falla la solicitud.
        BotoCoreError: Si ocurre un error de comunicación.

    Side Effects:
        Solicita una instancia EC2 y etiqueta la instancia y su volumen.
        No instala software ni configura una máquina real.
    """
    if not all((image_id, security_group_id, client_token, instance_type)):
        raise ValueError("Faltan parámetros obligatorios para EC2")
    if not isinstance(tags, dict) or not tags:
        raise ValueError("Se requieren etiquetas válidas")

    client = ec2_client or get_ec2_client()

    tag_specifications = [
        {
            "ResourceType": "instance",
            "Tags": [
                {"Key": key, "Value": value}
                for key, value in tags.items()
            ],
        },
        {
            "ResourceType": "volume",
            "Tags": [
                {"Key": key, "Value": value}
                for key, value in tags.items()
            ],
        },
    ]

    response = client.run_instances(
        ImageId=image_id,
        InstanceType=instance_type,
        MinCount=1,
        MaxCount=1,
        SecurityGroupIds=[security_group_id],
        ClientToken=client_token,
        TagSpecifications=tag_specifications,
    )

    instances = response.get("Instances", [])
    if not instances or not instances[0].get("InstanceId"):
        raise RuntimeError(
            "EC2 no devolvió InstanceId al solicitar la instancia"
        )

    instance = instances[0]
    return {
        "InstanceId": instance["InstanceId"],
        "State": instance.get("State", {}).get("Name", "unknown"),
    }
