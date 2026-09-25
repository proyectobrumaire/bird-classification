# Brumaire — nube (bird-classification)

Estación Brumaire: condensador atmosférico que alimenta un bebedero para aves y fotografía a las aves. Un solo sistema en 3 repos:

- **microprocessors**: firmware Arduino Mega + ESP32-CAM.
- **mobile**: app Flutter que baja la SD del ESP32 y sube fotos/logs a S3.
- **bird-classification** (este): infraestructura AWS y modelo de clasificación.

Código, comentarios y commits en español.

## Arquitectura (Terraform en `terraform/`, us-east-1)

- **S3** (`brumaire-data`): `images/raw/YYYY/MM/DD/` (fotos del ESP32), `images/processed/...` (`*_pred.png` anotadas), `logs/app/...` (JSON de la app), `models/bird_species_resnet18.pth`. Retención 1 año.
- **Lambda `classifier`** (imagen Docker en ECR, `lambda_handler.py`): se dispara con `images/raw/*.jpg`. Faster R-CNN (COCO, clase "bird", umbral 0.8) → recorte con 10 % de margen, letterbox 224 → ResNet18 de especies (umbral 0.7). Guarda la imagen anotada y un ítem `bird` por detección en DynamoDB.
- **Lambda `log_processor`**: se dispara con `logs/app/*.json`. Guarda un ítem `sensor` por timestamp (todas las claves `*_K`). Descarta los eventos y las líneas con timestamp inválido.
- **Lambda `presigner`** y **`gallery`**, detrás de API Gateway (`POST /` y `POST /gallery`), autenticadas con `x-api-key` (secret generado por Terraform, output sensible).
- **DynamoDB `brumaire-telemetry`**: `pk = brumaire-1#YYYY-MM-DD`, `sk = <iso UTC>#sensor` o `<iso UTC>#bird#<filename>#<i>`. TTL 1 año (`expires_at`).
- `dl_main.py`, `drive_connection.py` y el `__main__` de `bird_detector.py` son el prototipo local anterior (Google Drive, rutas de Windows); no se despliegan.

## Tiempo

- El RTC de la estación guarda **hora local** (la app lo sincroniza con el teléfono). `RTC_UTC_OFFSET_H` (default **−5**, Colombia) se pasa a `classifier`, `log_processor` y `gallery`, que convierten a UTC.
- Los datos anteriores al 2026-09-24 quedaron con la hora corrida (sensores 7 h, detecciones 5 h); no se han migrado.

## Despliegue

- `cd terraform && terraform plan && terraform apply` (estado remoto en S3, cuenta 773151223594). Revisar el plan antes de aplicar.
- Cambiar `lambda_handler.py`, los módulos del pipeline o el `Dockerfile` reconstruye y sube la imagen del clasificador (~20 min la primera vez).

## Pendientes conocidos

- Faster R-CNN descarga sus pesos en cada arranque en frío (convendría incluirlos en la imagen).
- `gallery` no pagina la consulta a DynamoDB (límite de 1 MB).
- Cada BIRD produce 3 fotos → hasta 3 detecciones por visita en la galería.
- Comparación del secret con `!=` (no en tiempo constante); queda el permiso `presigner_public` de la Function URL, que ya no se usa.
