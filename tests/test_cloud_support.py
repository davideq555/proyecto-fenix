import pytest
from unittest.mock import MagicMock

from fenix.services import cloud_support


def test_aws_tags():
    tags = cloud_support.aws_tags("grupo-2")
    assert tags == {
        "ManagedBy": "ppa2",
        "Project": "fenix",
        "GroupId": "grupo-2",
        "Owner": "grupo-2",
    }


def test_aws_tags_usa_variable_entorno(monkeypatch):
    monkeypatch.setenv("GROUP_ID", "grupo-3")
    assert cloud_support.aws_tags()["GroupId"] == "grupo-3"


def test_aws_tags_rechaza_grupo_vacio():
    with pytest.raises(ValueError):
        cloud_support.aws_tags(" ")


def test_mock_inputs_prefiere_vpc_predeterminada():
    cliente = MagicMock()
    cliente.describe_vpcs.return_value = {
        "Vpcs": [
            {"VpcId": "vpc-1", "IsDefault": False},
            {"VpcId": "vpc-2", "IsDefault": True},
        ]
    }
    cliente.describe_images.return_value = {
        "Images": [{"ImageId": "ami-1", "Name": "imagen"}]
    }

    resultado = cloud_support.mock_inputs(cliente)
    assert resultado == {"vpc_id": "vpc-2", "image_id": "ami-1"}


def test_mock_inputs_prefiere_ami_ecs():
    cliente = MagicMock()
    cliente.describe_vpcs.return_value = {
        "Vpcs": [{"VpcId": "vpc-1"}]
    }
    cliente.describe_images.return_value = {
        "Images": [
            {"ImageId": "ami-z", "Name": "imagen normal"},
            {"ImageId": "ami-a", "Name": "amzn2-ami-ecs-hvm"},
        ]
    }

    assert cloud_support.mock_inputs(cliente)["image_id"] == "ami-a"


def test_mock_inputs_falla_sin_vpc():
    cliente = MagicMock()
    cliente.describe_vpcs.return_value = {"Vpcs": []}

    with pytest.raises(RuntimeError, match="VPC"):
        cloud_support.mock_inputs(cliente)

    cliente.describe_images.assert_not_called()


def test_mock_inputs_falla_sin_ami():
    cliente = MagicMock()
    cliente.describe_vpcs.return_value = {
        "Vpcs": [{"VpcId": "vpc-1"}]
    }
    cliente.describe_images.return_value = {"Images": []}

    with pytest.raises(RuntimeError, match="AMI"):
        cloud_support.mock_inputs(cliente)


def test_mock_inputs_falla_vpc_sin_id():
    cliente = MagicMock()
    cliente.describe_vpcs.return_value = {
        "Vpcs": [{"IsDefault": True}]
    }

    with pytest.raises(RuntimeError, match="VpcId"):
        cloud_support.mock_inputs(cliente)


@pytest.mark.parametrize("nombre", ["", " ", None, 123])
def test_create_bucket_rechaza_nombre_invalido(nombre):
    with pytest.raises(ValueError):
        cloud_support.create_bucket(nombre, s3_client=MagicMock())


def test_create_bucket_region_us_east_1():
    cliente = MagicMock()

    resultado = cloud_support.create_bucket(
        " bucket-prueba ",
        s3_client=cliente,
        region="us-east-1",
    )

    assert resultado == "bucket-prueba"
    cliente.create_bucket.assert_called_once_with(Bucket="bucket-prueba")


def test_create_bucket_otra_region():
    cliente = MagicMock()

    cloud_support.create_bucket(
        "bucket-prueba",
        s3_client=cliente,
        region="sa-east-1",
    )

    assert cliente.create_bucket.call_args.kwargs == {
        "Bucket": "bucket-prueba",
        "CreateBucketConfiguration": {
            "LocationConstraint": "sa-east-1"
        },
    }


