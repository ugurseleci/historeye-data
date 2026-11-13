"""Aday seçimi ve kalite kapısı (tasarım §2.1, §4). Yalnızca OSM; Wikidata eşleşmesi ayrı adım.

Her OSM öğesi şu yapıya çevrilmiş olarak gelir (osm_read.py):
    {"osm": "w123", "tags": {...}, "kind": "point" | "polygon" | "line",
     "point": (lat, lon),               # nokta; çizgi ve poligonda temsil noktası
     "outers": [[(lat, lon), ...], ...]}  # yalnızca poligon; iç halkalar atılmış

Çıktı: kayıt listesi (multipolygon'un her dış halkası ayrı kayıt) ve atılanlar için sebep.
"""

import re

from shapely.geometry import Polygon
from shapely.validation import make_valid

from .geo import from_enu, to_enu

# Yapı sayılan historic değerleri (poligon olarak AR'a girebilir).
STRUCTURE_HISTORIC = {
    "castle", "church", "monastery", "mosque", "monument", "memorial", "ruins", "city_gate", "tower",
    "palace", "fort", "building", "manor", "citywalls", "aqueduct", "bridge", "fountain", "tomb",
    "wayside_shrine", "wayside_cross", "mausoleum", "cistern", "hammam", "bath", "caravanserai",
    "synagogue", "temple", "chapel", "lighthouse", "house", "mansion", "school", "yes",
}
# Yalnızca liste: yapı değil ya da konumu/ölçeği AR için uygun değil.
LIST_ONLY_HISTORIC = {"district", "milestone", "boundary_stone", "railway", "wreck", "battlefield",
                      "archaeological_site", "highwater_mark"}
# Yapı değil (müze nesnesi, araç): hiç alınmaz.
NOT_STRUCTURE_OBJECTS = {"aircraft", "locomotive", "ship", "tank", "cannon", "vehicle", "railway_car",
                         "anchor", "machine"}
# Nokta + yarıçap olarak AR'a girebilen küçük yapılar ve varsayılan yarıçap (m), tasarım §4.
SMALL_POINT_RADIUS = {
    "fountain": 3.0, "wayside_shrine": 1.5, "wayside_cross": 1.5, "memorial": 2.0,
    "statue": 1.5, "bust": 1.5, "obelisk": 2.0, "column": 2.0,
}
NOT_AR_MEMORIAL = {"plaque", "stolperstein", "blue_plaque", "stele"}

MIN_AREA_M2 = 20.0
MAX_AREA_M2 = 50_000.0
MAX_EXTENT_M = 300.0
MAX_VERTICES = 128
DP_TOLERANCES_M = (0.5, 1.0, 2.0, 3.0)
METERS_PER_LEVEL = 3.2

