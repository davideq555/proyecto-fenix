"""CLI de apoyo para operar y probar el flujo local de Fénix."""

from __future__ import annotations

import argparse  # Proporciona subcomandos reproducibles.
import hashlib  # Calcula HMAC-SHA256 para firmar envíos al webhook.
import hmac  # Genera el digest firmado del payload.
import json  # Lee archivos de eventos de prueba.
import os  # Recupera configuración del entorno.
import sys  # Comunica un código de salida al sistema operativo.
import time  # Obtiene epoch del emisor.
from pathlib import Path  # Lee archivos locales elegidos por el operador.

import requests  # Envía un webhook de prueba con bytes firmados.
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv  # Prepara el entorno de todos los comandos.

from fenix.demo.inject_fault import inject_fault
from fenix.services import demo, sqs, worker
from fenix.utils.errors import ProjectError
from fenix.utils.logging_json import setup


def _build_signature_header(secret: str, timestamp: int, raw_body: bytes) -> str:
    """Construye el header ``X-Webhook-Signature`` con formato ``t=...,v1=...``."""
    message = f"{timestamp}".encode("ascii") + b"." + raw_body
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def main() -> int:
    """Ejecuta un subcomando y cierra el logging antes de devolver el resultado.

    Returns:
        0 éxito o interrupción voluntaria; 1 error registrado; argparse usa 2.

    Raises:
        SystemExit: argparse muestra ayuda o rechaza argumentos.

    Side Effects:
        Ejecuta la operación elegida; nunca confirma mensajes desde finally.

    Notes:
        Durante desarrollo, NotImplementedError se informa como plantilla pendiente.
    """
    load_dotenv()  # No se lee ninguna credencial desde argumentos CLI.
    parser = argparse.ArgumentParser(description="Fénix autorreparación local")
    sub = parser.add_subparsers(dest="command", required=True)  # Un comando por vez.
    sub.add_parser("init-queues")  # Prepara work y DLQ.
    sub.add_parser("setup-demo")  # Crea el laboratorio autorizado.
    fault = sub.add_parser("fault")  # Introduce una falla controlada.
    fault.add_argument(
        "alias", choices=["node-01", "bucket-01"]
    )  # Sin IDs arbitrarios.
    send = sub.add_parser("send")  # Emite el evento por HTTP.
    send.add_argument("file")  # Archivo JSON visible para la defensa.
    consume = sub.add_parser("worker")  # Procesa la cola local.
    consume.add_argument("--once", action="store_true")  # Una consulta para pruebas.
    args = parser.parse_args()  # Errores sintácticos salen con código 2.
    logger, listener = setup("fenix-" + args.command)  # Archivo por proceso.
    code = 0  # El resultado se devuelve fuera de finally.
    try:  # Una frontera de entrada controla errores de las dependencias.
        if args.command == "init-queues":  # Crea primero la DLQ y luego la principal.
            print(sqs.ensure_queues())  # Lee nombres/atributos desde el entorno.
        elif args.command == "setup-demo":  # Crea o recupera recursos.
            print(demo.setup_demo())  # Lee GROUP_ID desde el entorno.
        elif args.command == "fault":  # Perturba solo el inventario del grupo.
            print(inject_fault(args.alias))  # I6: demo/inject_fault.py.
        elif args.command == "send":  # Firma exactamente lo que envía.
            body = Path(args.file).read_bytes()  # No reserializa después de firmar.
            json.loads(body)  # Rechaza archivos no JSON antes de enviar.
            timestamp = int(time.time())  # La firma autentica también este número.
            sig_header = _build_signature_header(
                os.environ["WEBHOOK_SECRET"],
                timestamp,
                body,
            )
            response = requests.post(
                "http://127.0.0.1:8000/webhook",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Webhook-Signature": sig_header,
                },
                timeout=(2, 15),
            )
            response.raise_for_status()  # No mostrar éxito ante 4xx o 5xx.
            print(
                response.status_code, response.text
            )  # Respuesta pública del receptor.
        elif args.command == "worker":  # Consume mensajes de la cola.
            acknowledged = worker.run(once=args.once)  # Usa dependencias del entorno.
            logger.info(
                "worker_finalizado",
                extra={"action": "worker", "status": f"ack={acknowledged}"},
            )
    except KeyboardInterrupt:  # Detención manual esperada.
        logger.info("detenido_por_operador")  # No se captura BaseException.
    except (
        ProjectError,
        BotoCoreError,
        ClientError,
        OSError,
        ValueError,
        KeyError,
        requests.RequestException,
        NotImplementedError,
    ) as error:
        logger.error("fallo_controlado", extra={"status": type(error).__name__})
        print(
            "Revisar configuración o TODO pendiente:", type(error).__name__
        )  # Sin URL/token.
        code = 1  # Un fallo no se presenta como ejecución exitosa.
    finally:  # Se libera el pipeline tanto en éxito como en error.
        listener.stop()  # No return, break ni continue dentro de finally.
    return code  # El código de salida refleja el resultado de la ejecución.


if __name__ == "__main__":  # Permite python -m fenix.cli.
    sys.exit(main())  # Hace visible el resultado a la terminal.
