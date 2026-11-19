import pytest
from unittest.mock import MagicMock

from fenix.services import demo


def test_slug_normaliza_nombre():
    assert demo._slug(" Grupo Dos ") == "grupo-dos"


def test_slug_reemplaza_caracteres_invalidos():
    assert demo._slug("Grupo_ABC@2026") == "grupo-abc-2026"


def test_slug_limita_longitud():
    assert len(demo._slug("a" * 50)) <= 35


def test_slug_rechaza_nombre_sin_caracteres_validos():
    with pytest.raises(ValueError):
        demo._slug(" !!! ")


def test_tags_to_dict_convierte_etiquetas():
    assert demo._tags_to_dict([
        {"Key": "Project", "Value": "fenix"},
        {"Key": "GroupId", "Value": "grupo-2"},
    ]) == {
        "Project": "fenix",
        "GroupId": "grupo-2",
    }


def test_tags_to_dict_acepta_lista_vacia_y_none():
    assert demo._tags_to_dict([]) == {}
    assert demo._tags_to_dict(None) == {}


def test_tags_to_dict_ignora_elementos_incompletos():
    assert demo._tags_to_dict([
        {"Key": "Project", "Value": "fenix"},
        {"Key": "Incomplete"},
        {"Value": "sin-clave"},
    ]) == {"Project": "fenix"}


def test_check_tags_acepta_etiquetas_correctas():
    demo._check_tags(
        {"Project": "fenix", "GroupId": "grupo-2"},
        {"Project": "fenix", "GroupId": "grupo-2"},
    )


def test_check_tags_rechaza_conflicto():
    with pytest.raises(RuntimeError, match="Conflicto de propiedad"):
        demo._check_tags(
            {"Project": "otro"},
            {"Project": "fenix"},
        )


def test_check_tags_rechaza_etiquetas_faltantes():
    with pytest.raises(RuntimeError, match="No se puede verificar"):
        demo._check_tags(
            {"Project": "fenix"},
            {"Project": "fenix", "GroupId": "grupo-2"},
        )


def test_check_tags_permite_etiquetas_faltantes_si_se_autoriza():
    demo._check_tags(
        {"Project": "fenix"},
        {"Project": "fenix", "GroupId": "grupo-2"},
        allow_missing=True,
    )


def test_is_pending_reconoce_recurso_pendiente():
    assert demo._is_pending(
        {"status": "pending", "id": "bucket-1"},
        "bucket-1",
    )


def test_is_pending_rechaza_recurso_diferente():
    assert not demo._is_pending(
        {"status": "pending", "id": "bucket-1"},
        "bucket-2",
    )


def test_is_pending_rechaza_datos_invalidos():
    assert not demo._is_pending(None, "bucket-1")
    assert not demo._is_pending({}, "bucket-1")
    assert not demo._is_pending(
        {"status": "complete", "id": "bucket-1"},
        "bucket-1",
    )


def test_bucket_exists_encuentra_bucket():
    cliente = MagicMock()
    cliente.list_buckets.return_value = {
        "Buckets": [
            {"Name": "bucket-1"},
            {"Name": "bucket-2"},
        ]
    }

    assert demo._bucket_exists(cliente, "bucket-2")


def test_bucket_exists_devuelve_false_si_no_existe():
    cliente = MagicMock()
    cliente.list_buckets.return_value = {
        "Buckets": [{"Name": "bucket-1"}]
    }

    assert not demo._bucket_exists(cliente, "bucket-3")


def test_bucket_exists_acepta_respuesta_sin_buckets():
    cliente = MagicMock()
    cliente.list_buckets.return_value = {}

    assert not demo._bucket_exists(cliente, "bucket-1")


def test_find_security_group_encuentra_grupo():
    cliente = MagicMock()
    grupo = {"GroupId": "sg-123"}
    cliente.describe_security_groups.return_value = {
        "SecurityGroups": [grupo]
    }

    resultado = demo._find_security_group(
        cliente, "grupo-prueba", "vpc-123"
    )

    assert resultado == grupo
    cliente.describe_security_groups.assert_called_once_with(
        Filters=[
            {"Name": "group-name", "Values": ["grupo-prueba"]},
            {"Name": "vpc-id", "Values": ["vpc-123"]},
        ]
    )


def test_find_security_group_devuelve_none_si_no_existe():
    cliente = MagicMock()
    cliente.describe_security_groups.return_value = {
        "SecurityGroups": []
    }

    assert demo._find_security_group(
        cliente, "grupo-prueba", "vpc-123"
    ) is None