_HEIGHT_RE = re.compile(r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*(m|meter|meters|metre|metres)?\s*$", re.I)


def parse_height_m(value):
    """OSM `height` → metre; anlaşılmayan biçim (ft, aralık…) None."""
    if not value:
        return None
    m = _HEIGHT_RE.match(value)
    if not m:
        return None
    h = float(m.group(1).replace(",", "."))
    return h if 0 < h < 1000 else None


def parse_levels(value):
    try:
        n = float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None
    return n if 0 < n < 200 else None


def label_height_from_estimate(est):
    """Kat sayısından tahmin: yalnızca etiket yüksekliği (conventions §6 clamp kuralı)."""
    return max(6.0, min(est * 0.7, est)) if est >= 6.0 else est


def names(tags):
    out = {}
    for lang in ("tr", "en"):
        v = tags.get(f"name:{lang}")
        if v:
            out[lang] = v.strip()
    if tags.get("name"):
        out["local"] = tags["name"].strip()
    return out


def heritage_level(tags):
    """OSM `heritage=1` UNESCO, 2 ulusal, 3+ bölgesel/yerel; bilinmiyorsa None."""
    v = tags.get("heritage")
    if v and v.isdigit():
        return int(v)
    return None


def historic_type(tags):
    h = tags.get("historic")
    if h:
        return h
    if tags.get("building:historic") or tags.get("heritage"):
        return "yes"
    return None


def priority(tags, htype):
    score = 2
    level = heritage_level(tags)
    if level == 1:
        score += 4
    elif level == 2:
        score += 3
    elif level is not None:
        score += 1
    if htype in {"mosque", "church", "palace", "castle", "fort", "monastery", "synagogue", "citywalls"}:
        score += 2
    elif htype in {"tower", "city_gate", "aqueduct", "tomb", "mausoleum", "hammam", "caravanserai", "cistern"}:
        score += 1
    if tags.get("wikidata"):
        score += 1
    if tags.get("wikipedia"):
        score += 1
    return min(score, 8)  # 9 küratörlü kayıtlara ayrılmıştır


def _simplify(ring_ll):
    """Halka (lat, lon) → onarılmış, sadeleştirilmiş halka ve metrikler; olmazsa (None, sebep)."""
    lat0 = sum(p[0] for p in ring_ll) / len(ring_ll)
    lon0 = sum(p[1] for p in ring_ll) / len(ring_ll)
    enu = [to_enu(lat0, lon0, lat, lon) for lat, lon in ring_ll]
    poly = Polygon(enu)
    if not poly.is_valid:
        fixed = make_valid(poly)
        polys = [g for g in getattr(fixed, "geoms", [fixed]) if g.geom_type == "Polygon"]
        if not polys:
            return None, "invalid_geometry", None
        poly = max(polys, key=lambda g: g.area)
    poly = Polygon(poly.exterior)  # iç halkalar atılır (tasarım §4 geometri sözleşmesi)
    area = poly.area
    minx, miny, maxx, maxy = poly.bounds
    extent = max(maxx - minx, maxy - miny)
    simplified = None
    for tol in DP_TOLERANCES_M:
        s = poly.simplify(tol, preserve_topology=True)
        if s.is_valid and not s.is_empty and len(s.exterior.coords) - 1 <= MAX_VERTICES:
            simplified = s
            break
    metrics = {"area_m2": area, "extent_m": extent}
    if simplified is None:
        return None, "too_many_vertices", metrics
    coords = list(simplified.exterior.coords)[:-1]
    if simplified.exterior.is_ccw is False:
        coords.reverse()  # saat yönünün tersi (normalize)
    rep = poly.representative_point()
    ring = [from_enu(lat0, lon0, e, n) for e, n in coords]
    metrics["rep"] = from_enu(lat0, lon0, rep.x, rep.y)
    return ring, None, metrics


def type_from_osm(tags):
    """Tarihi etiketi olmayan, Wikidata'dan yapı olduğu anlaşılan öğe için tür adı."""
    b = tags.get("building")
    if b in {"mosque", "church", "synagogue", "palace", "temple", "chapel", "cathedral", "monastery", "tower",
             "castle", "mansion", "house", "school", "bridge"}:
        return {"cathedral": "church"}.get(b, b)
    if tags.get("amenity") == "place_of_worship":
        return {"muslim": "mosque", "christian": "church", "jewish": "synagogue"}.get(tags.get("religion"), "temple")
    if tags.get("man_made") in {"tower", "lighthouse", "bridge"}:
        return tags["man_made"]
    return "building" if b and b != "no" else "yes"


def classify(feature, htype=None):
    """Bir OSM öğesinden kayıtlar ve (atıldıysa) sebep döndürür: (kayıtlar, sebep).

    htype verilirse (Wikidata'dan tarihi yapı olduğu doğrulanmış öğe) OSM etiketlerinin yerine kullanılır.
    """
    tags = feature["tags"]
    htype = htype or historic_type(tags)
    if htype is None:
        return [], "not_candidate"
    if htype in NOT_STRUCTURE_OBJECTS:
        return [], "object_not_structure"
    nm = names(tags)
    if not nm:
        return [], "no_name"

    base = {
        "layer": "open",
        "type": htype,
        "names": nm,
        "priority": priority(tags, htype),
        "osm": feature["osm"],
        "flags": [],
    }
    if tags.get("wikidata"):
        base["qid"] = tags["wikidata"].split(";")[0].strip()
        base["flags"].append("wd_unverified")  # Wikidata eşleşmesi henüz yapılmadı (P0, yalnızca OSM)
    if tags.get("heritage"):
        base["heritage"] = tags["heritage"]
    height = parse_height_m(tags.get("height"))
    levels = parse_levels(tags.get("building:levels"))
    if height is not None:
        base["height_m"] = height
    elif levels is not None:
        est = levels * METERS_PER_LEVEL
        base["label_height_m"] = round(label_height_from_estimate(est), 2)
        base["height_est_m"] = round(est, 2)  # yalnızca demo siluetleri; occlusion'a girmez

    memorial_kind = tags.get("memorial") or tags.get("monument")
    list_only_reason = None
    if htype in LIST_ONLY_HISTORIC:
        list_only_reason = "list_only_type"
    if htype == "memorial" and memorial_kind in NOT_AR_MEMORIAL:
        list_only_reason = "small_plaque"

    if feature["kind"] == "polygon":
        records = []
        outers = feature["outers"]
        is_building = bool(tags.get("building")) and tags.get("building") != "no"
        for i, ring_ll in enumerate(outers):
            rid = feature["osm"] if len(outers) == 1 else f"{feature['osm']}-{i + 1}"
            rec = dict(base, id="osm-" + rid, flags=list(base["flags"]))
            ring, err, metrics = _simplify(ring_ll) if len(ring_ll) >= 3 else (None, "invalid_geometry", None)
            if ring is None:
                lat = sum(p[0] for p in ring_ll) / len(ring_ll)
                lon = sum(p[1] for p in ring_ll) / len(ring_ll)
                rec.update(point=(lat, lon), rep=(lat, lon), ar=False, list=True)
                rec["flags"].append(err)
                records.append(rec)
                continue
            rec.update(footprint=ring, rep=metrics["rep"], area_m2=round(metrics["area_m2"], 1),
                       extent_m=round(metrics["extent_m"], 1), list=True)
            reason = list_only_reason
            if reason is None and not (is_building or htype in STRUCTURE_HISTORIC):
                reason = "not_structure"
            if reason is None and metrics["area_m2"] < MIN_AREA_M2:
                reason = "too_small"
            if reason is None and metrics["area_m2"] > MAX_AREA_M2:
                reason = "area_mode"
            if reason is None and metrics["extent_m"] > MAX_EXTENT_M:
                reason = "area_mode"
            if reason is None and tags.get("landuse"):
                reason = "area_mode"
            rec["ar"] = reason is None
            if reason:
                rec["flags"].append(reason)
            records.append(rec)
        return records, None

    # Nokta ya da çizgi (çizgi: kale suru, su kemeri → temsil noktasıyla yalnızca liste)
    rec = dict(base, id="osm-" + feature["osm"], point=feature["point"], rep=feature["point"],
               flags=list(base["flags"]), list=True)
    if feature["kind"] == "line":
        rec["ar"] = False
        rec["flags"].append("line_geometry")
        return [rec], None
    radius = None
    if list_only_reason is None:
        if htype == "fountain" or tags.get("amenity") == "fountain":
            radius = SMALL_POINT_RADIUS["fountain"]
        elif htype in ("wayside_shrine", "wayside_cross"):
            radius = SMALL_POINT_RADIUS[htype]
        elif htype == "memorial" and memorial_kind in ("statue", "bust", "obelisk", "column"):
            # Alt türü belirsiz memorial (türbe, saat kulesi olarak da etiketlenebiliyor) yalnızca liste.
            radius = SMALL_POINT_RADIUS[memorial_kind]
        elif htype == "monument" and memorial_kind in ("statue", "bust", "obelisk", "column"):
            radius = SMALL_POINT_RADIUS[memorial_kind]
    if radius is None:
        rec["ar"] = False
        rec["flags"].append(list_only_reason or "large_type_as_point")
    else:
        rec["ar"] = True
        rec["radius_m"] = radius
    return [rec], None
