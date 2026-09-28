"""
API de consulta (detrás de API Gateway HTTP API, payload v2), autenticada con x-api-key.

Rutas (una sola Lambda, se despacha por routeKey / rawPath):
- POST /gallery  detecciones de aves agrupadas por especie
- POST /photos   todas las fotos subidas (con o sin aves)
- POST /events   eventos del log de la estación

Los sensores (`env`) de una foto o evento son los del mismo timestamp exacto:
el firmware usa un único timestamp por evento. Contrato: .claude/contracts/api-fotos-eventos.md
"""
import os, json, re, base64, boto3
from datetime import datetime, timezone, timedelta

s3         = boto3.client("s3")
dynamodb   = boto3.client("dynamodb")
BUCKET     = os.environ["BUCKET_NAME"]
SECRET     = os.environ["API_SECRET"]
STATION    = os.environ.get("STATION_NAME", "brumaire-1")
TABLE      = os.environ.get("DYNAMO_TABLE", "brumaire-telemetry")
PRESIGN_TTL = 3600
RTC_UTC_OFFSET_H = int(os.environ.get("RTC_UTC_OFFSET_H", "0"))

MAX_ITEMS = 500   # máximo de elementos por respuesta en /photos y /events


def handler(event, context):
    auth = (event.get("headers") or {}).get("x-api-key", "")
    if auth != SECRET:
        return _resp(403, {"error": "Forbidden"})

    route = ROUTES.get(_route_path(event))
    if route is None:
        return _resp(404, {"error": "Not Found"})

    try:
        body = _parse_body(event)
        return route(body)
    except (KeyError, ValueError, TypeError) as e:
        return _resp(400, {"error": str(e)})


def _route_path(event) -> str:
    """'/photos' a partir de routeKey ('POST /photos') o rawPath. Sin ninguno → /gallery."""
    route_key = event.get("routeKey") or ""
    if " " in route_key:
        return route_key.split(" ", 1)[1]
    return event.get("rawPath") or "/gallery"


def _parse_body(event) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise ValueError("el body debe ser un objeto JSON")
    return body


# ── POST /gallery ─────────────────────────────────────────────────────────

def gallery(body):
    filename_f = body.get("filename")
    species_f  = body.get("species")

    if filename_f:
        cap_ts  = _ts_from_filename(filename_f)
        ts_from = cap_ts - timedelta(minutes=1)
        ts_to   = cap_ts + timedelta(minutes=1)
    else:
        ts_from, ts_to = _parse_range(body)

    items = []
    for day_items in _query_days(ts_from, ts_to):
        items.extend(day_items)

    sensors   = _sensors_by_ts(items)
    bird_rows = [
        it for it in items
        if _kind(it) == "bird"
        and not (species_f and it.get("species", {}).get("S") != species_f)
    ]

    if not bird_rows:
        return _resp(200, {})

    # Construir respuesta agrupada por especie
    by_species = {}
    for item in bird_rows:
        fname   = item["filename"]["S"]
        species = item["species"]["S"]
        ts_iso  = _sk_ts(item["sk"]["S"])

        proc_key = _processed_key(fname)
        img_url  = _presign(proc_key) if _object_exists(proc_key) else None

        entry = {
            "filename":        fname,
            "species":         species,
            "confidence":      float(item["confidence"]["N"]),
            "detector_score":  float(item["detector_score"]["N"]),
            "timestamp":       ts_iso,
            "image_url":       img_url,
            "env":             sensors.get(ts_iso, {}),
        }
        by_species.setdefault(species, []).append(entry)

    return _resp(200, by_species)


# ── POST /photos ──────────────────────────────────────────────────────────

def photos(body):
    ts_from, ts_to = _parse_range(body)

    items = _collect_newest_first(
        ts_from, ts_to,
        count=lambda its: sum(1 for it in its if _kind(it) == "photo"),
    )
    result, truncated = build_photos(items, _presign)
    return _resp(200, {"photos": result, "truncated": truncated})


