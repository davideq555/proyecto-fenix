"""Contratos inicialmente pendientes; el alumno debe hacerlos pasar sin borrarlos."""

import pytest  # Ejecuta los escenarios parametrizados.
from fenix.utils.errors import ValidationError
from fenix.utils.validation import identifier, ttl_seconds, utc_text


@pytest.mark.pendiente
def test_identifier_contract():
    """Exige conservar un ID válido y rechazar rutas o mayúsculas.

    Returns:
        None si las validaciones cumplen su contrato.
    Raises:
        AssertionError: Si acepta un valor inválido.
        NotImplementedError: Hasta que se complete I1-A.
    """
    assert identifier("grupo-01") == "grupo-01"  # No normaliza identidad.
    with pytest.raises(ValidationError):  # Bloquea rutas no autorizadas.
        identifier("../AJENO")  # Entrada maliciosa didáctica.


@pytest.mark.pendiente
def test_ttl_contract():
    """Comprueba límites incluidos y el rechazo de un TTL excesivo.

    Returns:
        None si TTL queda entre 60 y 600 segundos.
    Raises:
        AssertionError: Si acepta un valor fuera de rango.
        NotImplementedError: Hasta que se complete I1-B.
    """
    assert ttl_seconds("60") == 60  # Límite inferior permitido.
    assert ttl_seconds("600") == 600  # Límite superior permitido.
    with pytest.raises(ValidationError):  # No se redondean entradas.
        ttl_seconds("601")  # Supera el presupuesto didáctico.


@pytest.mark.pendiente
def test_utc_contract():
    """Exige que las fechas de expiración tengan UTC explícito.

    Returns:
        None si se acepta Z y se rechaza una fecha sin zona.
    Raises:
        AssertionError: Si una fecha ingenua resulta válida.
        NotImplementedError: Hasta que se complete I1-C.
    """
    assert utc_text("2026-10-22T18:00:00Z").utcoffset().total_seconds() == 0
    with pytest.raises(ValidationError):  # Sin zona no hay comparación segura.
        utc_text("2026-10-22T18:00:00")  # Evita depender del reloj local.
