"""Pruebas unitarias del almacenamiento del inventario."""

from __future__ import annotations

import json

import pytest

from fenix.utils import state


def test_inventory_path_usa_ruta_configurada(monkeypatch, tmp_path):
    ruta = tmp_path / "mi_inventario.json"
    monkeypatch.setenv("FENIX_INVENTORY_PATH", str(ruta))

    assert state.inventory_path() == ruta


def test_inventory_path_usa_ruta_del_proyecto(monkeypatch, tmp_path):
    monkeypatch.delenv("FENIX_INVENTORY_PATH", raising=False)
    monkeypatch.setattr(state, "PROJECT_ROOT", tmp_path)

    assert state.inventory_path() == tmp_path / "data" / "inventory.json"


def test_load_inventory_devuelve_vacio_si_no_existe(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "FENIX_INVENTORY_PATH",
        str(tmp_path / "inexistente.json"),
    )

    assert state.load_inventory() == {"resources": {}}


def test_load_inventory_lee_formato_actual(monkeypatch, tmp_path):
    ruta = tmp_path / "inventory.json"
    contenido = {
        "resources": {
            "servidor-01": {"type": "ec2", "state": "running"}
        }
    }
    ruta.write_text(json.dumps(contenido), encoding="utf-8")
    monkeypatch.setenv("FENIX_INVENTORY_PATH", str(ruta))

    assert state.load_inventory() == contenido


def test_load_inventory_admite_formato_antiguo(monkeypatch, tmp_path):
    ruta = tmp_path / "inventory.json"
    contenido = {
        "servidor-01": {"type": "ec2"},
        "bucket-01": {"type": "s3"},
    }
    ruta.write_text(json.dumps(contenido), encoding="utf-8")
    monkeypatch.setenv("FENIX_INVENTORY_PATH", str(ruta))

    assert state.load_inventory() == {"resources": contenido}


@pytest.mark.parametrize("contenido", ["[]", '"texto"', "123", "null"])
def test_load_inventory_rechaza_json_que_no_es_objeto(
    monkeypatch, tmp_path, contenido
):
    ruta = tmp_path / "inventory.json"
    ruta.write_text(contenido, encoding="utf-8")
    monkeypatch.setenv("FENIX_INVENTORY_PATH", str(ruta))

    with pytest.raises(ValueError, match="objeto JSON"):
        state.load_inventory()


def test_load_inventory_rechaza_resources_invalidos(monkeypatch, tmp_path):
    ruta = tmp_path / "inventory.json"
    ruta.write_text('{"resources": []}', encoding="utf-8")
    monkeypatch.setenv("FENIX_INVENTORY_PATH", str(ruta))

    with pytest.raises(ValueError, match="resources"):
        state.load_inventory()


def test_load_inventory_propaga_error_json(monkeypatch, tmp_path):
    ruta = tmp_path / "inventory.json"
    ruta.write_text("{json incorrecto", encoding="utf-8")
    monkeypatch.setenv("FENIX_INVENTORY_PATH", str(ruta))

    with pytest.raises(json.JSONDecodeError):
        state.load_inventory()


def test_save_inventory_guarda_y_recupera_datos(monkeypatch, tmp_path):
    ruta = tmp_path / "subcarpeta" / "inventory.json"
    monkeypatch.setenv("FENIX_INVENTORY_PATH", str(ruta))

    inventario = {
        "resources": {
            "servidor-01": {"type": "ec2", "nombre": "Máquina de prueba"}
        }
    }

    state.save_inventory(inventario)

    assert ruta.exists()
    assert state.load_inventory() == inventario
    assert "Máquina de prueba" in ruta.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "inventario",
    [
        None,
        [],
        "texto",
        123,
        {},
        {"resources": []},
        {"resources": None},
    ],
)
def test_save_inventory_rechaza_datos_invalidos(inventario):
    with pytest.raises(ValueError):
        state.save_inventory(inventario)


def test_save_inventory_no_deja_archivos_temporales(monkeypatch, tmp_path):
    ruta = tmp_path / "inventory.json"
    monkeypatch.setenv("FENIX_INVENTORY_PATH", str(ruta))

    state.save_inventory({"resources": {"prueba": {"type": "ec2"}}})

    temporales = list(tmp_path.glob(".inventory.json.*.tmp"))

    assert temporales == []


def test_save_resource_agrega_recurso(monkeypatch, tmp_path):
    ruta = tmp_path / "inventory.json"
    monkeypatch.setenv("FENIX_INVENTORY_PATH", str(ruta))

    recurso = {"type": "ec2", "state": "running"}

    resultado = state.save_resource("servidor-01", recurso)

    assert resultado == {"resources": {"servidor-01": recurso}}
    assert state.load_inventory() == resultado


def test_save_resource_conserva_recursos_existentes(monkeypatch, tmp_path):
    ruta = tmp_path / "inventory.json"
    monkeypatch.setenv("FENIX_INVENTORY_PATH", str(ruta))

    state.save_inventory({
        "resources": {
            "servidor-01": {"type": "ec2"}
        }
    })

    resultado = state.save_resource(
        "bucket-01",
        {"type": "s3"},
    )

    assert set(resultado["resources"]) == {"servidor-01", "bucket-01"}


@pytest.mark.parametrize("alias", ["", None, 123])
def test_save_resource_rechaza_alias_invalido(alias):
    with pytest.raises(ValueError):
        state.save_resource(alias, {"type": "ec2"})


@pytest.mark.parametrize("resource", [None, [], "texto", 123])
def test_save_resource_rechaza_recurso_invalido(resource):
    with pytest.raises(ValueError):
        state.save_resource("servidor-01", resource)
