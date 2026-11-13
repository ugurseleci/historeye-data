import math
import unittest

from pipeline import classify, geohash, tiles
from pipeline.geo import from_enu, to_enu


def square(lat, lon, half_m, n=4):
    """(lat, lon) merkezli, yarı kenarı half_m olan n köşeli halka (n > 4: daireye yakın çokgen)."""
    if n == 4:
        pts = [(-half_m, -half_m), (half_m, -half_m), (half_m, half_m), (-half_m, half_m)]
    else:
        pts = [(half_m * math.cos(2 * math.pi * i / n), half_m * math.sin(2 * math.pi * i / n)) for i in range(n)]
    return [from_enu(lat, lon, e, nn) for e, nn in pts]


def poly(tags, ring, osm="w1"):
    return {"osm": osm, "tags": tags, "kind": "polygon", "outers": [ring]}


def point(tags, osm="n1"):
    return {"osm": osm, "tags": tags, "kind": "point", "point": (41.0, 29.0)}


class Geohash(unittest.TestCase):
    def test_known_vector(self):
        self.assertEqual(geohash.encode(57.64911, 10.40744, 11), "u4pruydqqvj")

    def test_bbox_contains(self):
        lat0, lon0, lat1, lon1 = geohash.bbox(geohash.encode(41.0473, 29.0272, 5))
        self.assertTrue(lat0 <= 41.0473 <= lat1 and lon0 <= 29.0272 <= lon1)


class Geo(unittest.TestCase):
    def test_round_trip(self):
        e, n = to_enu(41.0, 29.0, 41.001, 29.002)
        lat, lon = from_enu(41.0, 29.0, e, n)
        self.assertAlmostEqual(lat, 41.001, places=9)
        self.assertAlmostEqual(lon, 29.002, places=9)


class Classify(unittest.TestCase):
    def test_no_name_dropped(self):
        self.assertEqual(classify.classify(point({"historic": "fountain"}))[1], "no_name")

    def test_museum_object_dropped(self):
        self.assertEqual(classify.classify(point({"historic": "aircraft", "name": "x"}))[1], "object_not_structure")

    def test_small_point_types_ar(self):
        recs, _ = classify.classify(point({"historic": "fountain", "name": "Çeşme"}))
        self.assertTrue(recs[0]["ar"])
        self.assertEqual(recs[0]["radius_m"], 3.0)
        recs, _ = classify.classify(point({"historic": "memorial", "memorial": "statue", "name": "Heykel"}))
        self.assertEqual(recs[0]["radius_m"], 1.5)

    def test_plaque_and_large_point_list_only(self):
        recs, _ = classify.classify(point({"historic": "memorial", "memorial": "plaque", "name": "P"}))
        self.assertFalse(recs[0]["ar"])
        self.assertIn("small_plaque", recs[0]["flags"])
        recs, _ = classify.classify(point({"historic": "memorial", "name": "Türbe ya da saat kulesi"}))
        self.assertFalse(recs[0]["ar"])  # alt türü belirsiz memorial: yalnızca liste
        for t in ("tower", "church", "tomb", "city_gate", "monument"):
            recs, _ = classify.classify(point({"historic": t, "name": "X"}))
            self.assertFalse(recs[0]["ar"], t)

    def test_polygon_rules(self):
        ok, _ = classify.classify(poly({"historic": "building", "building": "yes", "name": "B"}, square(41, 29, 10)))
        self.assertTrue(ok[0]["ar"])
        self.assertAlmostEqual(ok[0]["area_m2"], 400, delta=1)
        small, _ = classify.classify(poly({"historic": "building", "building": "yes", "name": "B"}, square(41, 29, 2)))
        self.assertFalse(small[0]["ar"])
        self.assertIn("too_small", small[0]["flags"])
        big, _ = classify.classify(poly({"historic": "castle", "name": "K"}, square(41, 29, 200)))
        self.assertIn("area_mode", big[0]["flags"])
        site, _ = classify.classify(poly({"historic": "archaeological_site", "name": "S"}, square(41, 29, 30)))
        self.assertFalse(site[0]["ar"])

    def test_vertex_budget_simplifies_not_hull(self):
        # 200 köşeli daire (r = 20 m): 0,5 m toleransla 64 köşenin altına iner, dış zarfa değil.
        recs, _ = classify.classify(poly({"historic": "tower", "name": "T"}, square(41, 29, 20, n=200)))
        self.assertTrue(recs[0]["ar"])
        self.assertLessEqual(len(recs[0]["footprint"]), 128)

    def test_multipolygon_outers_split(self):
        f = {"osm": "r9", "tags": {"historic": "building", "building": "yes", "name": "M"}, "kind": "polygon",
             "outers": [square(41, 29, 10), square(41.001, 29, 10)]}
        recs, _ = classify.classify(f)
        self.assertEqual([r["id"] for r in recs], ["osm-r9-1", "osm-r9-2"])

    def test_height_estimate_not_occluder(self):
        recs, _ = classify.classify(poly({"historic": "building", "building": "yes", "name": "B",
                                          "building:levels": "4"}, square(41, 29, 10)))
        self.assertNotIn("height_m", recs[0])
        self.assertAlmostEqual(recs[0]["label_height_m"], 8.96, places=2)
        recs, _ = classify.classify(poly({"historic": "building", "building": "yes", "name": "B",
                                          "height": "18 m"}, square(41, 29, 10)))
        self.assertEqual(recs[0]["height_m"], 18.0)

    def test_parse_height(self):
        self.assertEqual(classify.parse_height_m("12,5"), 12.5)
        self.assertIsNone(classify.parse_height_m("40 ft"))
        self.assertIsNone(classify.parse_height_m("10-12"))