def test_find_security_group_rechaza_duplicados():
    cliente = MagicMock()
    cliente.describe_security_groups.return_value = {
        "SecurityGroups": [
            {"GroupId": "sg-1"},
            {"GroupId": "sg-2"},
        ]
    }

    with pytest.raises(RuntimeError, match="varios grupos"):
        demo._find_security_group(
            cliente, "grupo-prueba", "vpc-123"
        )


def test_find_instance_busca_por_client_token():
    cliente = MagicMock()
    instancia = {
        "InstanceId": "i-123",
        "ClientToken": "token-1",
        "State": {"Name": "running"},
    }
    cliente.describe_instances.return_value = {
        "Reservations": [{"Instances": [instancia]}]
    }

    resultado = demo._find_instance(cliente, "token-1")

    assert resultado == instancia
    cliente.describe_instances.assert_called_once_with()


def test_find_instance_ignora_instancias_terminadas():
    cliente = MagicMock()
    cliente.describe_instances.return_value = {
        "Reservations": [{
            "Instances": [
                {
                    "InstanceId": "i-1",
                    "ClientToken": "token-1",
                    "State": {"Name": "terminated"},
                },
                {
                    "InstanceId": "i-2",
                    "ClientToken": "token-1",
                    "State": {"Name": "shutting-down"},
                },
            ]
        }]
    }

    assert demo._find_instance(cliente, "token-1") is None


def test_find_instance_devuelve_none_si_token_no_coincide():
    cliente = MagicMock()
    cliente.describe_instances.return_value = {
        "Reservations": [{
            "Instances": [{
                "InstanceId": "i-1",
                "ClientToken": "otro-token",
                "State": {"Name": "running"},
            }]
        }]
    }

    assert demo._find_instance(cliente, "token-1") is None


def test_find_instance_rechaza_varias_instancias_con_mismo_token():
    cliente = MagicMock()
    cliente.describe_instances.return_value = {
        "Reservations": [{
            "Instances": [
                {
                    "InstanceId": "i-1",
                    "ClientToken": "token-1",
                    "State": {"Name": "running"},
                },
                {
                    "InstanceId": "i-2",
                    "ClientToken": "token-1",
                    "State": {"Name": "running"},
                },
            ]
        }]
    }

    with pytest.raises(RuntimeError, match="varias instancias"):
        demo._find_instance(cliente, "token-1")


def test_find_instance_busca_por_id():
    cliente = MagicMock()
    instancia = {
        "InstanceId": "i-123",
        "State": {"Name": "running"},
    }
    cliente.describe_instances.return_value = {
        "Reservations": [{"Instances": [instancia]}]
    }

    resultado = demo._find_instance(
        cliente, "token-1", instance_id="i-123"
    )

    assert resultado == instancia
    cliente.describe_instances.assert_called_once_with(
        InstanceIds=["i-123"]
    )


def test_find_instance_por_id_devuelve_none_si_no_existe():
    cliente = MagicMock()
    cliente.describe_instances.return_value = {"Reservations": []}

    assert demo._find_instance(
        cliente, "token-1", instance_id="i-123"
    ) is None

def test_ensure_bucket_reutiliza_bucket_existente(monkeypatch):
    tags = {"Project": "fenix", "GroupId": "grupo-2"}
    cliente = MagicMock()

    monkeypatch.setattr(demo, "load_inventory", lambda: {
        "resources": {
            "bucket-01": {
                "id": "fenix-grupo-2-bucket-01",
                "status": "complete",
            }
        }
    })

    etiquetas = [{"Key": k, "Value": v} for k, v in tags.items()]
    cliente.list_buckets.return_value = {
        "Buckets": [{"Name": "fenix-grupo-2-bucket-01"}]
    }
    cliente.get_bucket_tagging.side_effect = [
        {"TagSet": etiquetas},
        {"TagSet": etiquetas},
    ]

    guardar = MagicMock()
    monkeypatch.setattr(demo, "save_resource", guardar)

    demo._ensure_bucket(
        cliente, "fenix-grupo-2-bucket-01", tags
    )

    cliente.put_bucket_tagging.assert_called_once()
    guardar.assert_called_once()


def test_ensure_bucket_crea_bucket_si_no_existe(monkeypatch):
    tags = {"Project": "fenix", "GroupId": "grupo-2"}
    cliente = MagicMock()
    cliente.list_buckets.return_value = {"Buckets": []}
    cliente.get_bucket_tagging.return_value = {
        "TagSet": [
            {"Key": k, "Value": v} for k, v in tags.items()
        ]
    }

    monkeypatch.setattr(
        demo, "load_inventory",
        lambda: {"resources": {}},
    )
    guardar = MagicMock()
    crear = MagicMock()

    monkeypatch.setattr(demo, "save_resource", guardar)
    monkeypatch.setattr(demo, "create_bucket", crear)
    monkeypatch.setattr(demo, "get_aws_region", lambda: "sa-east-1")

    demo._ensure_bucket(cliente, "fenix-grupo-2-bucket-01", tags)

    crear.assert_called_once_with(
        "fenix-grupo-2-bucket-01",
        s3_client=cliente,
        region="sa-east-1",
    )
    assert guardar.call_count == 2
    cliente.put_bucket_tagging.assert_called_once()


