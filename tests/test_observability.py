import gzip
import json
import logging
from logging.handlers import RotatingFileHandler

from fenix.utils.logging_json import JSONFormatter, gzip_name, gzip_rotate


def test_gzip_rotation_and_json_contract(tmp_path) -> None:
    """Verifica rotación por tamaño reducido, compresión gzip y estructura JSON oficial."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = log_dir / "fenix-test.jsonl"

    # Configuramos RotatingFileHandler con tamaño reducido (250 bytes) y los callbacks oficiales
    sink = RotatingFileHandler(
        str(log_file), maxBytes=250, backupCount=3, encoding="utf-8"
    )
    sink.setFormatter(JSONFormatter())
    sink.namer, sink.rotator = gzip_name, gzip_rotate

    logger = logging.getLogger("test_fenix_official")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False
    logger.addHandler(sink)

    # Emitimos eventos con los campos de correlación permitidos por el contrato
    for i in range(25):
        logger.info(
            "Evento de prueba para rotación gzip",
            extra={
                "event_id": f"evt-{i}",
                "message_id": f"msg-{i}",
                "resource_id": "i-0123456789abcdef0",
                "attempt": 1,
                "action": "isolate_instance",
                "status": "ok",
            },
        )

    sink.close()
    logger.removeHandler(sink)

    # Verificamos que existan archivos rotados y comprimidos en .gz
    gz_files = list(log_dir.glob("*.gz"))
    assert len(gz_files) > 0, "Debe generarse al menos un histórico comprimido .gz"

    # Leemos el contenido descomprimido y validamos el esquema JSON
    for gz_path in gz_files:
        with gzip.open(gz_path, "rt", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    record = json.loads(line)
                    assert "timestamp" in record
                    assert "level" in record
                    assert record["event_id"].startswith("evt-")
                    assert record["action"] == "isolate_instance"
