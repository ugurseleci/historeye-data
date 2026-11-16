"""Wikidata: öğe verisi (wbgetentities), sınıf kapanışı (QLever), eşleşme doğrulaması ve kart alanları.

Dump kullanılmaz (tasarım §14.1): yalnızca OSM'den gelen QID'ler toplu API ile çekilir; sınıf
kapanışı ücretsiz QLever SPARQL servisinden alınır. Her şey diskte önbelleğe yazılır; servis
düşerse önceki veriyle devam edilir.
"""

import json
import math
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "HistorEye-data/0.1 (+https://github.com/ugurseleci/historeye-data)"
WBGET = "https://www.wikidata.org/w/api.php"
QLEVER = "https://qlever.cs.uni-freiburg.de/api/wikidata"

Q_STRUCTURE = "Q811979"  # architectural structure
Q_BUILDING = "Q41176"
Q_SETTLEMENT = "Q486972"  # human settlement
Q_PROTECTED_AREA = "Q473972"
Q_ORGANIZATION = "Q43229"
Q_RELIGIOUS_COMMUNITY = "Q16970"
Q_MUSEUM = "Q33506"  # müze olan tarihi yapı (Galata Kulesi) kurum sayılıp tarihi gizlenmesin
Q_UNESCO_WHS = "Q9259"
Q_CIRCA = "Q5727902"
CLASS_ROOTS = (Q_STRUCTURE, Q_BUILDING, Q_SETTLEMENT, Q_PROTECTED_AREA, Q_ORGANIZATION, Q_RELIGIOUS_COMMUNITY,
               Q_MUSEUM)


def _http_json(url, data=None, accept="application/json", tries=5):
    body = urllib.parse.urlencode(data).encode() if data else None
    for attempt in range(tries):
        req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT, "Accept": accept})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < tries - 1:
                time.sleep(int(e.headers.get("Retry-After") or 2 ** (attempt + 2)))
                continue
            raise
        except urllib.error.URLError:
            if attempt < tries - 1:
                time.sleep(2 ** (attempt + 2))
                continue
            raise
    raise RuntimeError("ulaşılamadı: " + url)


def _load(path):
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def _save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False)
    os.replace(tmp, path)


def _slim(entity):
    """API cevabından yalnızca gereken alanlar (önbellek küçük kalsın)."""
    keep = ("P31", "P571", "P1619", "P84", "P149", "P1435", "P625", "P18")
    claims = {p: entity.get("claims", {}).get(p, []) for p in keep if p in entity.get("claims", {})}
    return {
        "labels": {k: v["value"] for k, v in entity.get("labels", {}).items()},
        "aliases": {k: [a["value"] for a in v] for k, v in entity.get("aliases", {}).items()},
        "claims": claims,
        "sitelinks": {k: v["title"] for k, v in entity.get("sitelinks", {}).items() if k in ("trwiki", "enwiki")},
        "sitelink_count": len(entity.get("sitelinks", {})),
    }


def _api_entities(batch, props, log, tries=8):
    """wbgetentities; API hatası HTTP 200 ile döner. maxlag'de bekleyip yeniden dener, diğer hatada durur.

    Hata cevabını "öğe yok" saymak önbelleği zehirler (P0'da yaşandı): bu yüzden burada açıkça ayrılır.
    """
    # maxlag gönderilmez: bu iş yalnızca okur. maxlag yazma yapan botların çoğaltma ve sorgu servisi
    # gecikmesini artırmaması için önerilir; okuma isteklerimiz o gecikmeyi etkilemez. Nezaket kuralları
    # yine geçerli: istekler sırayla, aralarında bekleme, tanıtıcı User-Agent. Sunucu maxlag hatası
    # dönerse (başka sebeple) aşağıdaki bekle-yeniden dene dalı yine çalışır.
    params = {"action": "wbgetentities", "ids": "|".join(batch), "props": props, "format": "json"}
    for attempt in range(tries):
        res = _http_json(WBGET, params)
        err = res.get("error")
        if not err:
            if "entities" not in res:
                raise RuntimeError(f"beklenmeyen API cevabı: {str(res)[:200]}")
            return res
        if err.get("code") == "maxlag" and attempt < tries - 1:
            wait = max(5, int(float(err.get("lag", 5))) + 1) * (attempt + 1)
            log(f"  wikidata maxlag ({err.get('lag')} sn), {wait} sn bekleniyor")
            time.sleep(wait)
            continue
        raise RuntimeError(f"Wikidata API hatası: {err.get('code')}: {err.get('info')}")
    raise RuntimeError("Wikidata API: maxlag sürüyor, daha sonra yeniden çalıştırın")


