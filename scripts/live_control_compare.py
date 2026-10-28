"""Check the live control run's geometries against the server's own EPSG:25832.

The control run requests Gauss-Krueger (EPSG:31466, northing-first) and
reprojects to EPSG:25832 itself. This script asks the same server for a few
features directly in EPSG:25832 and compares positions and areas. A swapped
axis would show up as shifts of thousands of kilometres.

Usage:
    python scripts/live_control_compare.py output/live-control/sample_sites_25832.gpkg
"""

from __future__ import annotations

import sys

import geopandas as gpd
import requests
from pyproj import Transformer

from oek_harvester.gml import parse_feature_collection

ENDPOINT = "https://geodienste-wasser.rlp-umwelt.de/geoserver/wfs"
LAYER = "wsg:twsg_rvo"
SAMPLE = 8


def main(gpkg: str) -> int:
    ours = gpd.read_file(gpkg)
    ours["key"] = ours["source_feature_id"].astype(str)
    ours = ours.set_index("key")

    response = requests.get(
        ENDPOINT,
        params={"SERVICE": "WFS", "VERSION": "2.0.0", "REQUEST": "GetFeature",
                "TYPENAMES": LAYER, "SRSNAME": "urn:ogc:def:crs:EPSG::25832",
                "COUNT": str(SAMPLE), "SORTBY": "ID"},
        headers={"User-Agent": "iris-oek-rp-harvester/0.1 (live control check)"},
        timeout=120,
    )
    response.raise_for_status()
    native = parse_feature_collection(response.content)
    to_lonlat = Transformer.from_crs(25832, 4326, always_xy=True)

    print(f"{'ID':>6}  {'name':34} {'lon':>7} {'lat':>7}  {'centroid shift m':>16}  {'area diff':>9}")
    worst = 0.0
    for feature in native:
        key = str(feature.properties["ID"]).split(".")[0]
        if key not in ours.index:
            print(f"{key:>6}  not in control run output")
            return 1
        mine = ours.loc[key].geometry
        theirs = feature.geometry
        shift = theirs.centroid.distance(mine.centroid)
        worst = max(worst, shift)
        lon, lat = to_lonlat.transform(theirs.centroid.x, theirs.centroid.y)
        area_diff = abs(theirs.area - mine.area) / theirs.area
        name = str(feature.properties.get("WSG_BEZ"))[:34]
        print(f"{key:>6}  {name:34} {lon:7.3f} {lat:7.3f}  {shift:16.2f}  {area_diff:9.4%}")

    west, south, east, north = ours.to_crs(4326).total_bounds
    print(f"\nall {len(ours)} features, lon/lat bounds: "
          f"{west:.2f} {south:.2f} {east:.2f} {north:.2f}")
    print(f"largest centroid shift: {worst:.2f} m")
    return 0 if worst < 5 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
