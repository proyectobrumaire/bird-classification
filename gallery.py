import os, csv, json, re, boto3
from datetime import datetime, timezone, timedelta, date as date_type
from io import StringIO

s3         = boto3.client("s3")
dynamodb   = boto3.client("dynamodb")
BUCKET     = os.environ["BUCKET_NAME"]
CSV_KEY    = os.environ["CSV_KEY"]
SECRET     = os.environ["API_SECRET"]
STATION    = os.environ.get("STATION_NAME", "brumaire-1")
TABLE      = os.environ.get("DYNAMO_TABLE", "brumaire-telemetry")
PRESIGN_TTL = 3600


def handler(event, context):
    auth = (event.get("headers") or {}).get("x-api-key", "")
    if auth != SECRET:
        return _resp(403, {"error": "Forbidden"})

    try:
        body       = json.loads(event.get("body") or "{}")
        filename_f = body.get("filename")
        species_f  = body.get("species")

        if filename_f:
            cap_ts  = _ts_from_filename(filename_f)
            ts_from = cap_ts - timedelta(minutes=1)
            ts_to   = cap_ts + timedelta(minutes=1)
        else:
            ts_from = datetime.fromisoformat(body["from"].replace("Z", "+00:00"))
            ts_to   = datetime.fromisoformat(body["to"].replace("Z", "+00:00"))
    except (KeyError, ValueError) as e:
        return _resp(400, {"error": str(e)})

    # Traer todos los items del rango desde DynamoDB
    items = _query_range(ts_from, ts_to)

    # Separar sensores y detecciones
    sensor_ts = []   # lista de (datetime, {key: float})
    bird_rows = []   # lista de dicts de detección

    for item in items:
        sk   = item["sk"]["S"]
        kind = item.get("type", {}).get("S", "")

        if kind == "sensor":
            ts = _parse_sk_ts(sk)
            readings = {
                k: float(item[k]["N"])
                for k in ("T1_K", "H1_K", "P1_K", "P2_K", "W1_K", "H2_K")
                if k in item
            }
            sensor_ts.append((ts, readings))

        elif kind == "bird":
            if species_f and item.get("species", {}).get("S") != species_f:
                continue
            bird_rows.append(item)

    if not bird_rows:
        return _resp(200, {})

    # Construir respuesta agrupada por especie
    by_species = {}
    for item in bird_rows:
        fname   = item["filename"]["S"]
        species = item["species"]["S"]
        cap_ts  = _parse_sk_ts(item["sk"]["S"])
        env     = _nearest_sensor(sensor_ts, cap_ts)

        proc_key = _processed_key(fname)
        img_url  = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET, "Key": proc_key},
            ExpiresIn=PRESIGN_TTL,
        ) if _object_exists(proc_key) else None

        entry = {
            "filename":        fname,
            "species":         species,
            "confidence":      float(item["confidence"]["N"]),
            "detector_score":  float(item["detector_score"]["N"]),
            "timestamp":       cap_ts.isoformat(),
            "image_url":       img_url,
            "env":             env,
        }
        by_species.setdefault(species, []).append(entry)

    return _resp(200, by_species)


# ── DynamoDB helpers ──────────────────────────────────────────────────────

def _query_range(ts_from: datetime, ts_to: datetime):
    """Queries all items across all dates in the range."""
    items = []
    cur   = ts_from.date()
    end   = ts_to.date()

    while cur <= end:
        pk       = f"{STATION}#{cur.isoformat()}"
        sk_from  = ts_from.isoformat() if cur == ts_from.date() else f"{cur.isoformat()}T00:00:00+00:00"
        sk_to    = ts_to.isoformat()   if cur == ts_to.date()   else f"{cur.isoformat()}T23:59:59+00:00"

        resp = dynamodb.query(
            TableName=TABLE,
            KeyConditionExpression="pk = :pk AND sk BETWEEN :from AND :to",
            ExpressionAttributeValues={
                ":pk":   {"S": pk},
                ":from": {"S": sk_from},
                ":to":   {"S": sk_to + "~"},
            },
        )
        items.extend(resp.get("Items", []))

        # Ampliar ventana de sensores: también traer ±35 min fuera del rango del día
        # para interpolación (solo en primer y último día)
        cur += timedelta(days=1)

    return items


def _parse_sk_ts(sk: str) -> datetime:
    """Extrae datetime del sk format '{iso_ts}#tipo'."""
    ts_part = sk.split("#")[0]
    return datetime.fromisoformat(ts_part)


def _nearest_sensor(sensor_ts, cap_ts: datetime, max_delta_secs=600):
    """Devuelve las lecturas del sensor más cercano en el tiempo (máx 10 min)."""
    if not sensor_ts:
        return {}
    closest_ts, closest_readings = min(
        sensor_ts,
        key=lambda pair: abs((pair[0] - cap_ts).total_seconds())
    )
    if abs((closest_ts - cap_ts).total_seconds()) > max_delta_secs:
        return {}
    return closest_readings


# ── helpers ───────────────────────────────────────────────────────────────

_TS_RE = re.compile(r"(\d{2})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})")

def _ts_from_filename(filename: str) -> datetime:
    m = _TS_RE.search(filename)
    if not m:
        raise ValueError(f"No timestamp in filename: {filename}")
    yy, mo, dd, hh, mi, ss = (int(x) for x in m.groups())
    return datetime(2000 + yy, mo, dd, hh, mi, ss, tzinfo=timezone.utc)


def _processed_key(filename: str) -> str:
    m = re.search(r"(\d{2})-(\d{2})-(\d{2})T", filename)
    if m:
        yy, mo, dd = m.groups()
        stem = filename.rsplit(".", 1)[0]
        return f"images/processed/20{yy}/{mo}/{dd}/{stem}_pred.png"
    return f"images/processed/{filename}"


def _object_exists(key: str) -> bool:
    try:
        s3.head_object(Bucket=BUCKET, Key=key)
        return True
    except Exception:
        return False


def _resp(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=str),
    }
