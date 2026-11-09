# Proyecto Fénix — Sistema de Autorreparación de Infraestructura

TP Final 1 · Programación para Automatización II · Opción 4.3.1

Sistema que recibe una alerta por webhook autenticado, la guarda en SQS,
repara un recurso emulado con Boto3 sobre LocalStack y comunica el resultado
a Telegram o Slack.

- `node-01` (EC2 mock) pasa de `stopped` a `running`.
- `bucket-01` (S3) recupera sus etiquetas obligatorias.
- Los mensajes válidos se confirman **después** de reparar y avisar.
- Los fallidos se reintentan con backoff y terminan en la DLQ.
- Repetir un evento o reiniciar el worker no repite acciones ya verificadas.

---

## Estado actual

Esqueleto inicial: la estructura de paquetes existe, pero **los módulos están
vacíos**. Falta incorporar la plantilla de la cátedra y completar los `TODO`.

Consecuencias esperadas hasta entonces:

- `python -m fenix.cli` falla.
- `pytest` termina con código 5 (`no tests ran`).

No es un problema de entorno: es el estado del repo.

---

## Requisitos

- **Python 3.11 o superior**, con la **misma versión en todo el grupo**.
- **Docker** con Docker Desktop iniciado (o Docker Engine + Compose v2).
- **Git**.

---

## Puesta en marcha

### 1. Clonar el repositorio

```bash
git clone <URL-del-repo>
cd proyecto_fenix
```

### 2. Crear y activar la `.venv`

```bash
python -m venv .venv
source .venv/bin/activate
```

En Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
```

Actualizar pip dentro del entorno:

```bash
python -m pip install --upgrade pip
```

### 3. Instalar dependencias

Si `requirements-lock.txt` **tiene contenido**, instalación reproducible:

```bash
python -m pip install -r requirements-lock.txt
python -m pip install -e . --no-deps
```

Si `requirements-lock.txt` **todavía está vacío** (aún no se generó):

```bash
python -m pip install -r requirements.txt
```

### 4. Configurar variables de entorno

```bash
cp .env.example .env
```

En Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

Generar un secreto para el webhook:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Pegar el valor en `WEBHOOK_SECRET` dentro de `.env`.

Completar `LOCALSTACK_AUTH_TOKEN` **solo si** la imagen de LocalStack lo exige.

`.env` no se versiona: nunca commitear tokens ni secretos.

### 5. Levantar LocalStack

```bash
docker compose up -d
docker compose ps
curl -s http://localhost:4566/_localstack/health
```

En Windows PowerShell, usar `curl.exe -s ...` (el alias `curl` apunta a
`Invoke-WebRequest` y no acepta `-s`).

LocalStack queda publicado únicamente en `127.0.0.1:4566`.

- `docker compose down` conserva los recursos (volumen + persistencia).
- `docker compose down -v` borra el volumen y arranca de cero.

### 6. Verificar la instalación

```bash
python -c "import fenix; print(fenix.__file__)"
```

Debe imprimir una ruta dentro de `src/fenix/`.

---

## Estructura del proyecto

```text
src/fenix/
├── cli.py
├── config/
│   └── settings.py
├── demo/
│   └── inject_fault.py
├── services/
│   ├── cloud_support.py
│   ├── demo.py
│   ├── events.py
│   ├── notifications.py
│   ├── receiver.py
│   ├── remediation.py
│   ├── sqs.py
│   └── worker.py
└── utils/
    ├── crypto.py
    ├── errors.py
    ├── logging_json.py
    ├── state.py
    └── validation.py

tests/test_fenix.py
examples/
data/          # runtime, no se versiona
logs/          # runtime, no se versiona
evidencias/    # capturas y recortes anonimizados, sí se versiona
docker-compose.yaml
```

---

## Roles

| Rol | Módulos principales | Integrante |
| --- | --- | --- |
| I1 | `utils/errors.py`, `utils/validation.py`, `services/events.py` | Dorado, Tomas |
| I2 | `services/sqs.py`, `services/demo.py` | |
| I3 | `utils/crypto.py`, `services/receiver.py`, `services/remediation.py` (`repair`) | Aramayo, David |
| I4 | `utils/logging_json.py`, `services/notifications.py` | Dorado, Mauricio |
| I5 | `services/worker.py`, `services/remediation.py` (`handle`), `cli.py` | |
| I6 | `tests/`, `demo/inject_fault.py` | |

---

## Dependencias: `requirements.txt` vs `requirements-lock.txt`

| Archivo | Qué contiene | Para qué |
| --- | --- | --- |
| `requirements.txt` | Solo mínimos (`paquete>=version`) | Declara el piso de compatibilidad |
| `requirements-lock.txt` | Versiones exactas (`paquete==version`) | Instalación reproducible |

El lock se genera después de instalar y verificar:

```bash
python -m pip freeze --exclude-editable > requirements-lock.txt
```

Se usa `--exclude-editable` para no guardar la ruta absoluta del proyecto.

---

## Datos de runtime

- `data/`: inventario y ledger. Propios de cada máquina; **no se versionan**.
- `logs/`: logs JSON y rotación gzip. Propios de cada máquina.
- `evidencias/`: copias anonimizadas para la corrección. **Sí se versionan**.

No borrar `data/` mientras existan recursos en LocalStack: contiene los IDs
que permiten reconocerlos.

---

## Problemas comunes

| Síntoma | Causa probable |
| --- | --- |
| `docker: command not found` | Docker Desktop no está iniciado o no está instalado |
| LocalStack pide Auth Token | Completar `LOCALSTACK_AUTH_TOKEN` en `.env` |
| `import fenix` falla | `.venv` sin activar o falta `pip install -e .` |
| `python -m fenix.cli` falla | Módulos vacíos: falta la plantilla de la cátedra |
---

## Pendiente para la entrega final

- Incorporar la plantilla de la cátedra y completar los `TODO`.
- Implementar la matriz de pruebas F01–F16.
- Completar el README con las secciones exigidas por el enunciado
  (contratos, diagrama Mermaid, matriz con esperado/obtenido, limitaciones,
  evidencia del canal elegido, limpieza y video).
- Grabar el video grupal y preparar la defensa individual.