def build_photos(items, presign, max_items=None):
    """
    Arma la lista de /photos a partir de los ítems de DynamoDB (photo, bird, sensor).
    Más reciente primero; dentro del mismo timestamp, por nombre de archivo.
    Devuelve (fotos, truncated).
    """
    max_items = max_items or MAX_ITEMS
    sensors  = _sensors_by_ts(items)
    birds    = {}   # filename → [(sk, item)]
    photo_it = []

    for it in items:
        kind = _kind(it)
        if kind == "photo":
            photo_it.append(it)
        elif kind == "bird":
            birds.setdefault(it["filename"]["S"], []).append((it["sk"]["S"], it))

    photo_it.sort(key=lambda it: it["filename"]["S"])
    photo_it.sort(key=lambda it: _sk_ts(it["sk"]["S"]), reverse=True)
    truncated = len(photo_it) > max_items

    result = []
    for it in photo_it[:max_items]:
        fname  = it["filename"]["S"]
        ts_iso = _sk_ts(it["sk"]["S"])
        image_key = it.get("image_key", {}).get("S")
        detections = [
            {
                "species":        b["species"]["S"],
                "confidence":     float(b["confidence"]["N"]),
                "detector_score": float(b["detector_score"]["N"]),
            }
            for _, b in sorted(birds.get(fname, []), key=lambda p: p[0])
        ]
        result.append({
            "filename":   fname,
            "timestamp":  ts_iso,
            "image_url":  presign(image_key) if image_key else None,
            "raw_url":    presign(it["raw_key"]["S"]),
            "detections": detections,
            "env":        sensors.get(ts_iso, {}),
        })
    return result, truncated


# ── POST /events ──────────────────────────────────────────────────────────

def events(body):
    ts_from, ts_to = _parse_range(body)
    types = body.get("types")
    if types is not None:
        if not isinstance(types, list) or not all(isinstance(t, str) for t in types):
            raise ValueError("types debe ser una lista de strings")
        types = set(types) or None   # lista vacía = todos los tipos

    items = _collect_newest_first(
        ts_from, ts_to,
        count=lambda its: len(_event_rows(its, types)),
        filter_expr=("attribute_exists(#ev)", {"#ev": "events"}),
    )
    result, truncated = build_events(items, types)
    return _resp(200, {"events": result, "truncated": truncated})


def build_events(items, types=None, max_items=None):
    """Un elemento por (timestamp, evento), más reciente primero. Devuelve (eventos, truncated)."""
    max_items = max_items or MAX_ITEMS
    rows = _event_rows(items, types)
    rows.sort(key=lambda r: r["timestamp"], reverse=True)   # estable: conserva el orden del log
    return rows[:max_items], len(rows) > max_items


def _event_rows(items, types):
    rows = []
    for it in sorted(items, key=lambda it: it["sk"]["S"]):
        if _kind(it) != "sensor":
            continue
        ts_iso = _sk_ts(it["sk"]["S"])
        env    = _readings(it)
        for ev in it.get("events", {}).get("L", []):
            name = ev.get("S")
            if name is None or (types and name not in types):
                continue
            rows.append({"timestamp": ts_iso, "event": name, "env": env})
    return rows


# ── DynamoDB helpers ──────────────────────────────────────────────────────

def _collect_newest_first(ts_from, ts_to, count, filter_expr=None):
    """
    Lee día por día desde el más reciente y se detiene al terminar un día en el
    que ya hay más de MAX_ITEMS elementos (así se sabe que hay que truncar sin
    leer todo el rango). Se completa el día entero para no dejar fotos sin sus
    detecciones o sensores del mismo timestamp.
    """
    items = []
    for day_items in _query_days(ts_from, ts_to, newest_first=True, filter_expr=filter_expr):
        items.extend(day_items)
        if count(items) > MAX_ITEMS:
            break
    return items


