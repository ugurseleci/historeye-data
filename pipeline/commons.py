"""Wikimedia Commons: görsel lisansı ve atıf (tasarım §6.1). Görselin kendisi indirilmez.

Kart, görseli uygulama Worker'ı üzerinden anlık çeker; hat yalnızca dosya adını, boyutu ve
lisans/atıf metnini karoya yazar. Lisans süzgecini geçmeyen ya da atfı boş görsel karoya girmez.
"""

import html
import json
import os
import re
import time

from . import wikidata as wd

API = "https://commons.wikimedia.org/w/api.php"

# Kabul: kamu malı, CC0, CC BY, CC BY-SA, Free Art License (FAL; BY-SA benzeri, atıf zorunlu) ve
# yalnızca atıf isteyen "Attribution" şablonu. GFDL kabul edilmez (tam lisans metni görselle taşınmalı).
# "No restrictions", "Copyrighted free use" gibi belirsiz durumlar şüpheden reddedilir.
_ACCEPT = re.compile(r"^(public domain|pd\b|pd-|cc0|cc[ -]by(-sa)?[ -]?\d|fal\b|free art licen[cs]e|attribution$)", re.I)
_REJECT_HINT = re.compile(r"fair use|non-?free|copyrighted|all rights reserved", re.I)


def _strip_html(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _api(titles, log, tries=6):
    params = {"action": "query", "titles": "|".join("File:" + t for t in titles), "prop": "imageinfo",
              "iiprop": "extmetadata|size", "iiextmetadatafilter":
              "LicenseShortName|License|LicenseUrl|Artist|Credit|AttributionRequired|NonFree|Restrictions|UsageTerms",
              "format": "json", "formatversion": "2"}
    for attempt in range(tries):
        res = wd._http_json(API, params)
        err = res.get("error")
        if not err:
            if "query" not in res:
                raise RuntimeError(f"beklenmeyen Commons cevabı: {str(res)[:200]}")
            return res["query"]
        if err.get("code") == "maxlag" and attempt < tries - 1:
            wait = max(5, int(float(err.get("lag", 5))) + 1) * (attempt + 1)
            log(f"  commons maxlag, {wait} sn bekleniyor")
            time.sleep(wait)
            continue
        raise RuntimeError(f"Commons API hatası: {err.get('code')}: {err.get('info')}")
    raise RuntimeError("Commons API: yeniden deneme sınırı")


def fetch(files, cache_path, pause_s=1.0, log=print):
    """Dosya adı → sade lisans bilgisi (ya da None: dosya yok). Önbellekli, 50'şerli."""
    cache = wd._load(cache_path)
    todo = sorted({f for f in files if f and f not in cache})
    for i in range(0, len(todo), 50):
        batch = todo[i:i + 50]
        q = _api(batch, log)
        alias = {}
        for n in q.get("normalized", []):
            alias[n["to"]] = n["from"]
        for r in q.get("redirects", []):
            alias[r["to"]] = alias.get(r["from"], r["from"])
        seen = set()
        for page in q.get("pages", []):
            title = page["title"]
            orig = alias.get(title, title)
            name = orig[5:] if orig.startswith("File:") else orig
            seen.add(name)
            if page.get("missing") or not page.get("imageinfo"):
                cache[name] = None
                continue
            ii = page["imageinfo"][0]
            meta = {k: v.get("value") for k, v in ii.get("extmetadata", {}).items()}
            cache[name] = {"w": ii.get("width"), "h": ii.get("height"),
                           "license": _strip_html(meta.get("LicenseShortName")),
                           "license_url": meta.get("LicenseUrl"),
                           "author": _strip_html(meta.get("Artist")) or _strip_html(meta.get("Credit")),
                           "attribution_required": str(meta.get("AttributionRequired", "")).lower() == "true",
                           "non_free": str(meta.get("NonFree", "")).lower() == "true",
                           "restrictions": _strip_html(meta.get("Restrictions")),
                           "usage_terms": _strip_html(meta.get("UsageTerms"))}
        missing = set(batch) - seen
        if missing:
            raise RuntimeError(f"Commons cevabında olmayan dosyalar: {sorted(missing)[:3]}")
        wd._save(cache_path, cache)
        if (i // 50) % 5 == 0:
            log(f"  commons: {min(i + 50, len(todo))}/{len(todo)}")
        time.sleep(pause_s)
    return cache


def decide(info):
    """(kabul_mü, sebep). Şüphede reddeder: atıfsız ya da lisansı belirsiz görsel gösterilmez."""
    if info is None:
        return False, "file_missing"
    if info["non_free"]:
        return False, "non_free"
    lic = info["license"] or ""
    if _REJECT_HINT.search(lic) or _REJECT_HINT.search(info.get("usage_terms") or ""):
        return False, "non_free"
    if not _ACCEPT.match(lic):
        return False, "license_unknown"
    if "personality" in (info.get("restrictions") or "").lower():
        return False, "personality_rights"
    is_pd = lic.lower().startswith(("public domain", "pd", "cc0"))
    if not is_pd and not info["author"]:
        return False, "no_attribution"
    return True, None


def source_url(file_name):
    return "https://commons.wikimedia.org/wiki/File:" + file_name.replace(" ", "_")


def apply(records, cache, log=print):
    stats = {}
    for r in records:
        img = r.get("image")
        if not img:
            continue
        info = cache.get(img["file"])
        ok, reason = decide(info)
        if not ok:
            del r["image"]
            r["flags"].append("image_" + reason)
            stats[reason] = stats.get(reason, 0) + 1
            continue
        r["image"] = {"file": img["file"], "w": info["w"], "h": info["h"], "license": info["license"],
                      "license_url": info["license_url"], "author": info["author"][:200] or None,
                      "source_url": source_url(img["file"])}
        stats["accepted"] = stats.get("accepted", 0) + 1
    return stats
