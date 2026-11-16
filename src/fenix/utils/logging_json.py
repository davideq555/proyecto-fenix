import gzip
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Any


class GzipRotatingFileHandler(RotatingFileHandler):
    """Handler personalizado que extiende RotatingFileHandler para comprimir
    los archivos de log rotados en formato gzip automáticamente.
    """

    def doRollover(self) -> None:
        """Realiza la rotación del archivo de log y comprime el archivo
        resultante utilizando gzip.
        """
        super().doRollover()
        if self.backupCount > 0:
            for i in range(self.backupCount - 1, 0, -1):
                sfn = f"{self.baseFilename}.{i}.gz"
                dfn = f"{self.baseFilename}.{i + 1}.gz"
                if os.path.exists(sfn):
                    if os.path.exists(dfn):
                        os.remove(dfn)
                    os.rename(sfn, dfn)
            dfn = f"{self.baseFilename}.1.gz"
            if os.path.exists(dfn):
                os.remove(dfn)
            if os.path.exists(self.baseFilename + ".1"):
                # Combinamos los contextos with según la recomendación de Ruff (SIM117)
                with (
                    open(self.baseFilename + ".1", "rb") as f_in,
                    gzip.open(dfn, "wb") as f_out,
                ):
                    f_out.writelines(f_in)
                os.remove(self.baseFilename + ".1")


class JsonFormatter(logging.Formatter):
    """Formateador de logs que convierte los registros en una estructura
    JSON limpia y uniforme para facilitar su observabilidad.
    """

    def format(self, record: logging.LogRecord) -> str:
        log_record: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_record)


def setup_logger(name: str = "fenix") -> logging.Logger:
    """Configura y retorna una instancia de logger con salida en consola
    y soporte para archivos rotados y comprimidos en formato gzip.

    Args:
        name (str): Nombre del logger. Por defecto "fenix".

    Returns:
        logging.Logger: Instancia configurada del logger.
    """
    logger = logging.getLogger(name)

    # Evitar duplicación de handlers si se llama múltiples veces
    if logger.handlers:
        return logger

    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    logger.setLevel(getattr(logging, log_level, logging.INFO))

    log_dir = os.getenv("LOG_DIR", "logs")
    os.makedirs(log_dir, exist_ok=True)

    log_file = os.path.join(log_dir, "fenix.log")

    # Valores por defecto como strings para cumplir con PLW1508
    max_bytes = int(os.getenv("LOG_MAX_BYTES", "1048576"))  # 1 MB por defecto
    backup_count = int(os.getenv("LOG_BACKUP_COUNT", "5"))

    # Formateador JSON
    formatter = JsonFormatter()

    # Handler para archivo con rotación y compresión gzip
    file_handler = GzipRotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Handler para consola (salida estándar)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger
