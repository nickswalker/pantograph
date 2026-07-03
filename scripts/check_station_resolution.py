#!/usr/bin/env python3
"""WP1 acceptance check: station names resolve to exchange ids.

Verifies, using app.services.course_service:
  1. every distinct ``preferred_station`` value in the database resolves to an
     integer exchange id (the DB is opened read-only);
  2. every station name returned by ``load_station_names()`` resolves;
  3. course exchanges resolve to their own id (round trip).

Run from the repo root (data/ paths are relative):
  uv run python scripts/check_station_resolution.py [--db data/pantograph.db]

Exits non-zero and lists offending names on failure.
"""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.course_service import (  # noqa: E402
    is_course_exchange, load_course, resolve_station_name,
)
from app.utils import load_station_names  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="data/pantograph.db",
                        help="SQLite database to check (opened read-only)")
    args = parser.parse_args()

    failures = []
    course = load_course()
    print(f"course: {course['event']}, {len(course['legs'])} legs, "
          f"{len(course['exchanges'])} exchanges")

    # 1. Every preferred_station stored in the DB resolves.
    db_path = Path(args.db)
    if db_path.exists():
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT DISTINCT preferred_station FROM team_membership "
                "WHERE preferred_station IS NOT NULL AND preferred_station != ''"
            ).fetchall()
        finally:
            conn.close()
        print(f"db: {len(rows)} distinct preferred_station value(s) in {db_path}")
        for (name,) in rows:
            exchange_id = resolve_station_name(name)
            if exchange_id is None:
                failures.append(f"db preferred_station {name!r} does not resolve")
            else:
                note = "" if is_course_exchange(exchange_id) else " (non-course station)"
                print(f"  ok: {name!r} -> {exchange_id}{note}")
    else:
        print(f"db: {db_path} not found -- skipping DB check")
        failures.append(f"database {db_path} not found")

    # 2. Every station name the registration form can offer resolves.
    station_names = load_station_names()
    if not station_names:
        failures.append("load_station_names() returned no stations")
    print(f"stations: {len(station_names)} names from load_station_names()")
    for name in station_names:
        exchange_id = resolve_station_name(name)
        if exchange_id is None:
            failures.append(f"station name {name!r} does not resolve")
        else:
            note = "" if is_course_exchange(exchange_id) else " (non-course station)"
            print(f"  ok: {name!r} -> {exchange_id}{note}")

    # 3. Course exchange names round-trip to their own id.
    for exchange in course["exchanges"]:
        if resolve_station_name(exchange["name"]) != exchange["id"]:
            failures.append(
                f"exchange name {exchange['name']!r} resolves to "
                f"{resolve_station_name(exchange['name'])!r}, expected {exchange['id']}")

    if failures:
        print("\nFAIL:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nPASS: all station names resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
