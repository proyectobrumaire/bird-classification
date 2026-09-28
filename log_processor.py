import json
import os
import boto3
from datetime import datetime, timezone, timedelta

dynamodb   = boto3.client("dynamodb")
s3         = boto3.client("s3")

STATION     = os.environ.get("STATION_NAME", "brumaire-1")
TABLE       = os.environ.get("DYNAMO_TABLE", "brumaire-telemetry")

# TTL: 1 año en segundos
TTL_SECS = 365 * 24 * 3600

RTC_UTC_OFFSET_H = int(os.environ.get("RTC_UTC_OFFSET_H", "0"))


def handler(event, context):
    record = event["Records"][0]
    bucket = record["s3"]["bucket"]["name"]
    key    = record["s3"]["object"]["key"]

    obj     = s3.get_object(Bucket=bucket, Key=key)
    entries = json.loads(obj["Body"].read()).get("entries", [])

    groups = group_entries(entries)
    write_groups(groups)

    n_events = sum(len(g["events"]) for g in groups.values())
    print(f"SENSOR_GROUPS key={key} sensor_groups={len(groups)} events={n_events}")
    return {"statusCode": 200, "sensor_groups": len(groups), "events": n_events}


def group_entries(entries):
    """
    Agrupa las entradas del JSON de la app por timestamp (UTC).

    El firmware usa un único timestamp por evento: la línea del evento y sus
    lecturas comparten exactamente el mismo timestamp.

    Devuelve {ts_iso_utc: {"sensors": {clave_K: "valor"}, "events": [str, ...]}}
    (events sin duplicados, en orden de aparición).
    """
    groups = {}

    for entry in entries:
        if not entry.get("timestamp_valid", True):
            continue
        etype = entry.get("type")
        if etype not in ("sensorData", "event"):
            continue

        ts = _parse_ts(entry.get("timestamp"))
        if ts is None:
            print(f"TIMESTAMP_INVALIDO entry={entry}")
            continue

        g = groups.setdefault(ts.isoformat(), {"sensors": {}, "events": []})

        if etype == "sensorData":
            skey = entry.get("sensor_key")
            sval = entry.get("sensor_value")
            if skey and sval is not None:
                g["sensors"][skey] = str(sval)
        else:
            ev = entry.get("event")
            if ev and ev not in g["events"]:
                g["events"].append(str(ev))

    # Timestamps sin lecturas ni eventos útiles no generan ítem
    return {ts: g for ts, g in groups.items() if g["sensors"] or g["events"]}


def write_groups(groups):
    """
    Escribe un ítem `<iso UTC>#sensor` por timestamp.

    Usa update_item (no put_item) para no perder datos si varios batches aportan
    al mismo timestamp, y agrega cada evento a la lista `events` solo si no está
    (condición) → reprocesar el mismo JSON deja el ítem igual.
    """
    for ts_iso, g in groups.items():
        key = _item_key(ts_iso)
        ts_dt = datetime.fromisoformat(ts_iso)

        names  = {"#type": "type"}
        values = {
            ":type": {"S": "sensor"},
            ":exp":  {"N": str(int(ts_dt.timestamp()) + TTL_SECS)},
        }
        sets = ["#type = :type", "expires_at = :exp"]
        for i, (k, v) in enumerate(sorted(g["sensors"].items())):
            names[f"#s{i}"]  = k
            values[f":s{i}"] = {"N": v}
            sets.append(f"#s{i} = :s{i}")

        dynamodb.update_item(
            TableName=TABLE,
            Key=key,
            UpdateExpression="SET " + ", ".join(sets),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
        )

        for ev in g["events"]:
            _append_event(key, ev)


def _append_event(key, ev):
    """Agrega `ev` a la lista `events` del ítem si todavía no está."""
    try:
        dynamodb.update_item(
            TableName=TABLE,
            Key=key,
            UpdateExpression="SET #ev = list_append(if_not_exists(#ev, :empty), :ev)",
            ConditionExpression="attribute_not_exists(#ev) OR NOT contains(#ev, :evs)",
            ExpressionAttributeNames={"#ev": "events"},
            ExpressionAttributeValues={
                ":empty": {"L": []},
                ":ev":    {"L": [{"S": ev}]},
                ":evs":   {"S": ev},
            },
        )
    except dynamodb.exceptions.ConditionalCheckFailedException:
        pass  # el evento ya estaba registrado


def _item_key(ts_iso):
    date_str = datetime.fromisoformat(ts_iso).strftime("%Y-%m-%d")
    return {
        "pk": {"S": f"{STATION}#{date_str}"},
        "sk": {"S": f"{ts_iso}#sensor"},
    }


def _parse_ts(ts_str):
    """
    Hora local del RTC → datetime UTC (sin microsegundos, para que coincida
    exactamente con el timestamp de las fotos). None si no se puede interpretar.
    """
    if not ts_str:
        return None
    try:
        # Formato app: "2026-09-24T21:36:19.000"
        # Formato firmware: "YY-MM-DDTHH-MM-SS" (guiones en la hora, año 2 dígitos)
        date_part, time_part = ts_str.split("T")
        y, m, d = date_part.split("-")
        if len(y) == 2:
            y = "20" + y
        normalized = f"{y}-{m}-{d}T{time_part.replace('-', ':')}"
        local_dt = datetime.fromisoformat(normalized)
        tz_local = timezone(timedelta(hours=RTC_UTC_OFFSET_H))
        return local_dt.replace(tzinfo=tz_local, microsecond=0).astimezone(timezone.utc)
    except Exception:
        return None
