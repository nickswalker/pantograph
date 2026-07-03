#!/usr/bin/env python3
"""Export the relay course model from relay-scheduler into data/legs_2026.json.

Reads (all read-only) from a relay-scheduler checkout:
  <event>/lrr.lp            -- leg/3 facts: the ordered course legs (solver leg ids)
  <event>/facts_course.lpx  -- distance/3, ascent/3, descent/3, commuteDistance/3,
                               strollerTraversible/2, exchangeName/2 course facts
  <event>/legs/*.gpx        -- per-leg track geometry ("<start>-<end>.gpx")
  <event>/legs/exchanges.geojson -- exchange coordinates

and from this repo:
  data/lrr_1_line.geojson   -- canonical station names used by the registration
                               form (load_station_names); exchange ids match.

Units (clorm "K" integer convention, see relay_scheduler/domain.py):
  - distance / commute: hundredths of a mile, i.e. ceil(miles * 100)
    (kPrecision with precision 2.0; facts_course.lpx carries
    distancePrecision("2.0") and solve.py defaults --distance-precision 2.0).
  - ascent / descent: integer feet (relay_scheduler/legs.py rounds
    GPX metres * 3.28084; not K-scaled).
  - durations/paces elsewhere in the domain use precision 0.0, i.e. plain
    seconds (durationPrecision("0.0")).

Usage:
  uv run python scripts/export_course.py /path/to/relay-scheduler \
      [--event lrr2026] [--stations-geojson data/lrr_1_line.geojson] \
      [--output data/legs_2026.json]
"""

import argparse
import json
import math
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

GPX_NS = {"gpx": "http://www.topografix.com/GPX/1/1"}


def strip_asp_comments(text):
    """Remove %-to-end-of-line ASP comments."""
    return re.sub(r"%[^\n]*", "", text)


def parse_int_facts(text, name, arity):
    """Parse all-integer-argument facts ``name(a,b,...)`` from ASP source."""
    pattern = re.compile(
        r"\b" + re.escape(name) + r"\(\s*" + r"\s*,\s*".join([r"(-?\d+)"] * arity) + r"\s*\)"
    )
    return [tuple(int(g) for g in m.groups()) for m in pattern.finditer(text)]


def parse_exchange_names(text):
    """Parse exchangeName(id, "Name") facts."""
    pattern = re.compile(r'\bexchangeName\(\s*(\d+)\s*,\s*"((?:[^"\\]|\\.)*)"\s*\)')
    return {int(m.group(1)): m.group(2) for m in pattern.finditer(text)}


def parse_legs(lrr_lp_text):
    """Parse the leg/3 pooled fact from lrr.lp into ordered (index, start, end)."""
    text = strip_asp_comments(lrr_lp_text)
    match = re.search(r"\bleg\s*\(([^)]*)\)", text)
    if not match:
        raise SystemExit("error: no leg/3 fact found in lrr.lp")
    legs = []
    for entry in match.group(1).split(";"):
        parts = [p.strip() for p in entry.split(",") if p.strip()]
        if not parts:
            continue
        if len(parts) != 3:
            raise SystemExit(f"error: malformed leg entry: {entry!r}")
        legs.append(tuple(int(p) for p in parts))
    legs.sort(key=lambda leg: leg[0])
    indexes = [leg[0] for leg in legs]
    if indexes != list(range(len(legs))):
        raise SystemExit(f"error: leg indexes are not contiguous from 0: {indexes}")
    return legs


def load_gpx_track(gpx_path):
    """Return the [lon, lat] track points of a GPX file, in file order."""
    root = ET.parse(gpx_path).getroot()
    points = []
    for trkpt in root.iterfind(".//gpx:trkpt", GPX_NS):
        points.append([round(float(trkpt.attrib["lon"]), 6), round(float(trkpt.attrib["lat"]), 6)])
    if not points:
        raise SystemExit(f"error: no track points in {gpx_path}")
    return points


def leg_geometry(legs_dir, start, end):
    """Geometry for start->end: direct GPX, or a stitched chain of GPX legs.

    Some solver legs skip an intermediate exchange (e.g. 145->143 bypasses the
    NE 130th St infill) and have no direct GPX; stitch the per-segment tracks.
    """
    direct = legs_dir / f"{start}-{end}.gpx"
    if direct.exists():
        return load_gpx_track(direct)

    # Build the directed edge set available as GPX files, then BFS start->end.
    edges = {}
    for gpx in legs_dir.glob("*.gpx"):
        try:
            a, b = (int(x) for x in gpx.stem.split("-"))
        except ValueError:
            continue
        edges.setdefault(a, []).append(b)
    queue, seen, parent = [start], {start}, {}
    while queue:
        node = queue.pop(0)
        if node == end:
            break
        for nxt in edges.get(node, []):
            if nxt not in seen:
                seen.add(nxt)
                parent[nxt] = node
                queue.append(nxt)
    if end not in parent:
        raise SystemExit(f"error: no GPX geometry (direct or chained) for leg {start}->{end}")
    path = [end]
    while path[-1] != start:
        path.append(parent[path[-1]])
    path.reverse()
    points = []
    for a, b in zip(path[:-1], path[1:]):
        segment = load_gpx_track(legs_dir / f"{a}-{b}.gpx")
        points.extend(segment if not points else segment[1:])
    print(f"note: leg {start}->{end} geometry stitched from GPX chain {path}")
    return points


