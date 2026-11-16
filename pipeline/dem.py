"""Çıplak zemin kotu ızgarası (tasarım §6): Copernicus GLO-30 + OSM yol ağı.

GLO-30 bir YÜZEY modelidir (çatı, ağaç dahil). Yoğun şehirde çatı kotunu zemin saymamak için
düğüm değeri, penceredeki yol piksellerinin (köprü ve tünel hariç) medyanından alınır; yolu
olmayan pencerede alt %10'luk dilim. Kot EGM2008 ortometriktir (conventions §2 ile aynı).

GLO-30 dosyaları AWS açık veri deposunda bulut uyumlu GeoTIFF'tir: tamamı indirilmez, yalnızca
gereken pencere HTTP aralık istekleriyle okunur (ücretsiz çalıştırıcının küçük diskine uygun).
"""

import base64
import math
import os

import numpy as np

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
os.environ.setdefault("GDAL_HTTP_MULTIRANGE", "YES")

import rasterio  # noqa: E402
from rasterio.enums import Resampling  # noqa: E402
from rasterio.errors import RasterioIOError  # noqa: E402
from rasterio.windows import from_bounds  # noqa: E402

from .geohash import bbox  # noqa: E402

URL = "https://copernicus-dem-30m.s3.amazonaws.com/{0}/{0}.tif"
PX = 1.0 / 3600.0  # hedef ızgara: 1 yay saniyesi (GLO-30'un ekvator çözünürlüğü)


def tile_name(lat_i, lon_i):
    ns = "N" if lat_i >= 0 else "S"
    ew = "E" if lon_i >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(lat_i):02d}_00_{ew}{abs(lon_i):03d}_00_DEM"


def pixel(lat, lon):
    """Küresel hedef ızgarada (satır, sütun)."""
    return int(math.floor((90.0 - lat) / PX)), int(math.floor((lon + 180.0) / PX))


class Dem:
    """1°×1° GLO-30 dosyalarını açık tutar; bir kutuyu 1" hedef ızgarada okur.

    Denizde GLO-30 dosyası yoktur: o bölge 0 m kabul edilir. Yüksek enlemde dosyaların boylam
    aralığı büyür (1,5", 2"…); okuma hedef ızgaraya en yakın komşuyla yeniden örneklenir.
    """

    def __init__(self, url=URL):
        self.url = url
        self._open = {}

    def _ds(self, lat_i, lon_i):
        key = (lat_i, lon_i)
        if key not in self._open:
            try:
                self._open[key] = rasterio.open(self.url.format(tile_name(lat_i, lon_i)))
            except RasterioIOError:
                self._open[key] = None  # deniz
        return self._open[key]

    def read(self, lat_min, lon_min, lat_max, lon_max):
        """Kutu → (dizi, üst satır, sol sütun) küresel 1" ızgarada."""
        r0, c0 = pixel(lat_max, lon_min)
        r1, c1 = pixel(lat_min, lon_max)
        out = np.zeros((r1 - r0 + 1, c1 - c0 + 1), dtype=np.float32)
        for lat_i in range(math.floor(lat_min), math.floor(lat_max) + 1):
            for lon_i in range(math.floor(lon_min), math.floor(lon_max) + 1):
                ds = self._ds(lat_i, lon_i)
                if ds is None:
                    continue
                a_lat0, a_lat1 = max(lat_min, lat_i), min(lat_max, lat_i + 1 - 1e-9)
                a_lon0, a_lon1 = max(lon_min, lon_i), min(lon_max, lon_i + 1 - 1e-9)
                if a_lat0 > a_lat1 or a_lon0 > a_lon1:
                    continue
                pr0, pc0 = pixel(a_lat1, a_lon0)
                pr1, pc1 = pixel(a_lat0, a_lon1)
                shape = (pr1 - pr0 + 1, pc1 - pc0 + 1)
                win = from_bounds(a_lon0, a_lat0, a_lon1, a_lat1, ds.transform)
                vals = ds.read(1, window=win, out_shape=shape, resampling=Resampling.nearest, boundless=True,
                               fill_value=0)
                out[pr0 - r0:pr1 - r0 + 1, pc0 - c0:pc1 - c0 + 1] = vals
        out[~np.isfinite(out)] = 0
        return out, r0, c0


