"""Aday seçimi (OSM + Wikidata), eşleşme doğrulaması ve kart alanları (tasarım §2–§5)."""

import collections
import datetime
import urllib.parse

from shapely.geometry import Point, Polygon

from . import classify as cl
from . import wikidata as wd
from .geo import to_enu

MATCH_DISTANCE_M = 50.0


def _qid(tags):
    v = (tags.get("wikidata") or "").split(";")[0].strip()
    return v if v.startswith("Q") else None


def _resolve(items, qid):
    item = items.get(qid)
    if isinstance(item, dict) and "redirect" in item:
        return item["redirect"], items.get(item["redirect"])
    return qid, item


def select(features, items, closure, cutoff_year):
    """OSM öğesi aday mı? OSM tarihi etiketi ya da Wikidata'da tarihi yapı. → (kayıtlar, atılma sayaçları)."""
    records, dropped = [], collections.Counter()
    for f in features:
        tags = f["tags"]
        qid = _qid(tags)
        if qid:
            qid, item = _resolve(items, qid)
        else:
            item = None
        if cl.historic_type(tags) is not None:
            recs, reason = cl.classify(f)
        elif item is None:
            dropped["wd_missing" if qid else "not_candidate"] += 1
            continue
        elif wd.is_excluded_class(item, closure):
            dropped["wd_settlement_or_area"] += 1
            continue
        elif not wd.is_structure(item, closure):
            dropped["wd_not_structure"] += 1
            continue
        elif not wd.is_historic(item, cutoff_year):
            dropped["wd_not_historic"] += 1
            continue
        else:
            recs, reason = cl.classify(f, htype=cl.type_from_osm(tags))
            for r in recs:
                r["flags"].append("via_wikidata")
        if reason:
            dropped[reason] += 1
        for r in recs:
            if "qid" in r:
                r["qid"] = qid  # yönlendirme çözülmüş
        records.extend(recs)
    return records, dropped


def _distance_to_record(rec, lat, lon):
    lat0, lon0 = rec["rep"]
    p = Point(to_enu(lat0, lon0, lat, lon))
    if "footprint" in rec:
        return Polygon([to_enu(lat0, lon0, a, b) for a, b in rec["footprint"]]).distance(p)
    return p.distance(Point(to_enu(lat0, lon0, *rec["point"])))


def verify_and_enrich(records, items, closure):
    """QID'li kayıtlarda konum + ad doğrulaması; geçenlere Wikidata kart alanları."""
    by_qid = collections.defaultdict(list)
    for r in records:
        if r.get("qid"):
            by_qid[r["qid"]].append(r)
    ref_ids = set()
    stats = collections.Counter()
    for qid, group in by_qid.items():
        item = items.get(qid)
        if not item or "redirect" in item:
            for r in group:
                r["ar"] = False
                r["flags"].append("wd_missing")
            stats["wd_missing"] += len(group)
            continue
        pts = wd.coords(item)
        buildings = [r for r in group if "footprint" in r]
        if len(group) > 1 and len(buildings) == len(group):
            for r in group:
                r["group_qid"] = qid
        for r in group:
            near = bool(pts) and min(min(_distance_to_record(g, lat, lon) for g in group) for lat, lon in pts) \
                <= MATCH_DISTANCE_M
            name_ok = wd.names_match(list(r["names"].values()), item)
            r["flags"] = [f for f in r["flags"] if f != "wd_unverified"]
            if not pts:
                r["flags"].append("wd_no_coord")
            elif not near:
                r["flags"].append("wd_far")
            if not name_ok:
                r["flags"].append("wd_name_mismatch")
            if pts and near and name_ok:
                stats["verified"] += 1
            else:
                r["flags"].append("match_conflict")
                r["ar"] = False
                stats["match_conflict"] += 1
                continue  # doğrulanmayan eşleşmeden nitelik alınmaz
            labels = item.get("labels", {})
            for lang in ("tr", "en"):
                if lang not in r["names"] and lang in labels:
                    r["names"][lang] = labels[lang]
            if not wd.is_institution(item, closure):
                p = wd.period(item)
                if p:
                    r["period"] = p
            for key, prop in (("architect", "P84"), ("style", "P149"), ("heritage_status", "P1435")):
                ids = wd.item_ids(item, prop)[:3]
                if ids:
                    r[key] = ids
                    ref_ids.update(ids)
            img = wd.image_file(item)
            if img:
                r["image"] = {"file": img}
            if item.get("sitelinks"):
                r["wiki"] = {k[:2]: v for k, v in item["sitelinks"].items()}
            level = wd.heritage_level(item)
            bonus = (4 if level == 1 else 3 if level == 2 else 0) + wd.sitelink_score(item)
            r["priority"] = min(8, max(r["priority"], 2 + bonus + (2 if r["type"] in {"mosque", "church", "palace",
                                                                                       "castle", "synagogue"} else 0)))
    return ref_ids, stats


def wikidata_only_candidates(bbox, known_qids, cache_dir, closure, cutoff_year, log=print):
    """OSM'de karşılığı olmayan, koordinatlı ve koruma statülü yapılar → yalnızca liste (tasarım §2.3)."""
    lat_min, lon_min, lat_max, lon_max = bbox
    q = ("PREFIX wdt: <http://www.wikidata.org/prop/direct/> "
         "PREFIX geof: <http://www.opengis.net/def/function/geosparql/> "
         "SELECT DISTINCT ?item WHERE { ?item wdt:P1435 ?h ; wdt:P625 ?c . "
         "BIND(geof:latitude(?c) AS ?lat) BIND(geof:longitude(?c) AS ?lon) "
         f"FILTER(?lat > {lat_min} && ?lat < {lat_max} && ?lon > {lon_min} && ?lon < {lon_max}) }}")
    res = wd._http_json(wd.QLEVER + "?" + urllib.parse.urlencode({"query": q}),
                        accept="application/sparql-results+json")
    qids = sorted({b["item"]["value"].rsplit("/", 1)[1] for b in res["results"]["bindings"]} - set(known_qids))
    log(f"  QLever: {len(qids)} OSM'de karşılığı olmayan statülü öğe")
    items = wd.fetch_entities(qids, f"{cache_dir}/entities.json", log=log)
    out = []
    for q in qids:
        item = items.get(q)
        if not item or "redirect" in item or not wd.is_structure(item, closure):
            continue
        pts = wd.coords(item)
        labels = item.get("labels", {})
        if not pts or not labels:
            continue
        nm = {k: labels[k] for k in ("tr", "en") if k in labels}
        if not nm:
            nm = {"local": next(iter(labels.values()))}
        lat, lon = pts[0]
        rec = {"id": "wd-" + q.lower(), "layer": "open", "type": "yes", "names": nm, "priority": 2,
               "osm": None, "qid": q, "point": (lat, lon), "rep": (lat, lon), "ar": False, "list": True,
               "flags": ["wikidata_only"]}
        out.append(rec)
    return out, items


def cutoff(years):
    return datetime.date.today().year - years
