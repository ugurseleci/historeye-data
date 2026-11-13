"""Geohash kodlama (standart base32). Karo seçimi için kullanılır; koordinat kodlamasına girmez."""

_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def encode(lat, lon, precision=5):
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    out, bit, ch, even = [], 0, 0, True
    while len(out) < precision:
        if even:
            mid = (lon_lo + lon_hi) / 2
            if lon >= mid:
                ch |= 1 << (4 - bit)
                lon_lo = mid
            else:
                lon_hi = mid
        else:
            mid = (lat_lo + lat_hi) / 2
            if lat >= mid:
                ch |= 1 << (4 - bit)
                lat_lo = mid
            else:
                lat_hi = mid
        even = not even
        if bit < 4:
            bit += 1
        else:
            out.append(_BASE32[ch])
            bit, ch = 0, 0
    return "".join(out)


def bbox(gh):
    """(lat_min, lon_min, lat_max, lon_max)."""
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    even = True
    for c in gh:
        v = _BASE32.index(c)
        for i in range(4, -1, -1):
            b = (v >> i) & 1
            if even:
                mid = (lon_lo + lon_hi) / 2
                lon_lo, lon_hi = (mid, lon_hi) if b else (lon_lo, mid)
            else:
                mid = (lat_lo + lat_hi) / 2
                lat_lo, lat_hi = (mid, lat_hi) if b else (lat_lo, mid)
            even = not even
    return lat_lo, lon_lo, lat_hi, lon_hi
