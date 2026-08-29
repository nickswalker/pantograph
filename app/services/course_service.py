"""Course data service.

Builds the course model from ``data/lrr2026.geojson``, which carries both Link
lines in one file: station Points, point-of-interest Points, and leg
LineStrings tagged with the ``lines`` they belong to and a ``sequence`` entry
per line.

The two lines share a 13-leg trunk from International District/Chinatown to
Lynnwood City Center. Those legs appear once, carrying both line keys, so a
Both Lines course is the union of the branches plus one trunk -- 38 legs, not
51. A leg's identity is therefore its ``(start_exchange, end_exchange)`` pair,
which is stable across lines; ``sequence`` is per-line display ordering only.

Units follow the solver's convention rather than the file's: distances are
integer hundredths of a mile (the geojson stores float miles) and ascent and
descent are integer feet. Straight-line commute distances between exchanges
are computed here, since the geojson has no commute data.
"""

import math
from functools import lru_cache

from app.utils import (
    ALL_LINES, LINE_1, LINE_2, _course_features, _legs_for_lines,
    _legs_in_running_order, course_lines_for,
)

# Hundredths of a mile: the scale the ASP facts and leg-solver.js expect.
DISTANCE_SCALE = 100
EARTH_RADIUS_MILES = 3958.7613


def _scale_miles(miles):
    """Miles -> integer hundredths, matching the solver's ceil convention."""
    return math.ceil(float(miles) * DISTANCE_SCALE)


def leg_key(start_exchange, end_exchange):
    """Stable identity for a leg, independent of which line it belongs to."""
    return (start_exchange, end_exchange)


def station_code(exchange_id):
    """The two-digit station code, as raceconditionrunning.com derives it.

    The last two characters of the exchange id. Codes are not unique on their
    own -- 54 is Stadium on the 1 Line and Judkins Park on the 2 Line -- so a
    code is only unambiguous next to its line badge.
    """
    text = str(exchange_id or '')
    return text[-2:] if len(text) >= 2 else None


def line_code(exchange_id):
    """Everything ahead of the station number: '1', '2', or '12'."""
    text = str(exchange_id or '')
    return text[:-2] if len(text) > 2 else None


def line_codes(exchange_id):
    """The line code split into one entry per line, for rendering.

    A trunk exchange's code is '12', which draws as two circles -- a 1 and a
    2 -- rather than one badge reading "12".
    """
    return list(line_code(exchange_id) or '')


@lru_cache(maxsize=1)
def exchanges_by_id():
    """Mapping of exchange id -> {id, name, coordinates}."""
    stations, _ = _course_features()
    return {
        props['id']: {'id': props['id'], 'name': props['name'], 'coordinates': coords}
        for props, coords in stations
    }


@lru_cache(maxsize=1)
def station_name_to_exchange_id():
    """Mapping of station name -> exchange id.

    Names come from the same file the course does, so no alias table is
    needed; a stored preference either matches a current station or does not
    resolve at all.
    """
    return {exchange['name']: exchange['id'] for exchange in exchanges_by_id().values()}


def resolve_station_name(name):
    """Resolve a station name to an exchange id, or None if unknown."""
    if not name:
        return None
    return station_name_to_exchange_id().get(name.strip())


def is_course_exchange(exchange_id, lines=None):
    """True if the exchange is an endpoint of a leg on ``lines``."""
    return any(
        exchange_id in (leg['start_exchange'], leg['end_exchange'])
        for leg in _legs_for_lines(lines)
    )


def _sequence_on_line(leg, line):
    """A leg's running position on ``line``; sequence is parallel to lines."""
    return leg['sequence'][leg['lines'].index(line)]


def legs_for(lines=None):
    """Course legs for ``lines``, in running order, de-duplicated.

    The course is a Y: each selected line has its own branch running from its
    terminus to International District/Chinatown, and from there they share a
    trunk to Lynnwood City Center. Legs come back in the order the race is
    actually run -- every branch first, then the shared trunk once -- so a
    both-lines team starts two runners at two termini at the same time and
    the trunk begins only once both branches have arrived.

    See :func:`app.utils._legs_in_running_order`, which owns the ordering so
    the station list on the registration form shares it.
    """
    return _legs_in_running_order(lines)


def _haversine_miles(a_coords, b_coords):
    lon_a, lat_a = a_coords[0], a_coords[1]
    lon_b, lat_b = b_coords[0], b_coords[1]
    phi_a, phi_b = math.radians(lat_a), math.radians(lat_b)
    d_phi = phi_b - phi_a
    d_lambda = math.radians(lon_b - lon_a)
    h = math.sin(d_phi / 2) ** 2 + math.cos(phi_a) * math.cos(phi_b) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(h))


@lru_cache(maxsize=4)
def commute_pairs(lines=None):
    """Straight-line [a, b, distance] triples between the exchanges on ``lines``.

    Each unordered pair appears once and self-distances are omitted, matching
    what leg-solver.js symmetrizes when generating commuteDistance/3 facts.
    """
    ids = sorted(exchange_ids_for(lines))
    exchanges = exchanges_by_id()
    pairs = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            distance = _haversine_miles(exchanges[a]['coordinates'], exchanges[b]['coordinates'])
            pairs.append([a, b, _scale_miles(distance)])
    return pairs


@lru_cache(maxsize=4)
def commute_distances(lines=None):
    """Symmetric {(a, b): distance} in hundredths of a mile, with zero self-distances."""
    table = {}
    for a, b, distance in commute_pairs(lines):
        table[(a, b)] = distance
        table[(b, a)] = distance
    for exchange_id in exchange_ids_for(lines):
        table[(exchange_id, exchange_id)] = 0
    return table


def exchange_ids_for(lines=None):
    """Every exchange touched by a leg on ``lines``."""
    ids = set()
    for leg in _legs_for_lines(lines):
        ids.add(leg['start_exchange'])
        ids.add(leg['end_exchange'])
    return ids


def serialize_legs(lines=None):
    """Legs in the shape the board and solver consume."""
    exchanges = exchanges_by_id()

    def endpoint(exchange_id):
        exchange = exchanges.get(exchange_id)
        return {
            'id': exchange_id,
            'name': exchange['name'] if exchange else None,
            'station_code': station_code(exchange_id),
            'line_code': line_code(exchange_id),
        }

    serialized = []
    for leg in legs_for(lines):
        start, end = leg['start_exchange'], leg['end_exchange']
        serialized.append({
            'start': endpoint(start),
            'end': endpoint(end),
            'distance': _scale_miles(leg['distance_mi']),
            'ascent': int(leg['ascent_ft']),
            'descent': int(leg['descent_ft']),
            'lines': list(leg['lines']),
            # Per-line running position, so the board can order and group by branch.
            'sequence': {line: _sequence_on_line(leg, line) for line in leg['lines']},
        })
    return serialized


def course_for(team_lines=None):
    """The full course model for a team's registered line(s)."""
    lines = course_lines_for(team_lines)
    return {
        'event': 'lrr2026',
        'units': {
            'distance': 'hundredths of a mile: ceil(miles * 100)',
            'commute': 'hundredths of a mile; straight-line between exchanges',
            'ascent': 'feet',
            'descent': 'feet',
            'pace': 'seconds per mile',
        },
        'lines': list(lines) if lines is not None else list(ALL_LINES),
        'legs': serialize_legs(lines),
        'commute': commute_pairs(lines),
        'station_index': station_name_to_exchange_id(),
    }
