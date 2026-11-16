"""Denetim örnekleminin otomatik çapraz denetimi (elle ortofoto denetiminin YERİNİ TUTMAZ).

Her AR kaydı için bağımsız ikinci bir kaynak aranır: kaydın Wikipedia maddesinin koordinatı
(madde içindeki koordinat şablonu; Wikidata P625'ten ayrı tutulur). Sonuç:
  tutarlı       — Wikipedia koordinatı OSM geometrisine ≤ 50 m
  şüpheli       — Wikipedia koordinatı var ama > 50 m uzakta (elle bakılmalı)
  doğrulanamadı — bağımsız kaynak yok (Wikipedia maddesi ya da koordinatı yok)

Kullanım: .venv/bin/python scripts/otomatik_denetim.py denetim/p0-bolgeler.csv
"""

import csv
import glob
import gzip
import json
import os
import sys
import time
import urllib.parse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shapely.geometry import Point, Polygon  # noqa: E402

from pipeline import wikidata as wd  # noqa: E402
from pipeline.geo import to_enu  # noqa: E402

REGION_DIR = {"Roma": "roma", "Lizbon": "lizbon", "Oslo": "oslo", "Kapadokya": "kapadokya", "İstanbul": "ist-p0"}
LIMIT_M = 50.0


def load_records(out_dir):
    recs = {}
    for p in glob.glob(os.path.join(out_dir, "tiles", "*.json.gz")):
        for r in json.load(gzip.open(p))["records"]:
            recs[r["id"]] = r
    return recs


def wiki_coords(lang, titles, log=print):
    """Wikipedia maddesi → birincil koordinat (lat, lon) ya da None."""
    out = {}
    titles = sorted(set(titles))
    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        res = wd._http_json(f"https://{lang}.wikipedia.org/w/api.php", {
            "action": "query", "prop": "coordinates", "titles": "|".join(batch), "redirects": "1",
            "colimit": "max", "format": "json", "formatversion": "2"})
        if "error" in res:
            raise RuntimeError(f"{lang}.wikipedia hatası: {res['error']}")
        q = res.get("query", {})
        alias = {}
        for n in q.get("normalized", []) + q.get("redirects", []):
            alias[n["to"]] = alias.get(n["from"], n["from"])
        for page in q.get("pages", []):
            orig = alias.get(page["title"], page["title"])
            c = (page.get("coordinates") or [None])[0]
            out[orig] = (c["lat"], c["lon"]) if c and c.get("globe", "earth") == "earth" else None
        time.sleep(1)
    return out


def distance_m(rec, lat, lon):
    def ll(p):
        return p[0] / 1e7, p[1] / 1e7
    if rec.get("footprint"):
        ring = [ll(p) for p in rec["footprint"]]
        lat0, lon0 = ring[0]
        poly = Polygon([to_enu(lat0, lon0, a, b) for a, b in ring])
        return poly.distance(Point(to_enu(lat0, lon0, lat, lon)))
    a, b = ll(rec["point"])
    e, n = to_enu(a, b, lat, lon)
    return max(0.0, (e * e + n * n) ** 0.5 - (rec.get("radius_m") or 0))


def main(path):
    with open(path, encoding="utf-8-sig") as fh:
        text = fh.read()
    delim = ";" if text.count(";") > text.count(",") else ","
    rows = list(csv.DictReader(text.splitlines(), delimiter=delim))
    cache = {}
    for region, d in REGION_DIR.items():
        if any(r.get("bölge", "İstanbul") == region for r in rows):
            cache[region] = load_records(os.path.join("out", d))
    want = {"en": [], "tr": []}
    for r in rows:
        rec = cache[r.get("bölge", "İstanbul")].get(r["id"])
        for lang in ("en", "tr"):
            t = (rec or {}).get("wiki", {}).get(lang)
            if t:
                want[lang].append(t)
    coords = {lang: wiki_coords(lang, titles) for lang, titles in want.items() if titles}
    summary = {}
    for r in rows:
        region = r.get("bölge", "İstanbul")
        rec = cache[region].get(r["id"])
        result, detail = "doğrulanamadı", "kayıt karoda yok" if rec is None else "bağımsız kaynak yok"
        if rec:
            dists = []
            for lang in ("en", "tr"):
                t = rec.get("wiki", {}).get(lang)
                c = coords.get(lang, {}).get(t) if t else None
                if c:
                    dists.append((distance_m(rec, *c), lang))
            if dists:
                d, lang = min(dists)
                result = "tutarlı" if d <= LIMIT_M else "şüpheli"
                detail = f"{lang}.wikipedia koordinatı {d:.0f} m"
            elif rec.get("wiki"):
                detail = "Wikipedia maddesinde koordinat yok"
        r["otomatik_denetim"] = result
        r["otomatik_ayrıntı"] = detail
        summary.setdefault(region, {}).setdefault(result, 0)
        summary[region][result] += 1
    out = path.replace(".csv", "-otomatik.csv")
    with open(out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(rows)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print("şüpheliler:")
    for r in rows:
        if r["otomatik_denetim"] == "şüpheli":
            print(f"  {r.get('bölge', '')}: {r['ad']} ({r['tür']}) — {r['otomatik_ayrıntı']} — {r['osm_bağlantısı']}")
    print("→", out)


if __name__ == "__main__":
    main(sys.argv[1])
