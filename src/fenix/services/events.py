"""
Servicio de procesamiento y validación de contratos de eventos.
Rol 1 (I1) - Proyecto Fénix.
"""

from fenix.utils.errors import InventoryError, ValidationError
from fenix.utils.validation import (
    validate_identifier,
    validate_ttl,
    validate_utc_timestamp,
)

# Inventario permitido de recursos según la arquitectura del proyecto
ALLOWED_INVENTORIES = {"bucket", "node"}


def validate_event_contract(event_data: dict) -> dict:
    """
    Valida estrictamente el contrato JSON de un evento entrante.
    Verifica tipos, campos obligatorios, formato de IDs, timestamp y pertenencia al inventario.
    """
    if not isinstance(event_data, dict):
        raise ValidationError("El evento debe ser un objeto JSON válido.")

    # 1. Validación de campos obligatorios en el nivel raíz
    required_fields = ["version", "event_id", "resource_type", "resource_id", "occurred_at", "ttl_seconds"]
    for field in required_fields:
        if field not in event_data:
            raise ValidationError(f"Falta el campo obligatorio en el contrato: '{field}'.")

    # 2. Validación de la versión del contrato
    version = event_data["version"]
    if not isinstance(version, str) or version != "1.0":
        raise ValidationError(f"Versión de contrato inválida: '{version}'. Debe ser '1.0'.")

    # 3. Validación de identificadores
    validate_identifier(event_data["event_id"], "event_id")
    validate_identifier(event_data["resource_id"], "resource_id")

    # 4. Validación del tipo de recurso contra el inventario
    resource_type = event_data["resource_type"]
    if not isinstance(resource_type, str):
        raise ValidationError("El campo 'resource_type' debe ser una cadena de texto.")
    if resource_type not in ALLOWED_INVENTORIES:
        raise InventoryError(f"El recurso '{resource_type}' no se encuentra registrado en el inventario del grupo.")

    # 5. Validación del timestamp UTC
    validate_utc_timestamp(event_data["occurred_at"])

    # 6. Validación del TTL
    validate_ttl(event_data["ttl_seconds"])

    return event_data