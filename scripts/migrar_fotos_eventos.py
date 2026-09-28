#!/usr/bin/env python3
"""
Migración para el contrato de fotos y eventos (.claude/contracts/api-fotos-eventos.md).

Pasos (ambos idempotentes):
  logs   Reprocesa todos los JSON de logs/app/ con la lógica actual de
         log_processor (lecturas + lista `events` en los ítems `<iso>#sensor`).
  fotos  Crea un ítem `<iso>#photo#<filename>` por cada foto de images/raw/ que
         aún no lo tenga (n_detections = ítems bird de ese filename; image_key
         solo si existe el _pred.png).

Por defecto es --dry-run: solo lee y reporta qué haría. Para escribir: --apply.

Uso:
  AWS_PROFILE=brumaire python3 scripts/migrar_fotos_eventos.py [--paso logs|fotos|todo] [--apply]

Requiere boto3. RTC_UTC_OFFSET_H debe coincidir con el de Terraform (default −5).
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("RTC_UTC_OFFSET_H", "-5")
os.environ.setdefault("STATION_NAME", "brumaire-1")
os.environ.setdefault("DYNAMO_TABLE", "brumaire-telemetry")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import log_processor  # noqa: E402  (usa las mismas variables de entorno)

s3       = log_processor.s3
dynamodb = log_processor.dynamodb
STATION  = log_processor.STATION
TABLE    = log_processor.TABLE
TTL_SECS = log_processor.TTL_SECS
RTC_UTC_OFFSET_H = log_processor.RTC_UTC_OFFSET_H

LOGS_PREFIX      = "logs/app/"
RAW_PREFIX       = "images/raw/"
PROCESSED_PREFIX = "images/processed/"


def _list_keys(bucket, prefix, suffix):
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(suffix):
                yield obj["Key"]


# ── Paso logs ─────────────────────────────────────────────────────────────

def migrar_logs(bucket, apply):
    n_files = n_ts = n_new_items = n_new_events = 0

    for key in _list_keys(bucket, LOGS_PREFIX, ".json"):
        body    = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        entries = json.loads(body).get("entries", [])
        groups  = log_processor.group_entries(entries)
        n_files += 1
        n_ts    += len(groups)

        # Qué cambiaría (lectura): ítems nuevos y eventos que faltan
        file_new_items = file_new_events = 0
        for ts_iso, g in groups.items():
            resp = dynamodb.get_item(
                TableName=TABLE,
                Key=log_processor._item_key(ts_iso),
                ProjectionExpression="#ev",
                ExpressionAttributeNames={"#ev": "events"},
            )
            item = resp.get("Item")
            if item is None:
                file_new_items += 1
                have = set()
            else:
                have = {e.get("S") for e in item.get("events", {}).get("L", [])}
            file_new_events += sum(1 for ev in g["events"] if ev not in have)

        n_new_items  += file_new_items
        n_new_events += file_new_events
        print(f"[logs] {key}: timestamps={len(groups)} "
              f"items_nuevos={file_new_items} eventos_nuevos={file_new_events}")

        if apply:
            log_processor.write_groups(groups)

    print(f"[logs] archivos={n_files} timestamps={n_ts} "
          f"items_nuevos={n_new_items} eventos_nuevos={n_new_events} "
          f"{'(escrito)' if apply else '(dry-run, nada escrito)'}")


# ── Paso fotos ────────────────────────────────────────────────────────────

_TS_RE = re.compile(r"(\d{2})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})")

def capture_ts(filename):
    """Igual que lambda_handler._capture_ts, pero None si el nombre no trae fecha."""
    m = _TS_RE.search(filename)
    if not m:
        return None
    yy, mo, dd, hh, mi, ss = (int(x) for x in m.groups())
    tz_local = timezone(timedelta(hours=RTC_UTC_OFFSET_H))
    return datetime(2000 + yy, mo, dd, hh, mi, ss, tzinfo=tz_local).astimezone(timezone.utc)


def pred_key(raw_key):
    """Clave del _pred.png que genera el clasificador para una foto raw."""
    processed = raw_key.replace(RAW_PREFIX, PROCESSED_PREFIX)
    p = Path(processed)
    return f"{p.parent}/{p.stem}_pred.png"


def photo_item(ts, filename, raw_key, image_key, n_detections):
    """Mismo formato que lambda_handler._photo_item (image_key opcional)."""
    item = {
        "pk":           {"S": f"{STATION}#{ts.strftime('%Y-%m-%d')}"},
        "sk":           {"S": f"{ts.isoformat()}#photo#{filename}"},
        "type":         {"S": "photo"},
        "filename":     {"S": filename},
        "raw_key":      {"S": raw_key},
        "n_detections": {"N": str(n_detections)},
        "expires_at":   {"N": str(int(ts.timestamp()) + TTL_SECS)},
    }
    if image_key:
        item["image_key"] = {"S": image_key}
    return item


class BirdCounter:
    """
    Cuenta ítems bird por filename. Busca en el día del timestamp y en los
    vecinos, porque los datos anteriores al 2026-09-24 tienen la hora corrida.
    """

    def __init__(self):
        self._cache = {}   # pk → {filename: n}

    def _day(self, day):
        pk = f"{STATION}#{day.isoformat()}"
        if pk not in self._cache:
            counts = {}
            params = {
                "TableName": TABLE,
                "KeyConditionExpression": "pk = :pk",
                "FilterExpression": "#t = :bird",
                "ExpressionAttributeNames": {"#t": "type"},
                "ExpressionAttributeValues": {":pk": {"S": pk}, ":bird": {"S": "bird"}},
                "ProjectionExpression": "filename",
            }
            while True:
                resp = dynamodb.query(**params)
                for it in resp.get("Items", []):
                    f = it.get("filename", {}).get("S")
                    counts[f] = counts.get(f, 0) + 1
                if not resp.get("LastEvaluatedKey"):
                    break
                params["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
            self._cache[pk] = counts
        return self._cache[pk]

    def count(self, filename, ts):
        d = ts.date()
        return sum(self._day(d + timedelta(days=k)).get(filename, 0) for k in (-1, 0, 1))


def migrar_fotos(bucket, apply):
    preds   = set(_list_keys(bucket, PROCESSED_PREFIX, "_pred.png"))
    birds   = BirdCounter()
    n_total = n_exist = n_new = n_sin_fecha = n_sin_pred = 0

    for raw_key in _list_keys(bucket, RAW_PREFIX, ".jpg"):
        n_total += 1
        filename = Path(raw_key).name
        ts = capture_ts(filename)
        if ts is None:
            n_sin_fecha += 1
            print(f"[fotos] {raw_key}: sin fecha en el nombre, se omite")
            continue

        image_key = pred_key(raw_key)
        if image_key not in preds:
            image_key = None
            n_sin_pred += 1

        item = photo_item(ts, filename, raw_key, image_key, birds.count(filename, ts))

        exists = "Item" in dynamodb.get_item(
            TableName=TABLE,
            Key={"pk": item["pk"], "sk": item["sk"]},
            ProjectionExpression="sk",
        )
        if exists:
            n_exist += 1
            continue

        n_new += 1
        print(f"[fotos] {raw_key}: crear {item['sk']['S']} "
              f"n_detections={item['n_detections']['N']} image_key={image_key}")
        if apply:
            try:
                # No sobrescribe un ítem que el clasificador haya escrito entretanto
                dynamodb.put_item(
                    TableName=TABLE, Item=item,
                    ConditionExpression="attribute_not_exists(sk)",
                )
            except dynamodb.exceptions.ConditionalCheckFailedException:
                pass

    print(f"[fotos] fotos={n_total} ya_existian={n_exist} a_crear={n_new} "
          f"sin_pred={n_sin_pred} sin_fecha={n_sin_fecha} "
          f"{'(escrito)' if apply else '(dry-run, nada escrito)'}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--paso", choices=["logs", "fotos", "todo"], default="todo")
    ap.add_argument("--bucket", default=os.environ.get("BUCKET_NAME", "brumaire-data"))
    ap.add_argument("--apply", action="store_true", help="escribe en DynamoDB (sin esto: dry-run)")
    args = ap.parse_args()

    print(f"tabla={TABLE} bucket={args.bucket} RTC_UTC_OFFSET_H={RTC_UTC_OFFSET_H} "
          f"modo={'APPLY' if args.apply else 'dry-run'}")
    if args.paso in ("logs", "todo"):
        migrar_logs(args.bucket, args.apply)
    if args.paso in ("fotos", "todo"):
        migrar_fotos(args.bucket, args.apply)


if __name__ == "__main__":
    main()
