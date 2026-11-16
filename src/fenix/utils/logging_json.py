import gzip  # Comprime los históricos rotados.
import json  # Emite una estructura por línea.
import logging  # Usa la biblioteca estándar de observabilidad.
import os  # Elimina el archivo original después de comprimirlo.
import queue  # Desacopla productores y escritura en un hilo del mismo proceso.
import shutil  # Copia el archivo al compresor.
from datetime import UTC, datetime  # Emite marcas UTC.
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path  # Crea la carpeta de logs.


class JSONFormatter(logging.Formatter):
    """Serializa campos permitidos; no copia indiscriminadamente record.__dict__.

    Notes:
        Cada proceso usa su propio archivo para evitar rotaciones concurrentes.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Convierte un registro en JSON compatible con json.loads.

        Args:
            record: Registro del logger con extras públicos opcionales.
        Returns:
            Una línea JSON con fecha UTC, nivel, evento y contexto autorizado.
        Raises:
            TypeError: Si un extra no es serializable; usar escalares públicos.
        """
        data = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "process": record.process,
            "thread": record.threadName,
        }  # Campos base.
        for name in (
            "event_id",
            "message_id",
            "resource_id",
            "attempt",
            "action",
            "status",
        ):
            data[name] = getattr(record, name, None)  # Lista cerrada de extras.
        return json.dumps(data, ensure_ascii=False)  # El mensaje no contiene secretos.


def gzip_name(name: str) -> str:
    """Calcula el nombre comprimido de un histórico.

    Args:
        name: Nombre propuesto por RotatingFileHandler.
    Returns:
        El mismo nombre con sufijo .gz.
    """
    return name + ".gz"  # Permite rotaciones y backups automáticos.


def gzip_rotate(source: str, dest: str) -> None:
    """Comprime y luego elimina el histórico original.

    Args:
        source: Archivo cerrado por el manejador.
        dest: Ruta del archivo comprimido.
    Returns:
        None.
    Raises:
        OSError: Si falla la lectura, compresión o eliminación.
    Notes:
        Esta versión didáctica no promete publicación atómica del gzip.
    """
    with open(source, "rb") as origin, gzip.open(dest, "wb") as target:
        shutil.copyfileobj(origin, target)  # Procesa el contenido sin leerlo entero.
    os.remove(source)  # Solo se borra después del cierre exitoso del gzip.


def setup(name: str) -> tuple[logging.Logger, QueueListener]:
    """Inicia el pipeline JSON en un archivo exclusivo de este proceso.

    Args:
        name: Nombre público del componente, por ejemplo fenix-worker.
    Returns:
        Logger y listener; el llamador debe ejecutar listener.stop en finally.
    Raises:
        OSError: Si no se puede crear logs/ o abrir el archivo.
    Side Effects:
        Inicia un hilo y abre un manejador rotativo; llamar una vez por proceso.
    """
    Path("logs").mkdir(exist_ok=True)  # Prepara una ruta local ignorada por Git.
    sink = RotatingFileHandler(
        f"logs/{name}-{os.getpid()}.jsonl",
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )  # Limita crecimiento.
    sink.setFormatter(JSONFormatter())  # Toda línea mantiene el mismo contrato.
    sink.namer, sink.rotator = gzip_name, gzip_rotate  # Asocia callbacks.
    records = queue.Queue()  # Esta cola en memoria NO sustituye a SQS.
    logger = logging.getLogger(name)  # Mantiene un nombre por componente.
    logger.setLevel(logging.DEBUG)  # Permite demostrar los cinco niveles.
    logger.handlers.clear()  # Evita duplicados si se reinicializa por una prueba.
    logger.propagate = False  # No replica mensajes en el logger raíz.
    logger.addHandler(QueueHandler(records))  # El productor no escribe en disco.
    listener = QueueListener(records, sink)  # Solo este hilo escribe el archivo.
    listener.start()  # Empieza el consumo de registros.
    return logger, listener  # El cierre pertenece al llamador.
