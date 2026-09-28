# Brumaire — nube (bird-classification)

Estación Brumaire: condensador atmosférico que alimenta un bebedero para aves y fotografía a las aves. Un solo sistema en 3 repos:

- **microprocessors**: firmware Arduino Mega + ESP32-CAM.
- **mobile**: app Flutter que baja la SD del ESP32 y sube fotos/logs a S3.
- **bird-classification** (este): infraestructura AWS y modelo de clasificación.

Código, comentarios y commits en español.

## Arquitectura (Terraform en `terraform/`, us-east-1)

- **S3** (`brumaire-data`): `images/raw/YYYY/MM/DD/` (fotos del ESP32), `images/processed/...` (`*_pred.png` anotadas), `logs/app/...` (JSON de la app), `models/bird_species_resnet18.pth`. Retención 1 año.
- **Lambda `classifier`** (imagen Docker en ECR, `lambda_handler.py`): se dispara con `images/raw/*.jpg`. Faster R-CNN (COCO, clase "bird", umbral 0.8) → recorte con 10 % de margen, letterbox 224 → ResNet18 de especies (umbral 0.7). Guarda la imagen anotada, un ítem `bird` por detección y un ítem `photo` por foto (con o sin aves) en DynamoDB.
- **Lambda `log_processor`**: se dispara con `logs/app/*.json`. Un ítem `sensor` por timestamp con todas las claves `*_K` y la lista `events` (eventos de ese timestamp, sin duplicados; el ítem existe aunque solo haya eventos). Usa `update_item` (varios batches se combinan; reprocesar es idempotente). Descarta las líneas con timestamp inválido.
- **Lambda `presigner`**, detrás de API Gateway (`POST /`), autenticada con `x-api-key` (secret generado por Terraform, output sensible).
- **Lambda `gallery`** (`gallery.py`), misma API y mismo secret; despacha por `routeKey`:
  - `POST /gallery` `{from, to, species?}` o `{filename}` → detecciones agrupadas por especie.
  - `POST /photos` `{from, to}` → todas las fotos (con `detections`, `image_url` del `_pred.png` o null, `raw_url`).
  - `POST /events` `{from, to, types?}` → un elemento por (timestamp, evento).
  - Contrato: `~/Brumaire/.claude/contracts/api-fotos-eventos.md`. `env` = sensores del **timestamp exacto** (el firmware usa un único timestamp por evento: línea del evento, lecturas y fotos). `/photos` y `/events`: más reciente primero, máx. 500 con `truncated`; se consulta día a día desde el más reciente y se corta al terminar el día en que se supera el máximo. Query paginado (`LastEvaluatedKey`).
- **DynamoDB `brumaire-telemetry`**: `pk = brumaire-1#YYYY-MM-DD` (fecha UTC), `sk = <iso UTC>#sensor`, `<iso UTC>#photo#<filename>` o `<iso UTC>#bird#<filename>#<i>`. TTL 1 año (`expires_at`).
- `scripts/migrar_fotos_eventos.py`: migración idempotente (dry-run por defecto) que reprocesa `logs/app/` para poblar `events` y crea los ítems `photo` de las fotos existentes.
- `dl_main.py`, `drive_connection.py` y el `__main__` de `bird_detector.py` son el prototipo local anterior (Google Drive, rutas de Windows); no se despliegan.

## Tiempo

- El RTC de la estación guarda **hora local** (la app lo sincroniza con el teléfono). `RTC_UTC_OFFSET_H` (default **−5**, Colombia) se pasa a `classifier`, `log_processor` y `gallery`, que convierten a UTC.
- Los datos anteriores al 2026-09-24 quedaron con la hora corrida (sensores 7 h, detecciones 5 h); no se han migrado. Ojo: reprocesarlos con la migración crea ítems `sensor` nuevos en la hora correcta sin borrar los viejos.

## Despliegue

- `cd terraform && terraform plan && terraform apply` (estado remoto en S3, cuenta 773151223594). Revisar el plan antes de aplicar.
- Cambiar `lambda_handler.py`, los módulos del pipeline o el `Dockerfile` reconstruye y sube la imagen del clasificador (~20 min la primera vez).

## Tests

- `python3 -m unittest discover -s tests -v` (sin AWS: boto3, torch y el pipeline se simulan).

## Pendientes conocidos

- Faster R-CNN descarga sus pesos en cada arranque en frío (convendría incluirlos en la imagen).
- `/gallery` arma la clave del `_pred.png` con la fecha local del nombre de archivo (no con la carpeta real de subida), hace un `head_object` por detección y no tiene tope de elementos. `/photos` usa `image_key` del ítem `photo`.
- Migración `scripts/migrar_fotos_eventos.py` pendiente de ejecutar (primero en dry-run).
- Cada BIRD produce 3 fotos → hasta 3 detecciones por visita en la galería.
- Comparación del secret con `!=` (no en tiempo constante); queda el permiso `presigner_public` de la Function URL, que ya no se usa.
