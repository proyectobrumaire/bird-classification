import os
os.environ.setdefault("TORCH_HOME", "/tmp")  # torch hub cache → /tmp (Lambda read-only FS)
import re
import boto3
from pathlib import Path
from datetime import datetime, timezone
from PIL import Image

# Pipeline existente — no se modifican esos archivos
from bird_detector import (
    load_detector,
    preprocess_image_for_detector,
    run_detector_on_image,
)
from utils_bounding_boxes_separation import prepare_crops_for_classifier
from predict_image_from_tensors import load_species_model, classify_crops_batch, annotate_full_image

import torch

s3          = boto3.client("s3")
dynamodb    = boto3.client("dynamodb")
BUCKET      = os.environ["BUCKET_NAME"]
STATION     = os.environ.get("STATION_NAME", "brumaire-1")
MODEL_KEY   = os.environ["MODEL_KEY"]
TABLE       = os.environ.get("DYNAMO_TABLE", "brumaire-telemetry")
MODEL_PATH  = "/tmp/model.pth"

TTL_SECS = 365 * 24 * 3600

# filename del ESP32: image_26-05-23T14-30-22_0.jpg
_TS_RE = re.compile(r"(\d{2})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})")

def _capture_ts(filename: str) -> datetime:
    """Extrae el timestamp de captura del RTC desde el nombre de archivo."""
    m = _TS_RE.search(filename)
    if not m:
        return datetime.now(timezone.utc)
    yy, mo, dd, hh, mi, ss = (int(x) for x in m.groups())
    return datetime(2000 + yy, mo, dd, hh, mi, ss, tzinfo=timezone.utc)


# Cargados una vez por instancia Lambda (warm start)
_detector_model = None
_coco_classes   = None
_species_model  = None
_class_names    = None


def _load_models():
    global _detector_model, _coco_classes, _species_model, _class_names

    device = "cuda" if torch.cuda.is_available() else "cpu"

    if _detector_model is None:
        _detector_model, _coco_classes = load_detector(device)

    if _species_model is None:
        if not os.path.exists(MODEL_PATH):
            s3.download_file(BUCKET, MODEL_KEY, MODEL_PATH)
        _species_model, _class_names = load_species_model(MODEL_PATH, device)

    return device


def handler(event, context):
    device = _load_models()

    record   = event["Records"][0]
    src_key  = record["s3"]["object"]["key"]  # images/raw/2026/05/23/image_26-05-23T14-30-22_0.jpg
    filename = Path(src_key).name

    print(f"PROCESSING key={src_key}")

    capture_ts = _capture_ts(filename)

    # Descargar imagen a /tmp
    local_img = f"/tmp/{filename}"
    s3.download_file(BUCKET, src_key, local_img)

    pil_img    = Image.open(local_img).convert("RGB")
    det_tensor = preprocess_image_for_detector(pil_img, device)

    detections = run_detector_on_image(
        _detector_model, _coco_classes, det_tensor,
        target_class="bird", conf_threshold=0.8,
    )

    crops = prepare_crops_for_classifier(pil_img, detections, padding_ratio=0.10)

    results = classify_crops_batch(
        _species_model, _class_names, crops, device, conf_threshold=0.7
    )

    # Guardar imagen anotada en processed/ con la misma jerarquía de fecha
    processed_key = src_key.replace("images/raw/", "images/processed/")
    stem          = Path(processed_key).stem
    parent        = str(Path(processed_key).parent)

    annotated  = annotate_full_image(pil_img, results)
    pred_local = f"/tmp/{stem}_pred.png"
    annotated.save(pred_local)
    s3.upload_file(pred_local, BUCKET, f"{parent}/{stem}_pred.png")

    kept = [r for r in results if r["keep"]]
    for r in kept:
        print(f"DETECTION key={src_key} species={r['pred_species']} conf={r['pred_conf']:.2f}")

    if kept:
        date_str  = capture_ts.strftime("%Y-%m-%d")
        image_key = f"{parent}/{stem}_pred.png"
        for r in kept:
            x1, y1, x2, y2 = r["padded_box"]
            dynamodb.put_item(TableName=TABLE, Item={
                "pk":             {"S": f"{STATION}#{date_str}"},
                "sk":             {"S": f"{capture_ts.isoformat()}#bird#{filename}"},
                "type":           {"S": "bird"},
                "filename":       {"S": filename},
                "species":        {"S": r["pred_species"]},
                "confidence":     {"N": f"{r['pred_conf']:.4f}"},
                "detector_score": {"N": f"{r['detector_score']:.4f}"},
                "image_key":      {"S": image_key},
                "x1": {"N": str(x1)}, "y1": {"N": str(y1)},
                "x2": {"N": str(x2)}, "y2": {"N": str(y2)},
                "expires_at":     {"N": str(int(capture_ts.timestamp()) + TTL_SECS)},
            })

    return {"statusCode": 200, "detections": len(kept)}
