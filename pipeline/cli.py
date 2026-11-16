"""Komut satırı: python -m pipeline.cli build --pbf data/istanbul-hist.osm.pbf --out out/istanbul"""

import argparse
import collections
import csv
import json
import os
import random
import statistics

from . import classify, enrich, osm_read, tiles
from . import wikidata as wd
from .geohash import bbox, encode


def _pct(values, p):
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


def neighbourhood_bytes(tile_size, gh):
    """Bir karonun çevresindeki 3×3 bloğun toplam boyutu (≈ 1 km yükleme kutusunun üst sınırı)."""
    lat0, lon0, lat1, lon1 = bbox(gh)
    dlat, dlon = lat1 - lat0, lon1 - lon0
    clat, clon = (lat0 + lat1) / 2, (lon0 + lon1) / 2
    total = 0
    for i in (-1, 0, 1):
        for j in (-1, 0, 1):
            total += tile_size.get(encode(clat + i * dlat, clon + j * dlon, len(gh)), 0)
    return total


def _has_answers(path):
    """Denetim dosyasında elle girilmiş cevap var mı (UTF-8, virgül ya da noktalı virgül)."""
    if not os.path.exists(path):
        return False
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        text = fh.read()
    delim = ";" if text.count(";") > text.count(",") else ","
    rows = list(csv.DictReader(text.splitlines(), delimiter=delim))
    return any((r.get("doğru_bina_mı") or "").strip() or (r.get("not") or "").strip() for r in rows)