def _query_days(ts_from: datetime, ts_to: datetime, newest_first=False, filter_expr=None):
    """Genera, por cada día (pk) del rango, la lista de ítems con sk en el rango."""
    days = []
    cur, end = ts_from.date(), ts_to.date()
    while cur <= end:
        days.append(cur)
        cur += timedelta(days=1)
    if newest_first:
        days.reverse()

    for day in days:
        pk      = f"{STATION}#{day.isoformat()}"
        sk_from = ts_from.isoformat() if day == ts_from.date() else f"{day.isoformat()}T00:00:00+00:00"
        sk_to   = ts_to.isoformat()   if day == ts_to.date()   else f"{day.isoformat()}T23:59:59+00:00"
        yield _query_pk(pk, sk_from, sk_to + "~", filter_expr)


def _query_pk(pk, sk_from, sk_to, filter_expr=None):
    """
    Query paginado (LastEvaluatedKey) de un pk entre dos sk.
    filter_expr: None o (FilterExpression, ExpressionAttributeNames).
    """
    params = {
        "TableName": TABLE,
        "KeyConditionExpression": "pk = :pk AND sk BETWEEN :from AND :to",
        "ExpressionAttributeValues": {
            ":pk":   {"S": pk},
            ":from": {"S": sk_from},
            ":to":   {"S": sk_to},
        },
    }
    if filter_expr:
        params["FilterExpression"], params["ExpressionAttributeNames"] = filter_expr

    items = []
    while True:
        resp = dynamodb.query(**params)
        items.extend(resp.get("Items", []))
        last = resp.get("LastEvaluatedKey")
        if not last:
            return items
        params["ExclusiveStartKey"] = last


def _kind(item) -> str:
    return item.get("type", {}).get("S", "")


def _sk_ts(sk: str) -> str:
    """ISO UTC del sk '{iso_ts}#tipo...' (tal cual, para comparar timestamps exactos)."""
    return sk.split("#", 1)[0]


def _readings(item) -> dict:
    """Todas las claves de sensor del ítem (atributos numéricos *_K)."""
    return {
        k: float(v["N"])
        for k, v in item.items()
        if k.endswith("_K") and "N" in v
    }


def _sensors_by_ts(items) -> dict:
    """{iso UTC: {clave_K: valor}} de los ítems sensor."""
    return {
        _sk_ts(it["sk"]["S"]): _readings(it)
        for it in items
        if _kind(it) == "sensor"
    }


# ── helpers ───────────────────────────────────────────────────────────────

def _parse_range(body):
    ts_from = _parse_iso(body["from"])
    ts_to   = _parse_iso(body["to"])
    if ts_from > ts_to:
        raise ValueError("from debe ser anterior o igual a to")
    return ts_from, ts_to


def _parse_iso(value) -> datetime:
    """ISO 8601 (acepta 'Z'; sin offset se asume UTC) → datetime en UTC."""
    if not isinstance(value, str):
        raise ValueError(f"timestamp inválido: {value!r}")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


_TS_RE = re.compile(r"(\d{2})-(\d{2})-(\d{2})T(\d{2})-(\d{2})-(\d{2})")

def _ts_from_filename(filename: str) -> datetime:
    m = _TS_RE.search(filename)
    if not m:
        raise ValueError(f"No timestamp in filename: {filename}")
    yy, mo, dd, hh, mi, ss = (int(x) for x in m.groups())
    tz_local = timezone(timedelta(hours=RTC_UTC_OFFSET_H))
    return datetime(2000 + yy, mo, dd, hh, mi, ss, tzinfo=tz_local).astimezone(timezone.utc)


def _processed_key(filename: str) -> str:
    m = re.search(r"(\d{2})-(\d{2})-(\d{2})T", filename)
    if m:
        yy, mo, dd = m.groups()
        stem = filename.rsplit(".", 1)[0]
        return f"images/processed/20{yy}/{mo}/{dd}/{stem}_pred.png"
    return f"images/processed/{filename}"


def _presign(key: str) -> str:
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": BUCKET, "Key": key},
        ExpiresIn=PRESIGN_TTL,
    )


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


ROUTES = {
    "/gallery": gallery,
    "/photos":  photos,
    "/events":  events,
}