def test_ensure_bucket_rechaza_tags_de_otro_grupo(monkeypatch):
    tags = {"Project": "fenix", "GroupId": "grupo-2"}
    cliente = MagicMock()
    cliente.list_buckets.return_value = {
        "Buckets": [{"Name": "bucket-existente"}]
    }
    cliente.get_bucket_tagging.return_value = {
        "TagSet": [
            {"Key": "Project", "Value": "fenix"},
            {"Key": "GroupId", "Value": "otro-grupo"},
        ]
    }

    monkeypatch.setattr(
        demo, "load_inventory",
        lambda: {"resources": {}},
    )

    with pytest.raises(RuntimeError, match="Conflicto de propiedad"):
        demo._ensure_bucket(cliente, "bucket-existente", tags)

    cliente.put_bucket_tagging.assert_not_called()


def test_ensure_security_group_reutiliza_grupo_existente(monkeypatch):
    tags = {"Project": "fenix", "GroupId": "grupo-2"}
    grupo = {
        "GroupId": "sg-123",
        "Tags": [
            {"Key": k, "Value": v} for k, v in tags.items()
        ],
        "IpPermissions": [],
    }
    cliente = MagicMock()
    cliente.describe_security_groups.side_effect = [
        {"SecurityGroups": [grupo]},
        {"SecurityGroups": [grupo]},
    ]

    monkeypatch.setattr(demo, "load_inventory", lambda: {
        "resources": {
            "node-01": {
                "security_group_id": "sg-123",
                "security_group_name": "grupo-prueba",
                "expected_tags": tags,
            }
        }
    })
    guardar = MagicMock()
    monkeypatch.setattr(demo, "save_resource", guardar)

    resultado = demo._ensure_security_group(
        cliente, "grupo-prueba", "vpc-123", tags
    )

    assert resultado == "sg-123"
    cliente.create_security_group.assert_not_called()
    cliente.create_tags.assert_not_called()
    guardar.assert_called_once()


def test_ensure_security_group_rechaza_reglas_inesperadas(monkeypatch):
    tags = {"Project": "fenix"}
    grupo = {
        "GroupId": "sg-123",
        "Tags": [{"Key": "Project", "Value": "fenix"}],
        "IpPermissions": [{"IpProtocol": "tcp"}],
    }
    cliente = MagicMock()
    cliente.describe_security_groups.side_effect = [
        {"SecurityGroups": [grupo]},
        {"SecurityGroups": [grupo]},
    ]

    monkeypatch.setattr(
        demo, "load_inventory",
        lambda: {"resources": {}},
    )

    with pytest.raises(RuntimeError, match="reglas de entrada"):
        demo._ensure_security_group(
            cliente, "grupo-prueba", "vpc-123", tags
        )


def test_ensure_instance_reutiliza_instancia_existente(monkeypatch):
    import hashlib

    group_id = "grupo-2"
    security_group_id = "sg-123"
    tags = {"Project": "fenix", "GroupId": group_id}

    token = hashlib.sha256(
        f"fenix:{group_id}:node-01".encode("utf-8")
    ).hexdigest()

    instancia = {
        "InstanceId": "i-123",
        "ClientToken": token,
        "State": {"Name": "running"},
        "Tags": [
            {"Key": k, "Value": v} for k, v in tags.items()
        ],
    }

    instancia_verificada = {
        **instancia,
        "SecurityGroups": [{"GroupId": security_group_id}],
    }

    cliente = MagicMock()
    cliente.describe_instances.return_value = {
        "Reservations": [{
            "Instances": [instancia_verificada]
        }]
    }

    monkeypatch.setattr(demo, "load_inventory", lambda: {
        "resources": {
            "node-01": {
                "client_token": token,
                "security_group_id": security_group_id,
            }
        }
    })
    monkeypatch.setattr(
        demo, "_find_instance",
        lambda *args, **kwargs: instancia,
    )
    guardar = MagicMock()
    monkeypatch.setattr(demo, "save_resource", guardar)

    resultado = demo._ensure_instance(
        cliente, "ami-123", security_group_id, group_id, tags
    )

    assert resultado == {
        "InstanceId": "i-123",
        "State": "running",
    }
    cliente.run_instances.assert_not_called()
    guardar.assert_called_once()
