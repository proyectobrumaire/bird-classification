"""
Tests locales sin AWS (boto3, torch y el pipeline de ML se simulan).

    cd bird-classification && python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# ── Stubs de módulos externos (antes de importar las Lambdas) ─────────────
_boto3 = types.ModuleType("boto3")
_boto3.client = lambda *a, **k: mock.MagicMock()
sys.modules["boto3"] = _boto3

for name in ("torch", "PIL", "PIL.Image", "bird_detector",
             "utils_bounding_boxes_separation", "predict_image_from_tensors"):
    sys.modules[name] = mock.MagicMock()

os.environ.update({
    "BUCKET_NAME": "bucket-test",
    "API_SECRET": "secreto",
    "MODEL_KEY": "models/m.pth",
    "RTC_UTC_OFFSET_H": "-5",
    "STATION_NAME": "brumaire-1",
    "DYNAMO_TABLE": "tabla",
})

import log_processor   # noqa: E402
import gallery         # noqa: E402
import lambda_handler  # noqa: E402
sys.path.insert(0, str(ROOT / "scripts"))
import migrar_fotos_eventos as migracion  # noqa: E402


class CCF(Exception):
    """ConditionalCheckFailedException simulada."""


class FakeDynamo:
    """DynamoDB en memoria: solo lo que usa log_processor.write_groups."""

    def __init__(self):
        self.items = {}
        self.exceptions = types.SimpleNamespace(ConditionalCheckFailedException=CCF)

    def update_item(self, TableName, Key, UpdateExpression, ExpressionAttributeValues,
                    ExpressionAttributeNames=None, ConditionExpression=None):
        names = ExpressionAttributeNames or {}
        vals  = ExpressionAttributeValues
        k     = (Key["pk"]["S"], Key["sk"]["S"])
        item  = self.items.setdefault(k, dict(Key))
        if UpdateExpression.startswith("SET #ev = list_append"):
            ev   = vals[":evs"]
            have = item.get("events", {"L": []})["L"]
            if ev in have:
                raise CCF()
            item["events"] = {"L": have + vals[":ev"]["L"]}
            return
        for part in UpdateExpression[len("SET "):].split(", "):
            lhs, rhs = part.split(" = ")
            item[names.get(lhs, lhs)] = vals[rhs]


def _entry(ts, **kw):
    e = {"timestamp": ts, "timestamp_valid": True, "seq": 1}
    e.update(kw)
    return e


# ── log_processor ─────────────────────────────────────────────────────────

class TestLogProcessor(unittest.TestCase):
    ENTRIES = [
        _entry("2026-09-24T21:36:19.000", type="event", event="PERIODIC"),
        _entry("2026-09-24T21:36:19.000", type="sensorData", sensor_key="T1_K", sensor_value=24.5),
        _entry("2026-09-24T21:36:19.000", type="sensorData", sensor_key="H1_K", sensor_value=70.1),
        _entry("2026-09-24T21:36:19.000", type="event", event="PERIODIC"),     # duplicado
        _entry("2026-09-24T21:40:00.000", type="event", event="VOLCADO"),      # sin lecturas
        _entry("2026-09-24T21:50:00.000", type="event", event="BOOT", timestamp_valid=False),
        _entry("basura", type="event", event="BOOT"),
    ]

    def setUp(self):
        self.fake = FakeDynamo()
        self.p = mock.patch.object(log_processor, "dynamodb", self.fake)
        self.p.start()

    def tearDown(self):
        self.p.stop()

    def test_agrupa_por_timestamp_utc(self):
        g = log_processor.group_entries(self.ENTRIES)
        self.assertEqual(set(g), {"2026-09-25T02:36:19+00:00", "2026-09-25T02:40:00+00:00"})
        self.assertEqual(g["2026-09-25T02:36:19+00:00"],
                         {"sensors": {"T1_K": "24.5", "H1_K": "70.1"}, "events": ["PERIODIC"]})
        self.assertEqual(g["2026-09-25T02:40:00+00:00"], {"sensors": {}, "events": ["VOLCADO"]})

    def test_timestamp_coincide_con_foto(self):
        # Mismo instante que image_26-09-24T21-36-19_0.jpg
        ts_log  = log_processor._parse_ts("2026-09-24T21:36:19.000")
        ts_foto = lambda_handler._capture_ts("image_26-09-24T21-36-19_0.jpg")
        self.assertEqual(ts_log.isoformat(), ts_foto.isoformat())

    def test_escribe_e_idempotente(self):
        g = log_processor.group_entries(self.ENTRIES)
        log_processor.write_groups(g)
        primero = json.dumps(sorted(self.fake.items.items()), sort_keys=True)
        log_processor.write_groups(g)
        self.assertEqual(json.dumps(sorted(self.fake.items.items()), sort_keys=True), primero)

        it = self.fake.items[("brumaire-1#2026-09-25", "2026-09-25T02:36:19+00:00#sensor")]
        self.assertEqual(it["type"], {"S": "sensor"})
        self.assertEqual(it["T1_K"], {"N": "24.5"})
        self.assertEqual(it["events"], {"L": [{"S": "PERIODIC"}]})
        solo_ev = self.fake.items[("brumaire-1#2026-09-25", "2026-09-25T02:40:00+00:00#sensor")]
        self.assertEqual(solo_ev["events"], {"L": [{"S": "VOLCADO"}]})
        self.assertIn("expires_at", solo_ev)
        self.assertNotIn("T1_K", solo_ev)

    def test_batches_distintos_se_combinan(self):
        ts = "2026-09-24T21:36:19.000"
        log_processor.write_groups(log_processor.group_entries([
            _entry(ts, type="sensorData", sensor_key="T1_K", sensor_value=1),
            _entry(ts, type="event", event="BIRD"),
        ]))
        log_processor.write_groups(log_processor.group_entries([
            _entry(ts, type="sensorData", sensor_key="H1_K", sensor_value=2),
            _entry(ts, type="event", event="PERIODIC"),
            _entry(ts, type="event", event="BIRD"),
        ]))
        it = self.fake.items[("brumaire-1#2026-09-25", "2026-09-25T02:36:19+00:00#sensor")]
        self.assertEqual(it["T1_K"], {"N": "1"})
        self.assertEqual(it["H1_K"], {"N": "2"})
        self.assertEqual(it["events"], {"L": [{"S": "BIRD"}, {"S": "PERIODIC"}]})


# ── lambda_handler ────────────────────────────────────────────────────────

class TestClassifier(unittest.TestCase):
    def _run(self, results):
        key = "images/raw/2026/09/24/image_26-09-24T21-36-19_0.jpg"
        event = {"Records": [{"s3": {"object": {"key": key}}}]}
        with mock.patch.object(lambda_handler, "dynamodb") as ddb, \
             mock.patch.object(lambda_handler, "s3"), \
             mock.patch.object(lambda_handler, "load_detector", return_value=(None, None)), \
             mock.patch.object(lambda_handler, "load_species_model", return_value=(None, None)), \
             mock.patch.object(lambda_handler, "classify_crops_batch", return_value=results):
            lambda_handler.handler(event, None)
        return [c.kwargs["Item"] for c in ddb.put_item.call_args_list]

    def test_foto_sin_aves_crea_item_photo(self):
        items = self._run([{"keep": False}])
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it["sk"]["S"], "2026-09-25T02:36:19+00:00#photo#image_26-09-24T21-36-19_0.jpg")
        self.assertEqual(it["pk"]["S"], "brumaire-1#2026-09-25")
        self.assertEqual(it["type"]["S"], "photo")
        self.assertEqual(it["raw_key"]["S"], "images/raw/2026/09/24/image_26-09-24T21-36-19_0.jpg")
        self.assertEqual(it["image_key"]["S"],
                         "images/processed/2026/09/24/image_26-09-24T21-36-19_0_pred.png")
        self.assertEqual(it["n_detections"]["N"], "0")
        self.assertIn("expires_at", it)

    def test_foto_con_aves_mantiene_items_bird(self):
        ave = {"keep": True, "pred_species": "colibri", "pred_conf": 0.93,
               "detector_score": 0.98, "padded_box": (1, 2, 3, 4)}
        items = self._run([ave, {"keep": False}, ave])
        tipos = [it["type"]["S"] for it in items]
        self.assertEqual(tipos, ["bird", "bird", "photo"])
        self.assertEqual(items[1]["sk"]["S"],
                         "2026-09-25T02:36:19+00:00#bird#image_26-09-24T21-36-19_0.jpg#1")
        self.assertEqual(items[2]["n_detections"]["N"], "2")


# ── gallery: /photos, /events, /gallery ───────────────────────────────────

def sensor(ts, events=None, **readings):
    it = {"pk": {"S": "p"}, "sk": {"S": f"{ts}#sensor"}, "type": {"S": "sensor"},
          "expires_at": {"N": "1"}}
    for k, v in readings.items():
        it[k] = {"N": str(v)}
    if events is not None:
        it["events"] = {"L": [{"S": e} for e in events]}
    return it


def photo(ts, fname, image_key=True):
    it = {"pk": {"S": "p"}, "sk": {"S": f"{ts}#photo#{fname}"}, "type": {"S": "photo"},
          "filename": {"S": fname}, "raw_key": {"S": f"images/raw/{fname}"},
          "n_detections": {"N": "0"}}
    if image_key:
        it["image_key"] = {"S": f"images/processed/{fname}_pred.png"}
    return it


def bird(ts, fname, i, species="colibri", conf=0.9):
    return {"pk": {"S": "p"}, "sk": {"S": f"{ts}#bird#{fname}#{i}"}, "type": {"S": "bird"},
            "filename": {"S": fname}, "species": {"S": species},
            "confidence": {"N": str(conf)}, "detector_score": {"N": "0.98"}}


T1 = "2026-09-25T02:36:19+00:00"
T2 = "2026-09-25T03:00:00+00:00"
T3 = "2026-09-25T03:00:01+00:00"


class TestBuildPhotos(unittest.TestCase):
    def test_orden_detecciones_env_exacto(self):
        items = [
            sensor(T1, ["BIRD"], T1_K=24.5, H1_K=70.1),
            photo(T1, "a_1.jpg"), photo(T1, "a_0.jpg"),
            bird(T1, "a_0.jpg", 1, "b", 0.8), bird(T1, "a_0.jpg", 0, "a", 0.93),
            photo(T2, "b_0.jpg", image_key=False),
            sensor(T3, T1_K=1.0),   # un segundo después: NO se asocia a b_0
        ]
        fotos, truncated = gallery.build_photos(items, presign=lambda k: f"url:{k}")
        self.assertFalse(truncated)
        self.assertEqual([f["filename"] for f in fotos], ["b_0.jpg", "a_0.jpg", "a_1.jpg"])
        self.assertEqual(fotos[0]["env"], {})
        self.assertIsNone(fotos[0]["image_url"])
        self.assertEqual(fotos[0]["raw_url"], "url:images/raw/b_0.jpg")
        self.assertEqual(fotos[0]["detections"], [])
        self.assertEqual(fotos[1]["timestamp"], T1)
        self.assertEqual(fotos[1]["env"], {"T1_K": 24.5, "H1_K": 70.1})
        self.assertEqual(fotos[1]["image_url"], "url:images/processed/a_0.jpg_pred.png")
        self.assertEqual(fotos[1]["detections"], [
            {"species": "a", "confidence": 0.93, "detector_score": 0.98},
            {"species": "b", "confidence": 0.8, "detector_score": 0.98},
        ])

    def test_truncated(self):
        items = [photo(f"2026-09-25T00:00:{i:02d}+00:00", f"f{i}.jpg") for i in range(5)]
        fotos, truncated = gallery.build_photos(items, presign=str, max_items=3)
        self.assertTrue(truncated)
        self.assertEqual([f["filename"] for f in fotos], ["f4.jpg", "f3.jpg", "f2.jpg"])
        _, truncated = gallery.build_photos(items, presign=str, max_items=5)
        self.assertFalse(truncated)


class TestBuildEvents(unittest.TestCase):
    ITEMS = [
        sensor(T1, ["BIRD", "PERIODIC"], T1_K=24.5),
        sensor(T2, ["VOLCADO"]),
        sensor(T3, T1_K=3.0),    # sin eventos
        photo(T1, "a_0.jpg"),
    ]

    def test_un_elemento_por_evento_orden(self):
        evs, truncated = gallery.build_events(self.ITEMS)
        self.assertFalse(truncated)
        self.assertEqual(evs, [
            {"timestamp": T2, "event": "VOLCADO", "env": {}},
            {"timestamp": T1, "event": "BIRD", "env": {"T1_K": 24.5}},
            {"timestamp": T1, "event": "PERIODIC", "env": {"T1_K": 24.5}},
        ])

    def test_filtro_types_y_truncated(self):
        evs, _ = gallery.build_events(self.ITEMS, types={"PERIODIC", "VOLCADO"})
        self.assertEqual([e["event"] for e in evs], ["VOLCADO", "PERIODIC"])
        evs, truncated = gallery.build_events(self.ITEMS, max_items=2)
        self.assertTrue(truncated)
        self.assertEqual(len(evs), 2)


class TestHandler(unittest.TestCase):
    def setUp(self):
        self.ddb = mock.MagicMock()
        self.s3  = mock.MagicMock()
        self.s3.generate_presigned_url.side_effect = lambda op, Params, ExpiresIn: f"url:{Params['Key']}"
        self.patches = [mock.patch.object(gallery, "dynamodb", self.ddb),
                        mock.patch.object(gallery, "s3", self.s3)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def call(self, path, body, key="secreto", raw=False):
        ev = {"routeKey": f"POST {path}", "rawPath": path, "headers": {"x-api-key": key},
              "body": body if raw else json.dumps(body)}
        r = gallery.handler(ev, None)
        return r["statusCode"], json.loads(r["body"])

    def test_forbidden_y_rutas(self):
        self.assertEqual(self.call("/photos", {}, key="x"), (403, {"error": "Forbidden"}))
        self.assertEqual(self.call("/otra", {})[0], 404)
        self.assertEqual(self.call("/photos", {"from": "x", "to": "y"})[0], 400)
        self.assertEqual(self.call("/events", "{", raw=True)[0], 400)
        self.assertEqual(self.call("/events", {"from": T1, "to": T2, "types": "BIRD"})[0], 400)
        self.assertEqual(self.call("/photos", {"from": T2, "to": T1})[0], 400)

    def test_ruteo_por_rawpath(self):
        self.ddb.query.return_value = {"Items": []}
        ev = {"rawPath": "/events", "headers": {"x-api-key": "secreto"},
              "body": json.dumps({"from": T1, "to": T2})}
        r = gallery.handler(ev, None)
        self.assertEqual(json.loads(r["body"]), {"events": [], "truncated": False})

    def test_photos_pagina_y_consulta_dia_a_dia(self):
        # Página 1 con LastEvaluatedKey, página 2 final (día 25); día 24 vacío
        pages = {
            ("brumaire-1#2026-09-25", None): {"Items": [photo(T1, "a_0.jpg")], "LastEvaluatedKey": {"k": 1}},
            ("brumaire-1#2026-09-25", 1):    {"Items": [sensor(T1, ["BIRD"], T1_K=2.0)]},
            ("brumaire-1#2026-09-24", None): {"Items": []},
        }
        self.ddb.query.side_effect = lambda **p: pages[(
            p["ExpressionAttributeValues"][":pk"]["S"],
            p.get("ExclusiveStartKey", {}).get("k"))]

        status, body = self.call("/photos", {"from": "2026-09-24T12:00:00Z", "to": "2026-09-25T04:00:00Z"})
        self.assertEqual(status, 200)
        self.assertEqual(body["truncated"], False)
        self.assertEqual(body["photos"][0]["env"], {"T1_K": 2.0})
        self.assertEqual(body["photos"][0]["timestamp"], T1)

        pks = [c.kwargs["ExpressionAttributeValues"][":pk"]["S"] for c in self.ddb.query.call_args_list]
        self.assertEqual(pks, ["brumaire-1#2026-09-25", "brumaire-1#2026-09-25", "brumaire-1#2026-09-24"])
        first = self.ddb.query.call_args_list[0].kwargs["ExpressionAttributeValues"]
        self.assertEqual(first[":from"]["S"], "2026-09-25T00:00:00+00:00")
        self.assertEqual(first[":to"]["S"], "2026-09-25T04:00:00+00:00~")

    def test_rango_con_offset_se_normaliza_a_utc(self):
        self.ddb.query.return_value = {"Items": []}
        self.call("/events", {"from": "2026-09-24T21:00:00-05:00", "to": "2026-09-24T22:00:00-05:00"})
        vals = self.ddb.query.call_args.kwargs["ExpressionAttributeValues"]
        self.assertEqual(vals[":pk"]["S"], "brumaire-1#2026-09-25")
        self.assertEqual(vals[":from"]["S"], "2026-09-25T02:00:00+00:00")

    def test_photos_se_detiene_al_superar_el_maximo(self):
        dia = lambda d: [photo(f"2026-09-{d}T10:00:{i:02d}+00:00", f"{d}_{i}.jpg") for i in range(3)]
        self.ddb.query.side_effect = lambda **p: {
            "Items": dia(p["ExpressionAttributeValues"][":pk"]["S"][-2:])}
        with mock.patch.object(gallery, "MAX_ITEMS", 4):
            _, body = self.call("/photos", {"from": "2026-09-20T00:00:00Z", "to": "2026-09-25T23:00:00Z"})
        self.assertEqual(self.ddb.query.call_count, 2)   # días 25 y 24; no sigue
        self.assertTrue(body["truncated"])
        self.assertEqual(len(body["photos"]), 4)
        self.assertEqual(body["photos"][0]["filename"], "25_2.jpg")

    def test_gallery_env_exacto(self):
        self.ddb.query.return_value = {"Items": [
            bird(T1, "image_26-09-24T21-36-19_0.jpg", 0),
            sensor(T1, ["BIRD"], T1_K=5.0),
            bird(T2, "image_26-09-24T22-00-00_0.jpg", 0),
            sensor(T3, T1_K=9.0),   # 1 s después de T2: antes se habría asociado
        ]}
        status, body = self.call("/gallery", {"from": T1, "to": T3})
        self.assertEqual(status, 200)
        envs = {e["timestamp"]: e["env"] for e in body["colibri"]}
        self.assertEqual(envs, {T1: {"T1_K": 5.0}, T2: {}})


class TestMigracion(unittest.TestCase):
    def test_item_photo_igual_al_del_clasificador(self):
        raw = "images/raw/2026/09/24/image_26-09-24T21-36-19_0.jpg"
        fname = "image_26-09-24T21-36-19_0.jpg"
        ts = migracion.capture_ts(fname)
        self.assertEqual(ts, lambda_handler._capture_ts(fname))
        pred = migracion.pred_key(raw)
        self.assertEqual(pred, "images/processed/2026/09/24/image_26-09-24T21-36-19_0_pred.png")
        self.assertEqual(migracion.photo_item(ts, fname, raw, pred, 2),
                         lambda_handler._photo_item(ts, fname, raw, pred, 2))
        self.assertNotIn("image_key", migracion.photo_item(ts, fname, raw, None, 0))
        self.assertIsNone(migracion.capture_ts("foto.jpg"))


if __name__ == "__main__":
    unittest.main()
