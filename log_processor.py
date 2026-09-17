import json
import os
import boto3
from collections import defaultdict
from datetime import datetime, timezone, timedelta

dynamodb   = boto3.client("dynamodb")
s3         = boto3.client("s3")

STATION     = os.environ.get("STATION_NAME", "brumaire-1")
TABLE       = os.environ.get("DYNAMO_TABLE", "brumaire-telemetry")

# TTL: 1 año en segundos
TTL_SECS = 365 * 24 * 3600


def handler(event, context):
    record = event["Records"][0]
    bucket = record["s3"]["bucket"]["name"]
    key    = record["s3"]["object"]["key"]

    obj     = s3.get_object(Bucket=bucket, Key=key)
    entries = json.loads(obj["Body"].read()).get("entries", [])

    sensor_groups = defaultdict(dict)           # {ts_iso: {sensor_key: value}}

    for entry in entries:
        if not entry.get("timestamp_valid", True):
            continue
        if entry.get("type") != "sensorData":
            continue

        ts   = _parse_ts(entry.get("timestamp"))
        skey = entry.get("sensor_key")
        sval = entry.get("sensor_value")
        if skey and sval is not None:
            # Agrupar por timestamp para un item por lectura periódica
            sensor_groups[ts.isoformat()][skey] = str(sval)

    # Un item por grupo de sensores (= una lectura periódica)
    for ts_iso, sensors in sensor_groups.items():
        ts_dt    = datetime.fromisoformat(ts_iso)
        date_str = ts_dt.strftime("%Y-%m-%d")
        item = {
            "pk":         {"S": f"{STATION}#{date_str}"},
            "sk":         {"S": f"{ts_iso}#sensor"},
            "type":       {"S": "sensor"},
            "expires_at": {"N": str(int(ts_dt.timestamp()) + TTL_SECS)},
        }
        for k, v in sensors.items():
            item[k] = {"N": v}
        dynamodb.put_item(TableName=TABLE, Item=item)

    print(f"SENSOR_GROUPS key={key} sensor_groups={len(sensor_groups)}")
    return {"statusCode": 200, "sensor_groups": len(sensor_groups)}


RTC_UTC_OFFSET_H = int(os.environ.get("RTC_UTC_OFFSET_H", "0"))

def _parse_ts(ts_str):
    if not ts_str:
        return datetime.now(timezone.utc)
    try:
        # Formato firmware: "YY-MM-DDTHH-MM-SS" (guiones en la hora, año 2 dígitos)
        date_part, time_part = ts_str.split("T")
        y, m, d = date_part.split("-")
        if len(y) == 2:
            y = "20" + y
        normalized = f"{y}-{m}-{d}T{time_part.replace('-', ':')}"
        local_dt = datetime.fromisoformat(normalized)
        tz_local = timezone(timedelta(hours=RTC_UTC_OFFSET_H))
        return local_dt.replace(tzinfo=tz_local).astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)
