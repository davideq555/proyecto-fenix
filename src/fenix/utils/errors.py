"""
Definición de excepciones personalizadas para el dominio del Proyecto Fénix.
Rol 1 (I1) - Manejo estricto de errores de dominio y contrato.
"""


class FenixError(Exception):
    """Excepción base para todos los errores de dominio del Proyecto Fénix."""

    def __init__(self, message: str, details: dict | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ValidationError(FenixError):
    """
    Se lanza cuando un evento, payload o alguno de sus campos incumple
    estrictamente con el contrato JSON o las especificaciones de formato.
    """


class InventoryError(FenixError):
    """
    Se lanza cuando un recurso especificado no se encuentra registrado
    en el inventario del grupo o presenta inconsistencias de tipo.
    """