def test_create_group_rechaza_datos_invalidos():
    cliente = MagicMock()

    with pytest.raises(ValueError):
        cloud_support.create_group("", "vpc-1", {"Project": "fenix"}, cliente)

    with pytest.raises(ValueError):
        cloud_support.create_group("grupo", "", {"Project": "fenix"}, cliente)

    with pytest.raises(ValueError):
        cloud_support.create_group("grupo", "vpc-1", {}, cliente)

    cliente.create_security_group.assert_not_called()


def test_create_group_crea_y_etiqueta():
    cliente = MagicMock()
    cliente.create_security_group.return_value = {"GroupId": "sg-123"}

    resultado = cloud_support.create_group(
        "grupo-prueba",
        "vpc-123",
        {"Project": "fenix", "GroupId": "grupo-2"},
        cliente,
    )

    assert resultado == "sg-123"
    cliente.create_security_group.assert_called_once()
    cliente.create_tags.assert_called_once_with(
        Resources=["sg-123"],
        Tags=[
            {"Key": "Project", "Value": "fenix"},
            {"Key": "GroupId", "Value": "grupo-2"},
        ],
    )


def test_create_group_falla_sin_group_id():
    cliente = MagicMock()
    cliente.create_security_group.return_value = {}

    with pytest.raises(RuntimeError, match="GroupId"):
        cloud_support.create_group(
            "grupo", "vpc-1", {"Project": "fenix"}, cliente
        )

    cliente.create_tags.assert_not_called()


def test_create_instance_rechaza_datos_invalidos():
    cliente = MagicMock()

    with pytest.raises(ValueError):
        cloud_support.create_instance(
            "", "sg-1", "token", {"Project": "fenix"}, cliente
        )

    with pytest.raises(ValueError):
        cloud_support.create_instance(
            "ami-1", "sg-1", "token", {}, cliente
        )

    cliente.run_instances.assert_not_called()


def test_create_instance_crea_y_devuelve_datos():
    cliente = MagicMock()
    cliente.run_instances.return_value = {
        "Instances": [{
            "InstanceId": "i-123",
            "State": {"Name": "pending"},
        }]
    }

    resultado = cloud_support.create_instance(
        "ami-123",
        "sg-123",
        "token-estable",
        {"Project": "fenix"},
        cliente,
    )

    assert resultado == {
        "InstanceId": "i-123",
        "State": "pending",
    }

    llamada = cliente.run_instances.call_args.kwargs
    assert llamada["ImageId"] == "ami-123"
    assert llamada["InstanceType"] == "t2.micro"
    assert llamada["MinCount"] == 1
    assert llamada["MaxCount"] == 1
    assert llamada["SecurityGroupIds"] == ["sg-123"]
    assert llamada["ClientToken"] == "token-estable"
    assert [t["ResourceType"] for t in llamada["TagSpecifications"]] == [
        "instance", "volume"
    ]


def test_create_instance_acepta_tipo_personalizado():
    cliente = MagicMock()
    cliente.run_instances.return_value = {
        "Instances": [{"InstanceId": "i-456"}]
    }

    resultado = cloud_support.create_instance(
        "ami-1",
        "sg-1",
        "token-1",
        {"Project": "fenix"},
        cliente,
        instance_type="t3.small",
    )

    assert resultado == {"InstanceId": "i-456", "State": "unknown"}
    assert cliente.run_instances.call_args.kwargs["InstanceType"] == "t3.small"


def test_create_instance_falla_sin_instancias():
    cliente = MagicMock()
    cliente.run_instances.return_value = {"Instances": []}

    with pytest.raises(RuntimeError, match="InstanceId"):
        cloud_support.create_instance(
            "ami-1", "sg-1", "token-1", {"Project": "fenix"}, cliente
        )


def test_create_instance_falla_si_instancia_no_tiene_id():
    cliente = MagicMock()
    cliente.run_instances.return_value = {
        "Instances": [{"State": {"Name": "pending"}}]
    }

    with pytest.raises(RuntimeError, match="InstanceId"):
        cloud_support.create_instance(
            "ami-1", "sg-1", "token-1", {"Project": "fenix"}, cliente
        )
