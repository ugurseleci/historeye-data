"""Karo, dizin ve manifest üretimi (tasarım §6, §7). Çıktı deterministiktir: aynı girdi → aynı bayt."""

import gzip
import hashlib
import io
import json
import os

from . import DATASET_SCHEMA_VERSION
from .geo import e7
from .geohash import encode

TILE_PRECISION = 5
INDEX_PRECISION = 3


def _ll(p):
    return [e7(p[0]), e7(p[1])]


def _ref(ids, labels):
    return [dict({"qid": q}, **labels.get(q, {})) for q in ids]


def encode_record(rec, labels=None):
    """İç kayıt → karo kaydı (koordinatlar [enlem, boylam] 1e-7 derece tamsayı)."""
    out = {"id": rec["id"], "layer": rec["layer"], "ar": rec["ar"], "list": rec["list"],
           "type": rec["type"], "names": rec["names"], "priority": rec["priority"], "osm": rec["osm"],
           "ground_elev_m": None}
    if "footprint" in rec:
        out["footprint"] = [_ll(p) for p in rec["footprint"]]
    else:
        out["point"] = _ll(rec["point"])
        if "radius_m" in rec:
            out["radius_m"] = rec["radius_m"]
    labels = labels or {}
    for k in ("height_m", "label_height_m", "qid", "heritage", "group_qid", "period", "image", "wiki"):
        if k in rec:
            out[k] = rec[k]
    for k in ("architect", "style", "heritage_status"):
        if k in rec:
            out[k] = _ref(rec[k], labels)
    if rec["flags"]:
        out["flags"] = sorted(set(rec["flags"]))
    return out


def gzip_json(obj):
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0, compresslevel=9) as gz:
        gz.write(raw)
    return buf.getvalue()


def _put(out_dir, sub, data):
    digest = hashlib.sha256(data).hexdigest()
    path = os.path.join(out_dir, sub, f"{digest}.json.gz")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "wb") as fh:
            fh.write(data)
    return digest


def build(records, out_dir, dataset_version, labels=None):
    """Kayıtları karolara yazar; karo, dizin ve manifest dosyalarını üretir. İstatistik döndürür."""
    by_tile = {}
    for rec in records:
        gh = encode(rec["rep"][0], rec["rep"][1], TILE_PRECISION)  # her kayıt tek karoya (temsil noktası)
        by_tile.setdefault(gh, []).append(rec)

    tile_hash, tile_size = {}, {}
    for gh, recs in sorted(by_tile.items()):
        recs.sort(key=lambda r: r["id"])
        body = {"schema_version": DATASET_SCHEMA_VERSION, "dataset_version": dataset_version,
                "geohash": gh, "dem": None, "records": [encode_record(r, labels) for r in recs]}
        data = gzip_json(body)
        tile_hash[gh] = _put(out_dir, "tiles", data)
        tile_size[gh] = len(data)

    indexes = {}
    for gh, h in tile_hash.items():
        indexes.setdefault(gh[:INDEX_PRECISION], {})[gh] = h
    index_hash, index_size = {}, {}
    for g3, entries in sorted(indexes.items()):
        data = gzip_json({"schema_version": DATASET_SCHEMA_VERSION, "geohash": g3, "tiles": entries})
        index_hash[g3] = _put(out_dir, "index", data)
        index_size[g3] = len(data)

    manifest = {"schema_version": DATASET_SCHEMA_VERSION, "dataset_version": dataset_version,
                "index": index_hash, "min_app_version": "0.1"}
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=1, sort_keys=True)
    return {"tile_size": tile_size, "tile_count": {gh: len(r) for gh, r in by_tile.items()},
            "index_size": index_size}
