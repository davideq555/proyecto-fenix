- **Fecha:** 2026-11-19
- **Rama:** `feat/i3-webhook-repair`
- **Commit:** `0a9726e`
- **Responsable:** Integrante 3 (I3)
- **Alcance:** pruebas unitarias de firma HMAC, receptor HTTP, adaptador de
  publicación en SQS y reparación convergente de EC2 y S3.

---

## Cómo se ejecutan

Las pruebas son unitarias: no levantan LocalStack ni servicios externos. Los
clientes de AWS se reemplazan por dobles (`Mock` y clientes fake) y el reloj se
inyecta donde hace falta.

```bash
python -m pytest \
  tests/test_crypto.py \
  tests/test_receiver.py \
  tests/test_receiver_sqs.py \
  tests/test_remediation_ec2.py \
  tests/test_remediation_s3.py -q
Cada archivo se puede ejecutar por separado. La salida por archivo es:

```text
tests/test_crypto.py           26 passed in 0.02s
tests/test_receiver.py         17 passed, 1 warning in 0.25s
tests/test_receiver_sqs.py     9 passed, 1 warning in 0.24s
tests/test_remediation_ec2.py  21 passed in 0.02s
tests/test_remediation_s3.py   17 passed in 0.01s
```

## Entorno de ejecución

| Componente | Versión |
|---|---|
| Python | 3.15.0 |
| pytest | 9.2.0 |
| boto3 | 1.44.7 |
| fastapi | 0.165.0 |
| httpx | 0.28.1 |

Se usó un entorno virtual con las dependencias declaradas en
`requirements.txt` y `pyproject.toml`.

## Resultado global

```text
90 passed, 1 warning in 0.29s
```

| Archivo | Área | Casos | Resultado | Tiempo |
|---|---|---:|---|---:|
| `tests/test_crypto.py` | Firma HMAC del webhook | 26 | 26 passed | 0.02 s |
| `tests/test_receiver.py` | Receptor HTTP | 17 | 17 passed | 0.25 s |
| `tests/test_receiver_sqs.py` | Adaptador de publicación SQS | 9 | 9 passed | 0.24 s |
| `tests/test_remediation_ec2.py` | Reparación convergente EC2 | 21 | 21 passed | 0.02 s |
| `tests/test_remediation_s3.py` | Reparación de etiquetas S3 | 17 | 17 passed | 0.01 s |
| **Total** | | **90** | **90 passed** | **0.29 s** |

---

## Casos representativos

| Área | Caso | Resultado esperado | Observado |
|---|---|---|---|
| Firma | Firma válida sobre bytes crudos | Acepta | ✅ |
| Firma | Cuerpo alterado después de firmar | Rechaza | ✅ |
| Firma | Timestamp fuera de ±300 s | Rechaza | ✅ |
| Firma | Timestamp exactamente en el borde | Acepta | ✅ |
| Firma | Header duplicado, ambiguo o con hex inválido | Rechaza | ✅ |
| Firma | Comparación en tiempo constante | Usa `hmac.compare_digest` | ✅ |
| Receptor | Cuerpo mayor a 16 KiB | `413` sin publicar | ✅ |
| Receptor | Firma ausente, inválida o vencida | `401` sin publicar | ✅ |
| Receptor | Inventario envuelto en `resources` | Se desenvuelve y valida | ✅ |
| Receptor | SQS no disponible | `503` sin aceptación falsa | ✅ |
| Receptor | SQS confirma el mensaje | `202` con `event_id` y `message_id` | ✅ |
| SQS | Respuesta de Boto3 o `MessageId` como texto | Extrae el identificador | ✅ |
| SQS | Respuesta sin `MessageId` | Falla y deriva en `503` | ✅ |
| EC2 | Instancia `stopped` | `start` y verificación de `running` | ✅ |
| EC2 | Instancia ya `running` | No ejecuta `start` | ✅ |
| EC2 | Credenciales rechazadas (`AuthFailure`, `AccessDenied`) | Fallo permanente | ✅ |
| EC2 | Espera mayor a 30 s | `ValueError` de configuración | ✅ |
| EC2 | ID del evento distinto del inventario | Usa el ID del inventario | ✅ |
| S3 | Bucket sin etiquetas (`NoSuchTagSet`) | Restaura las esperadas | ✅ |
| S3 | Etiquetas ajenas presentes | Las preserva | ✅ |
| S3 | Clave en conflicto | Gana la esperada | ✅ |
| S3 | Etiquetas ya completas | No escribe | ✅ |
| S3 | Credenciales rechazadas o `NoSuchBucket` | Fallo permanente | ✅ |
| S3 | Relectura no confirma | Fallo transitorio | ✅ |

---

## Correspondencia con la matriz

| Prueba | Qué valida en esta evidencia |
|---|---|
| **F04** | Firma ausente, alterada o con timestamp vencido → rechazo |
| **F02** | Componente de reparación EC2, con cliente simulado |
| **F03** | Componente de reparación S3, con cliente simulado |
| **F13** | Fallo de SQS → `503` |

Cada fila se verifica con los tests listados en este informe. Las pruebas de
reparación usan clientes simulados y cubren la lógica de convergencia de los
módulos de I3.

---

## Datos de entrada
`bucket-01`). No se utilizaron credenciales reales ni servicios externos.

## Limitaciones

- Las pruebas usan clientes simulados; no levantan LocalStack.
- Este informe cubre únicamente pruebas unitarias de los módulos de I3.
- Warning no bloqueante: Starlette sugiere `httpx2` en lugar de `httpx` para
  `TestClient`.

## Reproducción

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest \
  tests/test_crypto.py \
  tests/test_receiver.py \
  tests/test_receiver_sqs.py \
  tests/test_remediation_ec2.py \
  tests/test_remediation_s3.py -q
```