def road_pixels(points):
    """Yol noktaları → küresel piksel kümesi (satır, sütun)."""
    return {pixel(lat, lon) for lat, lon in points}


def ground_grid(dem, gh, roads, cells=32, halo=1):
    """geohash karosu için düğüm ızgarası: (cells + 1 + 2·halo)² düğüm, her kenarda 1 hücre örtüşme."""
    lat0, lon0, lat1, lon1 = bbox(gh)
    dlat, dlon = (lat1 - lat0) / cells, (lon1 - lon0) / cells
    n = cells + 1 + 2 * halo
    g_lat0, g_lon0 = lat0 - halo * dlat, lon0 - halo * dlon
    arr, r0, c0 = dem.read(g_lat0 - dlat / 2, g_lon0 - dlon / 2,
                           g_lat0 + (n - 1) * dlat + dlat / 2, g_lon0 + (n - 1) * dlon + dlon / 2)
    road_mask = np.zeros(arr.shape, dtype=bool)
    for r, c in roads:
        if r0 <= r < r0 + arr.shape[0] and c0 <= c < c0 + arr.shape[1]:
            road_mask[r - r0, c - c0] = True
    values = np.zeros((n, n), dtype=np.float64)
    used_roads = 0
    for i in range(n):  # satır: güneyden kuzeye
        lat = g_lat0 + i * dlat
        for j in range(n):
            lon = g_lon0 + j * dlon
            ra, ca = pixel(lat + dlat / 2, lon - dlon / 2)
            rb, cb = pixel(lat - dlat / 2, lon + dlon / 2)
            sl = (slice(max(ra - r0, 0), max(rb - r0 + 1, 1)), slice(max(ca - c0, 0), max(cb - c0 + 1, 1)))
            win, mask = arr[sl], road_mask[sl]
            if mask.any():
                values[i, j] = float(np.median(win[mask]))
                used_roads += 1
            elif win.size:
                values[i, j] = float(np.percentile(win, 10))
    return encode_grid(values, g_lat0, g_lon0, dlat, dlon), used_roads / (n * n)


def encode_grid(values, lat0, lon0, dlat, dlon):
    base = math.floor(float(values.min()))
    dm = np.clip(np.rint((values - base) * 10), 0, 65535).astype("<u2")
    return {"n_lat": int(values.shape[0]), "n_lon": int(values.shape[1]), "lat0": lat0, "lon0": lon0,
            "dlat": dlat, "dlon": dlon, "base_m": base,
            "dm_b64": base64.b64encode(dm.tobytes()).decode("ascii")}


def decode_grid(g):
    dm = np.frombuffer(base64.b64decode(g["dm_b64"]), dtype="<u2").reshape(g["n_lat"], g["n_lon"])
    return g["base_m"] + dm.astype(np.float64) / 10.0


def sample(g, lat, lon, values=None):
    """Çift doğrusal ara değer (cihazdaki okuyucuyla aynı formül)."""
    v = decode_grid(g) if values is None else values
    y = (lat - g["lat0"]) / g["dlat"]
    x = (lon - g["lon0"]) / g["dlon"]
    i = min(max(int(math.floor(y)), 0), g["n_lat"] - 2)
    j = min(max(int(math.floor(x)), 0), g["n_lon"] - 2)
    fy, fx = min(max(y - i, 0.0), 1.0), min(max(x - j, 0.0), 1.0)
    return float(v[i, j] * (1 - fy) * (1 - fx) + v[i, j + 1] * (1 - fy) * fx
                 + v[i + 1, j] * fy * (1 - fx) + v[i + 1, j + 1] * fy * fx)


def record_ground(g, rec, values=None):
    """Yapının zemin kotu: poligon köşeleri ve temsil noktasındaki en düşük değer (yokuşta alt taraf)."""
    v = decode_grid(g) if values is None else values
    pts = list(rec.get("footprint") or [rec["point"]]) + [rec["rep"]]
    return round(min(sample(g, lat, lon, v) for lat, lon in pts), 1)
