"""Gestión segura del inventario local de recursos."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]


def inventory_path() -> Path:
    """Devuelve la ruta del inventario local."""
    configured_path = os.getenv("FENIX_INVENTORY_PATH")

    if configured_path:
        return Path(configured_path).expanduser()

    return PROJECT_ROOT / "data" / "inventory.json"


def load_inventory() -> dict[str, Any]:
    """Carga el inventario o devuelve uno vacío si todavía no existe."""
    path = inventory_path()

    if not path.exists():
        return {"resources": {}}

    with path.open("r", encoding="utf-8") as file:
        inventory = json.load(file)

    if not isinstance(inventory, dict):
        raise ValueError("El inventario debe ser un objeto JSON")

    if "resources" in inventory:
        if not isinstance(inventory["resources"], dict):
            raise ValueError("La sección resources no es válida")
    else:
        # Admite inventarios antiguos en formato plano.
        inventory = {"resources": inventory}

    return inventory


def save_inventory(inventory: dict[str, Any]) -> None:
    """Guarda el inventario mediante un archivo temporal único y reemplazo atómico."""
    if not isinstance(inventory, dict):
        raise ValueError("El inventario debe ser un diccionario")

    resources = inventory.get("resources")

    if not isinstance(resources, dict):
        raise ValueError("La sección resources debe ser un diccionario")

    path = inventory_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary_path = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as file:
            temporary_path = Path(file.name)

            json.dump(inventory, file, indent=2, ensure_ascii=False)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())

        os.replace(temporary_path, path)

    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()

def save_resource(alias: str, resource: dict[str, Any]) -> dict[str, Any]:
    """Actualiza un recurso del inventario y guarda inmediatamente."""
    if not isinstance(alias, str) or not alias:
        raise ValueError("El alias del recurso no es válido")

    if not isinstance(resource, dict):
        raise ValueError("El recurso debe ser un diccionario")

    inventory = load_inventory()
    inventory["resources"][alias] = resource
    save_inventory(inventory)

    return inventory
