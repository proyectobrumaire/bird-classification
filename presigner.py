import json
import os
import boto3
from datetime import datetime, timezone

s3 = boto3.client("s3")
BUCKET = os.environ["BUCKET_NAME"]
SECRET = os.environ["API_SECRET"]

PREFIX_MAP = {
    "image":      "images/raw",
    "device_log": "logs/device",
    "app_log":    "logs/app",
}


def handler(event, context):
    auth = (event.get("headers") or {}).get("x-api-key", "")
    if auth != SECRET:
        return _response(403, {"error": "Forbidden"})

    try:
        body = json.loads(event.get("body") or "{}")
        file_type = body["type"]
        filename  = body["filename"]
        date_str  = body.get("date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    except (KeyError, json.JSONDecodeError) as e:
        return _response(400, {"error": str(e)})

    if file_type not in PREFIX_MAP:
        return _response(400, {"error": f"type debe ser uno de {list(PREFIX_MAP)}"})

    year, month, day = date_str.split("-")
    key = f"{PREFIX_MAP[file_type]}/{year}/{month}/{day}/{filename}"

    params = {"Bucket": BUCKET, "Key": key}
    if file_type == "app_log":
        params["ContentType"] = "application/json"

    url = s3.generate_presigned_url(
        "put_object",
        Params=params,
        ExpiresIn=300,  # 5 minutos
    )

    return _response(200, {"url": url, "key": key})


def _response(status, body):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }
