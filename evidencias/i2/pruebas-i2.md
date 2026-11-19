# Evidencias de pruebas - Integrante 2

**Proyecto:** Fénix  
**Responsable:** Integrante 2  
**Grupo:** grupo-01  
**Entorno:** LocalStack  
**Fecha de ejecución:** 19/11/2026

## 1. Preparación de colas SQS

Se ejecutó `ensure_queues()` dos veces.

Resultado observado:
- Cola principal: `fenix-events`
- DLQ: `fenix-events-dlq`
- Las dos ejecuciones devolvieron las mismas URLs.
- Se comprobaron las dos colas y no se detectaron duplicados.
- La cola principal tiene VisibilityTimeout de 45 segundos.
- ReceiveMessageWaitTimeSeconds: 10.
- RedrivePolicy apunta a la DLQ.
- maxReceiveCount: 3.

ARN de la cola principal:
`arn:aws:sqs:us-east-1:000000000000:fenix-events`

ARN de la DLQ:
`arn:aws:sqs:us-east-1:000000000000:fenix-events-dlq`

## 2. Prueba de retry_later

Se probó con un cliente SQS simulado.

Resultados:
- receive_count 1: demora de 2 segundos.
- receive_count 3: demora de 8 segundos.
- receive_count 4: demora de 16 segundos.
- receive_count 5 y 10: demora máxima de 30 segundos.
- Valores inválidos (0, -1, True, 1.5 y "3"): se rechazaron con ValueError.

## 3. Preparación de recursos de demostración

Se ejecutó `setup_demo()` dos veces en LocalStack.

Primera y segunda ejecución:
- Bucket S3: `fenix-grupo-01-bucket-01`
- Instancia EC2: `i-d899fe2aec4730ca3`
- Grupo de seguridad: `sg-dd9317dff82b9b845`
- Estado de instancia: `running`

Los identificadores coincidieron en ambas ejecuciones. No se detectaron recursos duplicados.

## 4. Inventario

Se comprobó el archivo `data/inventory.json`.

Se encontraron:
- Registro `bucket-01`, con el nombre del bucket y sus etiquetas esperadas.
- Registro `node-01`, con el ID de instancia, ID del grupo de seguridad, ClientToken y etiquetas esperadas.

## 5. Etiquetas de recursos

Se consultaron las etiquetas directamente en LocalStack para el bucket S3, la instancia EC2 y el grupo de seguridad.

Los tres recursos presentaron estas etiquetas:

- ManagedBy: ppa2
- Project: fenix
- GroupId: grupo-01
- Owner: grupo-01

## 6. Lectura de mensaje de DLQ

Se envió un mensaje de prueba directamente a la DLQ y se leyó mediante `receive_dlq_message()`.

MessageId:
`a8d27875-3d7a-4f12-b8f4-e69fe6805850`

El mensaje y su ReceiptHandle fueron recibidos correctamente.

**Alcance de esta prueba:** el mensaje se envió directamente a la DLQ. Esta comprobación demuestra que se puede leer un mensaje de la DLQ, pero no demuestra el recorrido automático desde la cola principal mediante RedrivePolicy.

## 7. Pruebas automatizadas

Se ejecutó la suite de pruebas con pytest.

Resultado registrado:
**224 passed, 1 warning.**

La advertencia corresponde a una deprecación de Starlette relacionada con httpx; no provocó fallos en las pruebas.

## 8. Evidencias adjuntas

- 01-colas-sqs.png
- 02-configuracion-sqs.png
- 03-setup-demo.png
- 04-inventario.png
- 05-etiquetas-recursos.png
- 06-pytest.png
- 07-lectura-dlq.png

Todas las capturas se encuentran en evidencias/i2.