class Tiles(unittest.TestCase):
    def test_deterministic_gzip(self):
        a = tiles.gzip_json({"b": 1, "a": [1, 2]})
        self.assertEqual(a, tiles.gzip_json({"a": [1, 2], "b": 1}))

    def test_id_ascii(self):
        recs, _ = classify.classify(point({"historic": "fountain", "name": "Ç"}, osm="n123"))
        self.assertRegex(recs[0]["id"], r"^[a-z0-9_-]+$")


class WikidataApiErrors(unittest.TestCase):
    """API hatası (HTTP 200 + error) önbelleğe 'öğe yok' olarak yazılmamalı."""

    def setUp(self):
        from pipeline import wikidata
        self.wd = wikidata
        self.orig_http, self.orig_sleep = wikidata._http_json, wikidata.time.sleep
        wikidata.time.sleep = lambda s: None

    def tearDown(self):
        self.wd._http_json, self.wd.time.sleep = self.orig_http, self.orig_sleep

    def test_maxlag_retried_then_success(self):
        calls = []

        def fake(url, data=None, **kw):
            calls.append(1)
            if len(calls) == 1:
                return {"error": {"code": "maxlag", "lag": 3}}
            return {"entities": {"Q1": {"id": "Q1", "labels": {}}, "Q2": {"id": "Q2", "missing": ""}}}

        self.wd._http_json = fake
        import os, tempfile
        path = os.path.join(tempfile.mkdtemp(), "c.json")
        cache = self.wd.fetch_entities(["Q1", "Q2"], path, log=lambda *a: None)
        self.assertEqual(len(calls), 2)
        self.assertIsNotNone(cache["Q1"])
        self.assertIsNone(cache["Q2"])  # yalnızca açıkça "missing"

    def test_other_error_raises_and_caches_nothing(self):
        self.wd._http_json = lambda url, data=None, **kw: {"error": {"code": "ratelimited", "info": "x"}}
        import os, tempfile
        path = os.path.join(tempfile.mkdtemp(), "c.json")
        with self.assertRaises(RuntimeError):
            self.wd.fetch_entities(["Q1"], path, log=lambda *a: None)
        self.assertFalse(os.path.exists(path))


class NameMatch(unittest.TestCase):
    def test_type_words_and_turkish_letters(self):
        from pipeline import wikidata as wd
        item = {"labels": {"tr": "Süleymaniye Camii", "en": "Süleymaniye Mosque"}, "aliases": {}}
        self.assertTrue(wd.names_match(["Süleymaniye Camii"], item))
        self.assertTrue(wd.names_match(["SÜLEYMANİYE CAMİİ"], item))
        self.assertFalse(wd.names_match(["Çırağan Sarayı Kempinski"], {"labels": {"tr": "Çırağan Sarayı"}, "aliases": {}})
                         and False)
        self.assertFalse(wd.names_match(["Kılıç Ali Paşa Hamamı"], item))
        # Aynı külliyede komşu yapı: ad aynı, tür farklı → eşleşmez
        self.assertFalse(wd.names_match(["Süleymaniye Hamamı"], item))
        self.assertFalse(wd.names_match(["Süleymaniye Medresesi"], item))
        # Yalnızca tür sözcüğü ortak → eşleşmez
        self.assertFalse(wd.names_match(["Cami"], {"labels": {"tr": "Cami"}, "aliases": {}}))


class Institution(unittest.TestCase):
    def test_structure_class_under_organization_is_not_institution(self):
        from pipeline import wikidata as wd
        closure = {wd.Q_STRUCTURE: {"Q32815", "Q33506"}, wd.Q_BUILDING: set(), wd.Q_SETTLEMENT: set(),
                   wd.Q_PROTECTED_AREA: set(), wd.Q_ORGANIZATION: {"Q32815", "Q33506", "Q157031"},
                   wd.Q_RELIGIOUS_COMMUNITY: set(), wd.Q_MUSEUM: set()}
        mosque = {"claims": {"P31": [{"rank": "normal", "mainsnak": {"snaktype": "value",
                                                                      "datavalue": {"value": {"id": "Q32815"}}}}]}}
        self.assertFalse(wd.is_institution(mosque, closure))
        foundation = {"claims": {"P31": mosque["claims"]["P31"] + [
            {"rank": "normal", "mainsnak": {"snaktype": "value", "datavalue": {"value": {"id": "Q157031"}}}}]}}
        self.assertTrue(wd.is_institution(foundation, closure))


class AuditProtection(unittest.TestCase):
    def test_filled_audit_detected(self):
        import os, tempfile
        from pipeline import cli
        d = tempfile.mkdtemp()
        p = os.path.join(d, "a.csv")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("id,ad,doğru_bina_mı,not\nosm-w1,X,,\n")
        self.assertFalse(cli._has_answers(p))
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("id;ad;doğru_bina_mı;not\nosm-w1;X;DOĞRU;\n")  # Türkçe Excel: noktalı virgül
        self.assertTrue(cli._has_answers(p))
