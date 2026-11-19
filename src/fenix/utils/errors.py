"""Excepciones compartidas. Integrante 1 revisa cuándo usar cada tipo."""


class ProjectError(Exception):
    """Error controlado del TP; base para informar y cerrar con código 1.

    Args:
        message: Descripción pública, sin tokens ni URLs privadas.
    """


class ValidationError(ProjectError):
    """Entrada inválida o recurso fuera del inventario autorizado.

    Args:
        message: Regla incumplida, sin copiar secretos de la entrada.
    """


class RetryableError(ProjectError):
    """Fallo transitorio que permite una nueva recepción de SQS.

    Args:
        message: Descripción del paso pendiente, sin datos sensibles.
    """


class PermanentError(ProjectError):
    """Fallo no recuperable que se conserva hasta llegar a la DLQ.

    Args:
        message: Motivo público del rechazo definitivo del evento.
    """
