"""Adaptador HTTP guiado. Elegir Telegram O Slack, no ambos obligatoriamente."""

import os

import requests

from fenix.utils.errors import PermanentError, RetryableError


def notify(text: str) -> None:
    """Envía un mensaje breve y valida la respuesta del proveedor seleccionado.

    Args:
        text: Resumen público de hasta 1000 caracteres, sin secretos ni raw body.

    Returns:
        None únicamente después de confirmar éxito del proveedor.

    Raises:
        RetryableError: Timeout, conexión, 429 o respuesta 5xx.
        PermanentError: Configuración inválida, 4xx definitivo o rechazo funcional.
    """
    safe_text = text[:1000]
    mode = os.getenv("NOTIFY_PROVIDER", "console").strip().lower()

    if mode == "console":
        print(f"[NOTIFY CONSOLE] {safe_text}")
        return

    if mode == "telegram":
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

        if not token or not chat_id:
            raise PermanentError("Configuración de Telegram incompleta en el entorno")

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": safe_text}

        try:
            response = requests.post(url, json=payload, timeout=(2, 5))
        except (requests.Timeout, requests.ConnectionError):
            # Se usa 'from None' para no filtrar la URL con el token en el traceback
            raise RetryableError("Timeout o fallo de conexión con Telegram") from None
        except requests.RequestException:
            raise PermanentError("Error de petición HTTP hacia Telegram") from None

        _check_http_status(response, provider="Telegram")

        try:
            data = response.json()
        except ValueError:
            raise PermanentError("Respuesta de Telegram no es un JSON válido") from None

        if data.get("ok") is not True:
            raise PermanentError("Telegram rechazó el envío (ok != True)")

        return

    if mode == "slack":
        webhook_url = os.getenv("SLACK_WEBHOOK_URL", "").strip()
        if not webhook_url:
            raise PermanentError("SLACK_WEBHOOK_URL no está configurado en el entorno")

        try:
            response = requests.post(
                webhook_url, json={"text": safe_text}, timeout=(2, 5)
            )
        except (requests.Timeout, requests.ConnectionError):
            raise RetryableError("Timeout o fallo de conexión con Slack") from None
        except requests.RequestException:
            raise PermanentError("Error de petición HTTP hacia Slack") from None

        _check_http_status(response, provider="Slack")

        if response.text.strip() != "ok":
            raise PermanentError("Slack rechazó el envío (respuesta != 'ok')")

        return

    raise PermanentError(f"Proveedor de notificaciones desconocido: {mode}")


def _check_http_status(response: requests.Response, provider: str) -> None:
    """Evalúa el código HTTP según la clasificación de errores transitorios/permanentes."""
    status = response.status_code
    if status == 200:
        return
    if status == 429:
        retry_after = response.headers.get("Retry-After", "desconocido")
        raise RetryableError(
            f"Rate limit (429) en {provider}. Retry-After: {retry_after}"
        )
    if 500 <= status < 600:
        raise RetryableError(f"Error de servidor ({status}) en {provider}")
    if 400 <= status < 500:
        raise PermanentError(
            f"Error de cliente ({status}) en {provider}: verificar credenciales o formato"
        )
    raise PermanentError(f"Código HTTP inesperado ({status}) en {provider}")