def build(args):
    features = osm_read.read(args.pbf)
    match_stats, labels = {}, {}
    if args.wikidata:
        os.makedirs(args.cache, exist_ok=True)
        qids = {enrich._qid(f["tags"]) for f in features} - {None}
        print(f"OSM öğesi: {len(features)}, QID: {len(qids)}")
        items = wd.fetch_entities(qids, os.path.join(args.cache, "entities.json"))
        redirects = {v["redirect"] for v in items.values() if isinstance(v, dict) and "redirect" in v}
        if redirects:
            items = wd.fetch_entities(qids | redirects, os.path.join(args.cache, "entities.json"))
        closure = wd.class_closure(os.path.join(args.cache, "classes.json"))
        records, dropped = enrich.select(features, items, closure, enrich.cutoff(args.cutoff_years))
        ref_ids, match_stats = enrich.verify_and_enrich(records, items, closure)
        if args.bbox:
            extra, items = enrich.wikidata_only_candidates(
                [float(x) for x in args.bbox.split(",")], {r.get("qid") for r in records}, args.cache,
                closure, enrich.cutoff(args.cutoff_years))
            records.extend(extra)
            match_stats = dict(match_stats, wikidata_only=len(extra))
        lab = wd.fetch_entities(ref_ids, os.path.join(args.cache, "labels.json"), props="labels")
        labels = {q: {k: v for k, v in (lab.get(q) or {}).get("labels", {}).items() if k in ("tr", "en")}
                  for q in ref_ids}
    else:
        records, dropped = [], collections.Counter()
        for f in features:
            recs, reason = classify.classify(f)
            if reason:
                dropped[reason] += 1
            records.extend(recs)
    # Aynı kimlik iki kez gelmesin (ör. bölge dosyası taşması); ilk görülen kalır.
    seen, unique = set(), []
    for r in records:
        if r["id"] in seen:
            dropped["duplicate_id"] += 1
            continue
        seen.add(r["id"])
        unique.append(r)
    records = unique

    os.makedirs(args.out, exist_ok=True)
    stats = tiles.build(records, args.out, args.version, labels)
    sizes = list(stats["tile_size"].values())
    counts = list(stats["tile_count"].values())
    flags = collections.Counter(fl for r in records for fl in r["flags"])
    by_type = collections.Counter((r["type"], "ar" if r["ar"] else "list") for r in records)
    shapes = collections.Counter("poligon" if "footprint" in r else ("nokta+yarıçap" if "radius_m" in r else "nokta")
                                 for r in records)
    densest = max(stats["tile_size"], key=stats["tile_size"].get) if sizes else None
    report = {
        "dataset_version": args.version,
        "osm_features": len(features),
        "records": len(records),
        "ar": sum(r["ar"] for r in records),
        "list_only": sum(not r["ar"] for r in records),
        "dropped": dict(dropped),
        "shapes": dict(shapes),
        "flags": dict(flags.most_common()),
        "by_type_top": {f"{t}/{k}": n for (t, k), n in by_type.most_common(25)},
        "with_wikidata_tag": sum("qid" in r for r in records),
        "wikidata_match": match_stats,
        "with_period": sum("period" in r for r in records),
        "with_architect": sum("architect" in r for r in records),
        "with_image": sum("image" in r for r in records),
        "with_height": sum("height_m" in r for r in records),
        "with_levels_estimate": sum("label_height_m" in r for r in records),
        "names": {lang: sum(lang in r["names"] for r in records) for lang in ("tr", "en", "local")},
        "tiles": {
            "count": len(sizes),
            "records_per_tile_p50_p95_max": [_pct(counts, 50), _pct(counts, 95), max(counts, default=0)],
            "bytes_p50_p95_p99_max": [_pct(sizes, 50), _pct(sizes, 95), _pct(sizes, 99), max(sizes, default=0)],
            "total_bytes": sum(sizes),
            "bytes_per_record": round(sum(sizes) / max(1, len(records)), 1),
            "densest_tile": densest,
            "densest_3x3_bytes": neighbourhood_bytes(stats["tile_size"], densest) if densest else 0,
        },
        "index_bytes": stats["index_size"],
    }
    with open(os.path.join(args.out, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)

    # Elle denetim örneklemi: rastgele AR kayıtları (sabit tohum), ortofotoyla karşılaştırılacak.
    rng = random.Random(args.seed)
    ar = sorted((r for r in records if r["ar"]), key=lambda r: r["id"])
    sample = rng.sample(ar, min(args.sample, len(ar)))
    audit_path = os.path.join(args.out, "denetim-ornegi.csv")
    if _has_answers(audit_path):
        # Doldurulmuş denetim dosyasının üzerine yazılmaz (P0'da elle doldurulan dosya kayboluyordu).
        audit_path = os.path.join(args.out, f"denetim-ornegi-{args.version}-{args.seed}.csv")
        print(f"Doldurulmuş denetim dosyası korunuyor; yeni örneklem: {audit_path}")
    with open(audit_path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["id", "ad", "tür", "şekil", "alan_m2", "yarıçap_m", "osm_bağlantısı", "doğru_bina_mı", "not"])
        for r in sample:
            if r["osm"]:
                kind, num = r["osm"][0], r["osm"][1:].split("-")[0]
                url = f"https://www.openstreetmap.org/{ {'n': 'node', 'w': 'way', 'r': 'relation'}[kind] }/{num}"
            else:
                url = f"https://www.wikidata.org/wiki/{r['qid']}"
            name = r["names"].get("tr") or r["names"].get("local") or r["names"].get("en")
            w.writerow([r["id"], name, r["type"], "poligon" if "footprint" in r else "nokta",
                        r.get("area_m2", ""), r.get("radius_m", ""), url, "", ""])
    print(json.dumps(report, ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser(prog="pipeline")
    sub = ap.add_subparsers(required=True)
    b = sub.add_parser("build", help="PBF → karolar + rapor")
    b.add_argument("--pbf", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--version", default="p0")
    b.add_argument("--sample", type=int, default=50)
    b.add_argument("--seed", type=int, default=20261113)
    b.add_argument("--wikidata", action="store_true", help="Wikidata ile seçim ve eşleşme doğrulaması")
    b.add_argument("--cache", default="data/wikidata-cache")
    b.add_argument("--cutoff-years", type=int, default=100, help="P1435 yoksa en az bu kadar yıllık olmalı")
    b.add_argument("--bbox", help="lat_min,lon_min,lat_max,lon_max: OSM'de olmayan statülü yapılar (QLever)")
    b.set_defaults(func=build)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
