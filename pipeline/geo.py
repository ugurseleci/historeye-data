"""Yerel ENU (metre) dönüşümü: uygulamadaki GeoCore ile aynı düz yaklaşım (conventions §2).

Bütün metrik hesaplar (alan, boyut, sadeleştirme toleransı) derece üzerinde değil,
kaydın kendi merkezindeki yerel ENU'da yapılır.
"""

import math

EARTH_R = 6_371_000.0


def to_enu(lat0, lon0, lat, lon):
    d_lon = (lon - lon0 + 180.0) % 360.0 - 180.0
    return (
        math.radians(d_lon) * EARTH_R * math.cos(math.radians(lat0)),
        math.radians(lat - lat0) * EARTH_R,
    )


def from_enu(lat0, lon0, e, n):
    lat = lat0 + math.degrees(n / EARTH_R)
    lon = lon0 + math.degrees(e / (EARTH_R * math.cos(math.radians(lat0))))
    return lat, ((lon + 180.0) % 360.0) - 180.0


def e7(x):
    """Derece → 1e-7 derece tamsayı (OSM hassasiyeti)."""
    return int(round(x * 1e7))
