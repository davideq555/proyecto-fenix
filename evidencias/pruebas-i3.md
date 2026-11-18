# Evidencia de pruebas unitarias — Integrante 3

- **Fecha:** 2026-11-17
- **Rama:** `feat/i3-webhook-repair`
- **Commit de pruebas:** `99255dd`
- **Responsable:** Integrante 3 (I3)
- **Alcance:** firma HMAC del webhook, receptor HTTP, adaptador de publicación
  en SQS y reparación convergente de EC2 y S3.

---

## Entorno de ejecución

| Componente | Versión |
|---|---|
| Python | 3.15.0 |
| pytest | 9.2.0 |
| boto3 | 1.44.5 |
| fastapi | 0.163.2 |
| httpx | 0.28.1 |

Las pruebas se ejecutaron en un entorno virtual con las dependencias
declaradas en `requirements.txt` y `pyproject.toml`.

## Comando ejecutado

```bash
python -m pytest \
  tests/test_crypto.py \
  tests/test_receiver.py \
  tests/test_receiver_sqs.py \
  tests/test_remediation_ec2.py \
  tests/test_remediation_s3.py -q
```

## Resultado global

```text
85 passed, 1 warning in 0.22s
```

| Archivo | Área | Casos | Resultado | Tiempo |
|---|---|---:|---|---:|
| `tests/test_crypto.py` | Firma HMAC del webhook | 26 | 26 passed | 0.02 s |
| `tests/test_receiver.py` | Receptor HTTP | 16 | 16 passed | 0.24 s |
| `tests/test_receiver_sqs.py` | Adaptador de publicación SQS | 11 | 11 passed | 0.15 s |
| `tests/test_remediation_ec2.py` | Reparación convergente EC2 | 18 | 18 passed | 0.02 s |
| `tests/test_remediation_s3.py` | Reparación de etiquetas S3 | 14 | 14 passed | 0.02 s |
| **Total** | | **85** | **85 passed** | **0.22 s** |

Salida por archivo:

```text
tests/test_crypto.py           26 passed in 0.02s
tests/test_receiver.py         16 passed, 1 warning in 0.24s
tests/test_receiver_sqs.py     11 passed, 1 warning in 0.15s
tests/test_remediation_ec2.py  18 passed in 0.02s
tests/test_remediation_s3.py   14 passed in 0.02s
```

---

## Cobertura

| Módulo | Sentencias | Sin cubrir | Cobertura |
|---|---:|---:|---:|
| `src/fenix/utils/crypto.py` | 34 | 0 | **100 %** |
| `src/fenix/services/receiver.py` | 128 | 13 | **90 %** |
| `src/fenix/services/remediation.py` | 157 | 20 | **87 %** |
| **Total** | **319** | **33** | **90 %** |

```text
Name                                Stmts   Miss  Cover
---------------------------------------------------------
src/fenix/services/receiver.py        128     13    90%
src/fenix/services/remediation.py     157     20    87%
src/fenix/utils/crypto.py              34      0   100%
---------------------------------------------------------
TOTAL                                 319     33    90%
```

Las líneas sin cubrir corresponden a defensas extremas (tipos inválidos,
respuestas malformadas, `BotoCoreError`) y al cableado de entorno que todavía
depende de módulos entregados o de otros roles.

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
| Receptor | JSON roto o UTF-8 inválido | `400` sin publicar | ✅ |
| Receptor | Lista JSON o contrato inválido | `422` sin publicar | ✅ |
| Receptor | SQS no disponible | `503` sin aceptación falsa | ✅ |
| Receptor | SQS confirma el mensaje | `202` con `event_id` y `message_id` | ✅ |
| SQS | Respuesta de Boto3 o `MessageId` como texto | Extrae el identificador | ✅ |
| SQS | Respuesta sin `MessageId` | Falla y deriva en `503` | ✅ |
| EC2 | Instancia `stopped` | `start` y verificación de `running` | ✅ |
| EC2 | Instancia ya `running` | No ejecuta `start` | ✅ |
| EC2 | Instancia `terminated` | Fallo permanente | ✅ |
| EC2 | Polling agotado o `Throttling` | Fallo transitorio | ✅ |
| EC2 | ID del evento distinto del inventario | Usa el ID del inventario | ✅ |
| S3 | Bucket sin etiquetas (`NoSuchTagSet`) | Restaura las esperadas | ✅ |
| S3 | Etiquetas ajenas presentes | Las preserva | ✅ |
| S3 | Clave en conflicto | Gana la esperada | ✅ |
| S3 | Etiquetas ya completas | No escribe | ✅ |
| S3 | Relectura no confirma | Fallo transitorio | ✅ |

---

## Correspondencia con la matriz

| Prueba | Cobertura en esta evidencia |
|---|---|
| **F04** | Firma ausente, alterada o con timestamp vencido → rechazo |
| **F05** | Mismos datos JSON con bytes distintos → rechazo |
| **F06** | JSON roto → `400`; lista JSON → `422` |
| **F02** | Reparación EC2 verificada a nivel unitario con cliente simulado |
| **F03** | Reparación S3 verificada a nivel unitario con cliente simulado |
| **F13** | Fallo de SQS → `503` |

La integración end-to-end de F02, F03 y F13 —worker, ledger, ACK y
notificación— corresponde a I5. Esta evidencia cubre la unidad de I3 y su
comportamiento aislado.

---

## Datos de entrada

Se usaron datos sintéticos de laboratorio (`evt-node-001`, `node-01`,
`bucket-01`). No se utilizaron credenciales reales, servicios externos ni
recursos de LocalStack.

## Limitaciones

- Las pruebas usan clientes simulados; no reemplazan la evidencia de las dos
  reparaciones por API sobre LocalStack.
- Esa evidencia por API queda pendiente de `settings.py` (apoyo entregado,
  revisión de I2) y del inventario generado por `setup-demo` (I2).
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