def relay_scheduler_provenance(repo_path):
    try:
        commit = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        commit = None
    return commit


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("relay_scheduler", type=Path,
                        help="Path to the relay-scheduler checkout (read-only)")
    parser.add_argument("--event", default="lrr2026",
                        help="Event directory inside relay-scheduler (default: lrr2026)")
    parser.add_argument("--stations-geojson", type=Path,
                        default=Path("data/lrr_1_line.geojson"),
                        help="Pantograph stations GeoJSON for canonical names")
    parser.add_argument("--output", type=Path, default=Path("data/legs_2026.json"))
    args = parser.parse_args()

    event_dir = args.relay_scheduler / args.event
    facts_text = strip_asp_comments((event_dir / "facts_course.lpx").read_text())
    legs = parse_legs((event_dir / "lrr.lp").read_text())

    distances = {(a, b): d for a, b, d in parse_int_facts(facts_text, "distance", 3)}
    ascents = {(a, b): v for a, b, v in parse_int_facts(facts_text, "ascent", 3)}
    descents = {(a, b): v for a, b, v in parse_int_facts(facts_text, "descent", 3)}
    commutes = {(a, b): v for a, b, v in parse_int_facts(facts_text, "commuteDistance", 3)}
    stroller = parse_int_facts(facts_text, "strollerTraversible", 2)
    fact_names = parse_exchange_names(facts_text)

    precision = re.search(r'distancePrecision\("([\d.]+)"\)', facts_text)
    if precision and float(precision.group(1)) != 2.0:
        raise SystemExit(f"error: unexpected distancePrecision {precision.group(1)}; "
                         "the units documentation below assumes 2.0")

    # Exchange coordinates from the legs bundle metadata.
    with open(event_dir / "legs" / "exchanges.geojson") as f:
        bundle_names, coordinates = {}, {}
        for feature in json.load(f)["features"]:
            props = feature["properties"]
            bundle_names[props["id"]] = props["name"]
            coordinates[props["id"]] = [round(c, 6) for c in feature["geometry"]["coordinates"]]

    # Canonical station names as the registration form stores them
    # (app/utils.py load_station_names reads this file; ids match exchange ids).
    with open(args.stations_geojson) as f:
        station_names = {int(feat["properties"]["id"]): feat["properties"]["name"]
                         for feat in json.load(f)["features"]}

    course_exchange_ids = sorted(fact_names)
    exchanges, aliases = [], {}
    for ex_id in course_exchange_ids:
        candidates = [station_names.get(ex_id), fact_names.get(ex_id), bundle_names.get(ex_id)]
        canonical = next(name for name in candidates if name)
        if ex_id not in coordinates:
            raise SystemExit(f"error: no coordinates for exchange {ex_id} in exchanges.geojson")
        exchanges.append({"id": ex_id, "name": canonical, "coordinates": coordinates[ex_id]})
        for variant in candidates:
            if variant and variant != canonical:
                aliases[variant] = ex_id

    # Stations offered by the registration form that are not course exchanges
    # (e.g. unopened infill stations). They still resolve to their line id so a
    # stored preferred_station never dangles, but carry no legs/commute data.
    non_course = {name: ex_id for ex_id, name in sorted(station_names.items())
                  if ex_id not in fact_names}

    leg_objects = []
    for index, start, end in legs:
        missing = [k for k, table in
                   (("distance", distances), ("ascent", ascents), ("descent", descents))
                   if (start, end) not in table]
        if missing:
            raise SystemExit(f"error: facts_course.lpx lacks {missing} for leg {start}->{end}")
        leg_objects.append({
            "index": index,
            "start": start,
            "end": end,
            "distance": distances[(start, end)],
            "ascent": ascents[(start, end)],
            "descent": descents[(start, end)],
            "geometry": leg_geometry(event_dir / "legs", start, end),
        })

    # commuteDistance/3 is symmetric with explicit zero self-pairs; verify and
    # emit each unordered pair once.
    commute_pairs = []
    for (a, b), dist in sorted(commutes.items()):
        if a > b:
            if commutes.get((b, a)) != dist:
                raise SystemExit(f"error: asymmetric commuteDistance for {a},{b}")
            continue
        if a == b:
            if dist != 0:
                raise SystemExit(f"error: non-zero self commuteDistance({a},{a},{dist})")
            continue
        commute_pairs.append([a, b, dist])

    course = {
        "event": args.event,
        "source": {
            "repository": "light-rail-relay/relay-scheduler",
            "commit": relay_scheduler_provenance(args.relay_scheduler),
            "files": [f"{args.event}/lrr.lp", f"{args.event}/facts_course.lpx",
                      f"{args.event}/legs/"],
        },
        "units": {
            "distance": "hundredths of a mile: ceil(miles * 100) "
                        "(clorm K, precision 2.0; relay_scheduler/domain.py kPrecision)",
            "commute": "hundredths of a mile (same scale as distance); "
                       "straight-line between exchanges, symmetric, self-distance 0 omitted",
            "ascent": "feet (integer, not K-scaled)",
            "descent": "feet (integer, not K-scaled)",
            "pace": "seconds per mile (duration precision 0.0) -- for solver fact generation",
            "leg_index": "solver leg id from lrr.lp leg/3: 0-based, in running order",
        },
        "exchanges": exchanges,
        "legs": leg_objects,
        "commute": commute_pairs,
        "stroller_traversible": sorted({tuple(sorted(pair)) for pair in stroller}),
        "station_aliases": aliases,
        "non_course_stations": non_course,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(course, f, indent=1)
        f.write("\n")
    total = sum(leg["distance"] for leg in leg_objects)
    print(f"wrote {args.output}: {len(leg_objects)} legs, {len(exchanges)} exchanges, "
          f"{len(commute_pairs)} commute pairs, total {total / 100:.2f} mi")
    return 0


if __name__ == "__main__":
    sys.exit(main())
