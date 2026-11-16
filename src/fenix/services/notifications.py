import os

import requests


def notify(message: str) -> bool:
    """Envía una notificación a un proveedor externo (console, telegram o slack)

    según la configuración de la variable de entorno NOTIFY_PROVIDER.

    Args:
        message (str): El texto de la notificación a enviar.

    Returns:
        bool: True si el envío fue exitoso, False en caso contrario.

    Raises:
        requests.exceptions.RequestException: Capturado internamente para
            evitar interrumpir el flujo principal ante fallos de red.
    """
    provider = os.getenv("NOTIFY_PROVIDER", "console").strip().lower()

    # 1. Modo consola (ideal para desarrollo y pruebas sin internet)
    if provider == "console":
        print(f"[NOTIFY CONSOLE] {message}")
        return True

    # 2. Modo Telegram
    elif provider == "telegram":
        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

        if not telegram_token or not telegram_chat_id:
            return False

        url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
        payload = {"chat_id": telegram_chat_id, "text": message}
        try:
            # Timeout estricto: 2s de conexión y 5s de lectura
            response = requests.post(url, json=payload, timeout=(2.0, 5.0))

            if response.status_code == 200:
                data = response.json()
                if data.get("ok") is True:
                    return True
            return False
        except requests.exceptions.RequestException:
            # Captura errores de red/timeout sin exponer tokens sensibles
            return False

    # 3. Modo Slack
    elif provider == "slack":
        slack_webhook_url = os.getenv("SLACK_WEBHOOK_URL")

        if not slack_webhook_url:
            return False

        payload = {"text": message}
        try:
            response = requests.post(
                slack_webhook_url, json=payload, timeout=(2.0, 5.0)
            )

            return response.status_code == 200 and response.text.strip() == "ok"
        except requests.exceptions.RequestException:
            return False

    return False
