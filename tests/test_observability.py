import gzip
import json
import logging

from fenix.utils.logging_json import GzipRotatingFileHandler, JsonFormatter


def test_gzip_rotating_handler_and_json_logging(tmp_path) -> None:
    """Verifica que el logger escriba en JSON válido, fuerce la rotación

    por tamaño reducido y comprima el archivo resultante en formato gzip (F16).
    """
    # Usamos la carpeta temporal aislada de pytest (evita saturar el disco)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = log_dir / "fenix.log"

    # Configuramos un logger dedicado para la prueba
    logger_name = "test_observability_logger"
    test_logger = logging.getLogger(logger_name)
    test_logger.handlers.clear()
    test_logger.setLevel(logging.INFO)
    test_logger.propagate = False

    # Instanciamos el GzipRotatingFileHandler con un tamaño muy chico (maxBytes=200)
    # para forzar la rotación de inmediato sin generar megabytes de logs.
    file_handler = GzipRotatingFileHandler(
        str(log_file), maxBytes=200, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(JsonFormatter())
    test_logger.addHandler(file_handler)

    # Escribimos suficientes registros para superar los 200 bytes y provocar la rotación
    for i in range(30):
        test_logger.info(
            "Mensaje de prueba para verificar rotación y compresión gzip",
            extra={"event_id": f"evt-test-{i}"},
        )

    # Cerramos el handler para liberar los archivos en disco
    file_handler.close()
    test_logger.removeHandler(file_handler)

    # Verificamos que se hayan generado archivos comprimidos .gz (histórico comprimido)
    compressed_files = list(log_dir.glob("*.gz"))
    assert len(compressed_files) > 0, (
        "Debe existir al menos un archivo de respaldo comprimido en .gz"
    )

    # Abrimos los archivos .gz y validamos que cada línea contenga un JSON válido con correlación
    for gz_path in compressed_files:
        with gzip.open(gz_path, "rt", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    parsed_record = json.loads(line)
                    assert "timestamp" in parsed_record
                    assert "level" in parsed_record
                    assert "message" in parsed_record
