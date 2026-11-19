"""Contrato de eventos de autorreparación; integrante 1."""

from fenix.utils.errors import ValidationError
from fenix.utils.validation import identifier, utc_text

# Estructura temporal en memoria para rastrear event_ids procesados y evitar conflictos
_EVENT_REGISTRY = {}


def validate(event: dict, inventory: dict) -> dict:
    """Valida esquema cerrado y resuelve exclusivamente aliases autorizados.

    Args:
        event: JSON objeto con schema_version, event_id, event_type, resource_id,
            occurred_at y simulate_failures.
        inventory: Mapa alias -> recurso, preparado por setup-demo y revisado por el grupo.
    Returns:
        Copia validada del evento; no modifica inventory.
    Raises:
        ValidationError: Tipo, campo, timestamp o recurso no permitido.
    """
    if not isinstance(event, dict):
        raise ValidationError("El evento debe ser un objeto JSON (diccionario).")

    # Definir campos exactos obligatorios del contrato cerrado
    required_fields = {
        "schema_version",
        "event_id",
        "event_type",
        "resource_id",
        "occurred_at",
        "simulate_failures",
    }

    if set(event.keys()) != required_fields:
        raise ValidationError(
            f"Las claves del evento no coinciden con el contrato esperado. Encontradas: {list(event.keys())}"
        )

    # 1. Validar schema_version (Entero estricto igual a 1, rechazando booleanos)
    schema_version = event.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        raise ValidationError(
            "El campo 'schema_version' debe ser estrictamente el entero 1."
        )

    # 2. Validar event_id e resource_id usando la función auxiliar identifier
    event_id = identifier(event.get("event_id"))
    resource_id = identifier(event.get("resource_id"))

    # 3. Validar event_type permitido
    event_type = event.get("event_type")
    allowed_event_types = {"instance.stopped", "bucket.tags_missing"}
    if event_type not in allowed_event_types:
        raise ValidationError(f"Tipo de evento desconocido: '{event_type}'.")

    # 4. Validar occurred_at usando utc_text
    utc_text(event.get("occurred_at"))

    # 5. Validar simulate_failures (Entero de 0 a 5, rechazando booleanos)
    simulate_failures = event.get("simulate_failures")
    if type(simulate_failures) is not int or not (0 <= simulate_failures <= 5):
        raise ValidationError(
            "El campo 'simulate_failures' debe ser un número entero entre 0 y 5."
        )

    # 6. Validar inventario y correspondencia de tipos de recursos
    if not isinstance(inventory, dict) or resource_id not in inventory:
        raise ValidationError(
            f"El recurso '{resource_id}' no se encuentra autorizado en el inventario."
        )

    resource_info = inventory[resource_id]
    resource_type = resource_info.get("type")

    # Comprobar correspondencia lógica entre el tipo de evento y el tipo de recurso del inventario
    if event_type == "instance.stopped" and resource_type != "ec2":
        raise ValidationError(
            f"El evento '{event_type}' no corresponde al recurso de tipo '{resource_type}'."
        )
    if event_type == "bucket.tags_missing" and resource_type != "s3":
        raise ValidationError(
            f"El evento '{event_type}' no corresponde al recurso de tipo '{resource_type}'."
        )

    # 7. Control de conflictos: verificar si el event_id ya fue registrado con contenido distinto
    if event_id in _EVENT_REGISTRY:
        existing_event = _EVENT_REGISTRY[event_id]
        if existing_event != event:
            raise ValidationError(
                f"Conflicto detectado: el event_id '{event_id}' ya fue registrado con un contenido diferente."
            )
    else:
        # Guardar copia del evento asociado a este ID
        _EVENT_REGISTRY[event_id] = dict(event)

    # Devolver una copia validada del evento sin alterar el inventario
    return dict(event)
