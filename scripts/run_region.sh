#!/usr/bin/env bash
# Tek bölge: Geofabrik dosyası → kutu → adaylar + yollar → karolar.
# Kullanım: scripts/run_region.sh <ad> <geofabrik-yolu> <lat_min,lon_min,lat_max,lon_max>
#   ör.     scripts/run_region.sh roma europe/italy/centro 41.80,12.35,41.99,12.60
# GitHub Actions matris işlerinin yerel karşılığıdır (tasarım §14.1).
set -euo pipefail
name=$1; gf=$2; box=$3
IFS=, read -r lat0 lon0 lat1 lon1 <<<"$box"
cd "$(dirname "$0")/.."
mkdir -p data out
src="data/$(basename "$gf")-latest.osm.pbf"
if [ ! -f "$src" ]; then
  curl -sS -L -o "$src" "https://download.geofabrik.de/${gf}-latest.osm.pbf"
  want=$(curl -sS -L "https://download.geofabrik.de/${gf}-latest.osm.pbf.md5" | cut -d' ' -f1)
  have=$(md5 -q "$src" 2>/dev/null || md5sum "$src" | cut -d' ' -f1)
  [ "$want" = "$have" ] || { echo "MD5 uyuşmuyor: $src"; rm -f "$src"; exit 1; }
fi
osmium extract -b "$lon0,$lat0,$lon1,$lat1" "$src" -o "data/$name.osm.pbf" --overwrite -s smart
osmium tags-filter "data/$name.osm.pbf" nwr/historic nwr/heritage nwr/building:historic nwr/wikidata \
  -o "data/$name-cand.osm.pbf" --overwrite
osmium tags-filter "data/$name.osm.pbf" w/highway -o "data/$name-roads.osm.pbf" --overwrite
rm -rf "out/$name"
.venv/bin/python -m pipeline.cli build --pbf "data/$name-cand.osm.pbf" --out "out/$name" --version "p0-$name" \
  --wikidata --commons --bbox "$box" --roads "data/$name-roads.osm.pbf"
