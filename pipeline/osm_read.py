"""PBF → aday öğeler. Poligonlar pyosmium alan derlemesiyle (kapalı yollar + multipolygon ilişkileri)."""

import osmium

from .classify import historic_type


class _Handler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.features = []
        self.area_ways = set()  # alan olarak derlenmiş yol kimlikleri (çizgi olarak tekrar eklenmesin)

    def node(self, n):
        tags = dict(n.tags)
        if historic_type(tags) is None or not n.location.valid():
            return
        self.features.append({"osm": f"n{n.id}", "tags": tags, "kind": "point",
                              "point": (n.location.lat, n.location.lon)})

    def way(self, w):
        tags = dict(w.tags)
        if historic_type(tags) is None or w.is_closed():
            return  # kapalı yollar alan olarak gelir
        pts = [(nd.lat, nd.lon) for nd in w.nodes if nd.location.valid()]
        if len(pts) < 2:
            return
        mid = pts[len(pts) // 2]
        self.features.append({"osm": f"w{w.id}", "tags": tags, "kind": "line", "point": mid})

    def area(self, a):
        tags = dict(a.tags)
        if historic_type(tags) is None:
            return
        osm = f"w{a.orig_id()}" if a.from_way() else f"r{a.orig_id()}"
        outers = []
        for outer in a.outer_rings():
            ring = [(nd.lat, nd.lon) for nd in outer if nd.location.valid()]
            if len(ring) > 1 and ring[0] == ring[-1]:
                ring = ring[:-1]
            outers.append(ring)
        if outers:
            self.features.append({"osm": osm, "tags": tags, "kind": "polygon", "outers": outers})


def read(pbf_path):
    h = _Handler()
    h.apply_file(pbf_path, locations=True)
    return h.features