def fetch_entities(qids, cache_path, props="labels|aliases|claims|sitelinks", pause_s=1.0, log=print):
    """QID → sade öğe. Önbellekte olmayanlar 50'şerli toplu istekle çekilir."""
    cache = _load(cache_path)
    todo = sorted(q for q in set(qids) if q not in cache and re.fullmatch(r"Q[0-9]+", q))
    for i in range(0, len(todo), 50):
        batch = todo[i:i + 50]
        res = _api_entities(batch, props, log)
        for q in batch:
            ent = res["entities"].get(q)
            if ent is None:
                raise RuntimeError(f"API cevabında {q} yok")
            if "missing" in ent:
                cache[q] = None  # yalnızca API açıkça "yok" dediğinde
            elif "redirects" in ent:
                cache[q] = {"redirect": ent["redirects"]["to"]}
            else:
                cache[q] = _slim(ent) if props.startswith("labels|aliases|claims") else \
                    {"labels": {k: v["value"] for k, v in ent.get("labels", {}).items()}}
        if (i // 50) % 10 == 0:
            log(f"  wikidata: {min(i + 50, len(todo))}/{len(todo)}")
        _save(cache_path, cache)
        time.sleep(pause_s)
    return cache


def class_closure(cache_path, log=print):
    """Her kök sınıf için alt sınıf kümesi (P279*), QLever'den. Önbellekten okunur."""
    cache = _load(cache_path)
    for root in CLASS_ROOTS:
        if root in cache:
            continue
        q = f"PREFIX wd: <http://www.wikidata.org/entity/> PREFIX wdt: <http://www.wikidata.org/prop/direct/> " \
            f"SELECT ?c WHERE {{ ?c wdt:P279* wd:{root} }}"
        res = _http_json(QLEVER + "?" + urllib.parse.urlencode({"query": q}),
                         accept="application/sparql-results+json")
        cache[root] = sorted(b["c"]["value"].rsplit("/", 1)[1] for b in res["results"]["bindings"])
        log(f"  sınıf kapanışı {root}: {len(cache[root])}")
        _save(cache_path, cache)
        time.sleep(1)
    return {k: set(v) for k, v in cache.items()}


# --- Öğe alanları ---------------------------------------------------------------


def _statements(item, prop):
    """Deprecated olmayan ifadeler; preferred varsa yalnızca onlar."""
    st = [s for s in item.get("claims", {}).get(prop, []) if s.get("rank") != "deprecated"]
    pref = [s for s in st if s.get("rank") == "preferred"]
    return pref or st


def _value(s):
    snak = s.get("mainsnak", {})
    if snak.get("snaktype") != "value":
        return None
    return snak.get("datavalue", {}).get("value")


def item_ids(item, prop):
    out = []
    for s in _statements(item, prop):
        v = _value(s)
        if isinstance(v, dict) and v.get("id"):
            out.append(v["id"])
    return out


def coords(item):
    out = []
    for s in _statements(item, "P625"):
        v = _value(s)
        if isinstance(v, dict) and "latitude" in v and (v.get("globe", "").endswith("Q2") or not v.get("globe")):
            out.append((v["latitude"], v["longitude"]))
    return out


def _time(s):
    v = _value(s)
    if not isinstance(v, dict) or "time" not in v:
        return None
    m = re.match(r"^([+-])(\d+)-", v["time"])
    if not m:
        return None
    year = int(m.group(2)) * (-1 if m.group(1) == "-" else 1)
    circa = any(q.get("datavalue", {}).get("value", {}).get("id") == Q_CIRCA
                for q in s.get("qualifiers", {}).get("P1480", []))
    return {"year": year, "precision": v.get("precision", 9), "circa": circa}


def period(item):
    """P571 (başlangıç) → kart dönemi (tasarım §6). Kaba hassasiyet (< 6) gösterilmez."""
    times = [t for t in (_time(s) for s in _statements(item, "P571")) if t and t["precision"] >= 6]
    if not times:
        return None
    t = min(times, key=lambda x: x["year"])  # preferred yoksa en erken
    out = {"start": t["year"], "precision": t["precision"], "circa": t["circa"]}
    opening = [x for x in (_time(s) for s in _statements(item, "P1619")) if x and x["precision"] >= 9]
    if t["precision"] >= 9 and opening and opening[0]["year"] >= t["year"]:
        out["end"] = opening[0]["year"]
    return out


def classes(item):
    return set(item_ids(item, "P31"))


def is_structure(item, closure):
    c = classes(item)
    if c & (closure[Q_STRUCTURE] | closure[Q_BUILDING]):
        return True
    return False


def is_excluded_class(item, closure):
    """Yerleşim ya da koruma alanı: yapı olsa bile tek yapı olarak alınmaz."""
    c = classes(item)
    return bool(c & (closure[Q_SETTLEMENT] | closure[Q_PROTECTED_AREA])) and not (c & closure[Q_BUILDING])


def is_institution(item, closure):
    """Yapı olmayan bir kurum / topluluk sınıfı da taşıyan öğe (vakıf, tarikat…): tarih gösterilmez.

    Wikidata'nın sınıf ağacında "cami", "müze" gibi yapı sınıfları da "kuruluş"un alt sınıfıdır;
    bunları kurum saymak bütün camilerin tarihini gizler (P0'da yaşandı). Bu yüzden yalnızca yapı
    kapanışında olmayan kurum sınıfları sayılır.
    """
    structure = closure[Q_STRUCTURE] | closure[Q_BUILDING] | closure.get(Q_MUSEUM, set())
    org = closure[Q_ORGANIZATION] | closure[Q_RELIGIOUS_COMMUNITY]
    return any(c in org and c not in structure for c in classes(item))


def is_historic(item, cutoff_year):
    if item_ids(item, "P1435"):
        return True
    p = period(item)
    return bool(p and p["start"] <= cutoff_year)


# --- Eşleşme doğrulaması ----------------------------------------------------------

# Tür sözcükleri SİLİNMEZ, diller arası ortak bir türe eşlenir: "Süleymaniye Camii" ile
# "Süleymaniye Mosque" eşleşir ama "Süleymaniye Hamamı" eşleşmez (aynı külliyedeki komşu yapıya
# yanlış QID bağlanmışsa yakalanır; P0'da tür sözcükleri silindiği için bu fark kayboluyordu).
_TYPE_GROUPS = {
    "mosque": "cami camii camisi mescit mescidi mosque",
    "church": "kilise kilisesi church chapel sapel şapel",
    "synagogue": "sinagog sinagogu synagogue",
    "palace": "saray sarayı palace",
    "pavilion": "köşk köşkü kasır kasrı pavilion kiosk",
    "tower": "kule kulesi tower",
    "tomb": "türbe türbesi tomb mausoleum",
    "fountain": "çeşme çeşmesi sebil sebili fountain",
    "bath": "hamam hamamı bath baths hammam",
    "inn": "han hanı kervansaray kervansarayı inn caravanserai",
    "madrasa": "medrese medresesi madrasa",
    "complex": "külliye külliyesi complex",
    "castle": "kale kalesi hisar hisarı castle fortress fort",
    "gate": "kapı kapısı gate",
    "mansion": "yalı yalısı konak konağı mansion",
    "lodge": "tekke tekkesi dergah dergahı lodge",
    "museum": "müze müzesi museum",
    "monument": "anıt anıtı monument memorial",
    "cistern": "sarnıç sarnıcı cistern",
    "aqueduct": "kemer kemeri aqueduct",
    "school": "mektep mektebi okul okulu school",
}
_STOP_WORDS = {"ve", "ile", "of", "the", "and", "de", "da"}


def _fold(w):
    w = w.replace("İ", "i").replace("I", "ı").lower()
    w = unicodedata.normalize("NFKD", w)
    return "".join(ch for ch in w if not unicodedata.combining(ch)).replace("ı", "i")


_TYPE_CANON = {_fold(w): "T:" + canon for canon, words in _TYPE_GROUPS.items() for w in words.split()}


def _norm_tokens(name):
    toks = set()
    for t in re.findall(r"[a-z0-9]+", _fold(name)):
        if t in _STOP_WORDS:
            continue
        toks.add(_TYPE_CANON.get(t, t))
    return toks


def names_match(osm_names, item, threshold=0.5):
    """OSM adlarından biri ile Wikidata etiket/takma adlarından biri: token Jaccard ≥ eşik."""
    wd_names = list(item.get("labels", {}).values())
    for v in item.get("aliases", {}).values():
        wd_names.extend(v)
    a_sets = [t for t in (_norm_tokens(n) for n in osm_names) if t]
    b_sets = [t for t in (_norm_tokens(n) for n in wd_names) if t]
    for a in a_sets:
        for b in b_sets:
            shared = a & b
            if not any(not t.startswith("T:") for t in shared):
                continue  # yalnızca tür sözcüğü ortak ("Cami" ↔ "Cami") eşleşme sayılmaz
            if len(shared) / len(a | b) >= threshold:
                return True
    return False


def heritage_level(item):
    ids = item_ids(item, "P1435")
    if not ids:
        return None
    return 1 if Q_UNESCO_WHS in ids else 2


def image_file(item):
    for s in _statements(item, "P18"):
        v = _value(s)
        if isinstance(v, str):
            return v
    return None


def sitelink_score(item):
    return min(2, int(math.log2(item.get("sitelink_count", 0) + 1) / 2))
