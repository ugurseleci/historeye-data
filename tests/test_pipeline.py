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
        self.assertLessEqual(len(recs[0]["footprint"]), 64)

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
