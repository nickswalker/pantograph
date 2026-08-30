#!/usr/bin/env python3
"""Race-day rehearsal: simulate a full event on a disposable copy of the app.

Spins up a throwaway instance of the Flask app (its own SQLite DB and uploads
folder under ``simulation/``, never the real ``data/pantograph.db``), then:

  1. Registers 12 team-format entries and 5 solo entries with an assortment
     of line selections, passwords, and join preferences, plus ~10 accounts
     that never join anything.
  2. Has the admin approve every team but one, which is left permanently
     PENDING (it "never pays") -- its captain exists but nobody else can
     join, and it is excluded from race day entirely.
  3. Plays out race day in real time (compressed): each entrant walks its
     course's exchanges and uploads a ~2MB JPEG at each one, some without
     GPS, some skipped outright, exactly like the real upload endpoint would
     see from a phone at a relay exchange.

Nothing here touches OAuth -- accounts are seeded directly into the scratch
DB and authenticated over HTTP with a session cookie signed the same way
Flask-Login's would be, using the app's own SECRET_KEY. Every other action
(team creation, admin approval, joining, photo upload) goes over real HTTP
to the real routes, so it exercises the actual validation and upload
pipeline, not a shortcut around it.

Usage:
    uv run python scripts/simulate_race_day.py
    uv run python scripts/simulate_race_day.py --teams 12 --solos 5 --orphans 10
    uv run python scripts/simulate_race_day.py --race-seconds-per-hour 20 --no-hold

While it runs (and after, until you press Enter or Ctrl-C), the server stays
up at http://127.0.0.1:<port>/ -- the public gallery links printed at the end
need no login, so you can watch photos land live in a browser.
"""
import argparse
import io
import os
import random
import secrets
import shutil
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# .env loading (the app itself never does this -- it expects real deployment
# secrets in the environment already. We're a standalone script, so load
# them from the repo's .env ourselves, without overriding anything already
# set in the environment.)
# --------------------------------------------------------------------------

def _load_dotenv(path):
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


# --------------------------------------------------------------------------
# Fake-but-plausible data pools
# --------------------------------------------------------------------------

FIRST_NAMES = [
    "Alex", "Jordan", "Sam", "Taylor", "Morgan", "Casey", "Riley", "Jamie",
    "Avery", "Quinn", "Drew", "Skyler", "Reese", "Rowan", "Emerson", "Hayden",
    "Parker", "Sawyer", "Dakota", "Finley", "Elliot", "Kendall", "Blake", "Charlie",
    "Devon", "Harley", "Marlowe", "Shawn", "Tatum", "Wren", "Priya", "Kenji",
    "Fatima", "Diego", "Noor", "Soren", "Yuki", "Mateo", "Ines", "Oleg",
]

LAST_NAMES = [
    "Nguyen", "Garcia", "Smith", "Johansson", "Patel", "Kim", "Rossi", "Muller",
    "Andersen", "Okafor", "Silva", "Kowalski", "Haddad", "Novak", "Berg", "Torres",
    "Fischer", "Yamamoto", "Costa", "Larsen", "Petrov", "Osei", "Dubois", "Nakamura",
    "Reyes", "Weber", "Choi", "Hassan", "Lindqvist", "Moreau", "Santos", "Braun",
]

TEAM_NAME_POOL = [
    "Third Rail Runners", "Overhead Wire Warriors", "Catenary Crew", "Night Owl Express",
    "Platform Nine and a Half", "The Last Mile Club", "Rail Yard Renegades", "Panto Graphs",
    "Sound Transit Sirens", "Tunnel Vision Track Club", "The Overheaders", "Off Peak Pacers",
    "Signal Failure Squad", "Grade Separated", "Light Rail Legends", "The Regional Expresses",
    "Contact Wire Coalition", "Northbound & Down", "Deadhead Runners", "Rapid Transit Renegades",
    "Wayside Warriors", "Trackside Track Club",
]

SOLO_NAME_POOL = [
    "Lone Conductor", "The Solo Commute", "One Track Mind", "Single Car Consist",
    "The Independent Line", "Unaccompanied Minor", "Free Range Runner", "The Wildcat Special",
]

COMMENT_POOL = [
    "First relay ever, excited!", "Bringing snacks for the exchange.", "Can hop in on an earlier leg if needed.",
    "Recovering from a half marathon last week, taking it easy.", "Will have a stroller, heads up.",
    "Bringing my dog for my leg.", "Happy to run more than my share if someone drops.",
]


def unique_names(count):
    """``count`` distinct "First Last" combinations, extending with a numeric
    suffix once the pool of combinations is exhausted."""
    seen = set()
    names = []
    while len(names) < count:
        name = f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}"
        if name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names


def unique_from_pool(pool, count):
    if count <= len(pool):
        return random.sample(pool, count)
    # Pool exhausted -- extend with numbered variants rather than raise.
    picked = list(pool)
    random.shuffle(picked)
    i = 2
    while len(picked) < count:
        picked.append(f"{random.choice(pool)} {i}")
        i += 1
    return picked[:count]


# --------------------------------------------------------------------------
# Synthetic photos: ~2MB JPEGs with real EXIF (capture time, optional GPS),
# matching exactly what app.utils.get_exif_data/get_gps_data/get_capture_time
# parse on the way in.
# --------------------------------------------------------------------------

class PhotoFactory:
    """Builds upload-ready JPEG bytes on demand.

    Pixel data is generated once (a small pool of noise images, ~2MB each
    when re-encoded) and reused; only the EXIF block -- built fresh into its
    own object each call -- differs between photos, so this is safe to call
    from multiple threads concurrently.
    """

    def __init__(self, width=2000, height=1500, quality=85, pool_size=3):
        from PIL import Image
        self._Image = Image
        self._quality = quality
        self._pool = [
            Image.frombytes('RGB', (width, height), os.urandom(width * height * 3))
            for _ in range(pool_size)
        ]

    def build(self, capture_dt, gps_latlon=None, filename='exchange.jpg'):
        """``capture_dt`` must be timezone-aware. ``gps_latlon`` is (lat, lon) or None."""
        Image = self._Image
        exif = Image.Exif()
        stamp = capture_dt.strftime('%Y:%m:%d %H:%M:%S')
        offset = capture_dt.strftime('%z')
        offset = f"{offset[:3]}:{offset[3:]}" if offset else None

        exif[0x0132] = stamp  # DateTime
        exif_ifd = exif.get_ifd(0x8769)  # Exif IFD
        exif_ifd[36867] = stamp  # DateTimeOriginal
        if offset:
            exif_ifd[36881] = offset  # OffsetTimeOriginal

        if gps_latlon is not None:
            lat, lon = gps_latlon
            gps_ifd = exif.get_ifd(0x8825)  # GPSInfo
            gps_ifd[1] = 'N' if lat >= 0 else 'S'
            gps_ifd[2] = _degrees_to_dms(abs(lat))
            gps_ifd[3] = 'E' if lon >= 0 else 'W'
            gps_ifd[4] = _degrees_to_dms(abs(lon))

        base = random.choice(self._pool)
        buf = io.BytesIO()
        base.save(buf, 'JPEG', quality=self._quality, exif=exif.tobytes())
        return buf.getvalue(), filename


def _degrees_to_dms(decimal_degrees):
    d = int(decimal_degrees)
    m_float = (decimal_degrees - d) * 60
    m = int(m_float)
    s = (m_float - m) * 60
    return (d, m, round(s, 2))


def _jittered(coord, max_deg=0.0006):
    """Nudge a coordinate by up to ~60m so photos aren't suspiciously exact,
    while staying comfortably inside the app's 200m exchange-matching radius."""
    return coord + random.uniform(-max_deg, max_deg)


# --------------------------------------------------------------------------
# HTTP client: one per simulated user, authenticated via a Flask-Login
# session cookie signed with the app's own SECRET_KEY (no OAuth involved).
# --------------------------------------------------------------------------

class SimClient:
    def __init__(self, base_url, cookie_name, cookie_value, label):
        self.base_url = base_url
        self.label = label  # for logging
        self.cookie_name = cookie_name
        self.cookie_value = cookie_value  # kept around so we can print login cookies at the end
        self.http = requests.Session()
        self.http.cookies.set(cookie_name, cookie_value)

    def _url(self, path):
        return f"{self.base_url}{path}"

    def create_team(self, **form):
        return self.http.post(self._url('/create-team'), data=form)

    def join_team(self, **form):
        return self.http.post(self._url('/join-team'), data=form)

    def approve_team(self, team_id):
        return self.http.post(self._url(f'/admin/team/{team_id}/approve'))

    def get_assignments(self, team_id):
        return self.http.get(self._url(f'/team/{team_id}/assignments'))

    def put_assignments(self, team_id, assignments):
        return self.http.put(self._url(f'/team/{team_id}/assignments'), json=assignments)

    def upload_image(self, team_id, jpeg_bytes, filename):
        files = {'files': (filename, jpeg_bytes, 'image/jpeg')}
        return self.http.post(self._url(f'/team/{team_id}/images'), files=files)


# --------------------------------------------------------------------------
# Entrant model
# --------------------------------------------------------------------------

@dataclass
class SimUser:
    id: str
    name: str
    email: str
    client: SimClient = None


@dataclass
class Entrant:
    team_id: str
    name: str
    format: str            # 'Team' or 'Solo'
    lines: object           # TeamLines enum member
    captain: SimUser
    members: list = field(default_factory=list)  # includes captain
    password: str = None
    gallery_hash: str = None
    approved: bool = False
    timeline: list = field(default_factory=list)  # [{exchange_id, name, lat, lon, offset_seconds}]


def log(msg):
    ts = time.strftime('%H:%M:%S')
    with log.lock:
        print(f"[{ts}] {msg}", flush=True)


log.lock = threading.Lock()


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=5055)
    p.add_argument('--teams', type=int, default=12, help='number of Team-format entries')
    p.add_argument('--solos', type=int, default=5, help='number of Solo entries')
    p.add_argument('--orphans', type=int, default=10, help='accounts created but never joining a team')
    p.add_argument('--seed', type=int, default=None, help='random seed, for a reproducible run')
    p.add_argument('--race-seconds-per-hour', type=float, default=40.0,
                    help='real seconds per simulated race hour (default 40: a 6h relay plays out in 4 real minutes)')
    p.add_argument('--skip-prob', type=float, default=0.12, help='chance a team misses an exchange photo entirely')
    p.add_argument('--no-gps-prob', type=float, default=0.25, help='chance an uploaded photo carries no GPS data')
    p.add_argument('--data-dir', default=str(REPO_ROOT / 'simulation'), help='scratch DB + uploads live here')
    p.add_argument('--keep-data', action='store_true', help="don't wipe --data-dir before starting")
    p.add_argument('--no-hold', action='store_true', help='exit immediately after the run instead of keeping the server up')
    p.add_argument('--no-assign-legs', action='store_true', help='skip populating the leg-assignment board')
    return p.parse_args()


def main():
    args = parse_args()
    if args.seed is not None:
        random.seed(args.seed)
    else:
        args.seed = random.randrange(1_000_000)
        random.seed(args.seed)

    os.chdir(REPO_ROOT)
    sys.path.insert(0, str(REPO_ROOT))  # so `app` resolves regardless of invocation cwd
    _load_dotenv(REPO_ROOT / '.env')
    os.environ.setdefault('FLASK_ENV', 'development')  # insecure cookies over plain http, no ProxyFix

    data_dir = Path(args.data_dir)
    if data_dir.exists() and not args.keep_data:
        shutil.rmtree(data_dir)
    uploads_dir = data_dir / 'uploads'
    uploads_dir.mkdir(parents=True, exist_ok=True)

    # Point the app at scratch storage BEFORE create_app() reads Config.
    from app.config import Config
    Config.SQLALCHEMY_DATABASE_URI = f"sqlite:///{data_dir / 'race_day.db'}"
    Config.UPLOAD_FOLDER = str(uploads_dir)
    # The upload route is rate-limited per source IP (real race day has one
    # real phone per IP). Every simulated user here shares 127.0.0.1, so
    # without this the limit throttles the whole rehearsal, not each team.
    # Must be set before create_app() -- Flask-Limiter latches it at init.
    Config.RATELIMIT_ENABLED = False

    from app import create_app
    from app.models import db, User, Team, TeamLines, OAuthProvider
    from app.services import course_service
    from app.utils import course_lines_for, max_preferred_miles, load_end_station_names, course_distance_miles

    flask_app = create_app()
    # Dev mode: reload templates from disk on every request instead of
    # caching them after first render, so edits to templates/*.html show up
    # without restarting this script. (We drive the server via make_server
    # below rather than app.run(), so Flask's process-restarting reloader
    # -- which watches .py files and needs to own the main process -- isn't
    # used here; this only affects Jinja template caching.)
    flask_app.config['TEMPLATES_AUTO_RELOAD'] = True
    flask_app.jinja_env.auto_reload = True

    from werkzeug.serving import make_server
    server = make_server(args.host, args.port, flask_app, threaded=True)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://{args.host}:{args.port}"
    log(f"Server up at {base_url} (seed={args.seed}, scratch DB at {data_dir})")

    for _ in range(50):
        try:
            requests.get(f"{base_url}/stats.json", timeout=1)
            break
        except requests.RequestException:
            time.sleep(0.1)

    photo_factory = PhotoFactory()

    with flask_app.app_context():
        serializer = flask_app.session_interface.get_signing_serializer(flask_app)
        cookie_name = flask_app.config.get('SESSION_COOKIE_NAME', 'session')

        def new_client(user, label):
            cookie_value = serializer.dumps({'_user_id': user.id, '_fresh': True})
            return SimClient(base_url, cookie_name, cookie_value, label)

        # --- Seed accounts ---------------------------------------------
        # A running server thread reads through its own DB connection, so
        # each account must be committed (not just flushed) before the HTTP
        # call that logs in as them -- otherwise that connection's snapshot
        # predates the INSERT and the login silently fails as anonymous.
        hold_back_index = random.randrange(args.teams) if args.teams else -1
        team_sizes = [1 if i == hold_back_index else random.randint(8, 16) for i in range(args.teams)]
        total_team_slots = sum(team_sizes)

        log(f"Seeding accounts (admin + {total_team_slots} team seats + {args.solos} solo captains "
            f"+ {args.orphans} orphans)...")

        admin_user = User(
            email=os.environ['ADMIN_EMAIL'], name="Race Admin",
            provider=OAuthProvider.GOOGLE, provider_id='sim-admin', is_admin=True,
        )
        db.session.add(admin_user)
        db.session.commit()
        admin_client = new_client(admin_user, 'admin')

        all_names = unique_names(total_team_slots + args.solos + args.orphans)
        name_iter = iter(all_names)
        counter = [0]

        def make_user(name):
            counter[0] += 1
            slug = ''.join(c for c in name.lower() if c.isalnum())
            u = User(
                email=f"{slug}.{counter[0]}@sim.example.test",
                name=name,
                provider=OAuthProvider.GOOGLE,
                provider_id=f"sim-{counter[0]}",
            )
            db.session.add(u)
            db.session.commit()
            return u

        orphan_users = [make_user(next(name_iter)) for _ in range(args.orphans)]
        log(f"  {len(orphan_users)} accounts created that will never join a team.")

        # --- Build entrant plans -----------------------------------------
        team_names = unique_from_pool(TEAM_NAME_POOL, args.teams)
        solo_names = unique_from_pool(SOLO_NAME_POOL, args.solos)

        line_choices_team = [TeamLines.ONE] * 45 + [TeamLines.TWO] * 40 + [TeamLines.BOTH] * 15
        line_choices_solo = [TeamLines.ONE, TeamLines.TWO]

        def random_preferences(lines):
            cap = max_preferred_miles(lines)
            stations = load_end_station_names(course_lines_for(lines))
            pace_seconds = random.randint(390, 780)
            return {
                'willing_to_lead': 'yes' if random.random() < 0.25 else 'no',
                'preferred_miles': str(round(random.uniform(0.8, cap), 1)),
                'planned_pace': f"{pace_seconds // 60}:{pace_seconds % 60:02d}",
                'preferred_station': random.choice(stations) if random.random() < 0.6 else '',
                'comments': random.choice(COMMENT_POOL) if random.random() < 0.3 else '',
                'waiver_agreed': 'on',
                'email_opt_in': 'true' if random.random() < 0.5 else 'false',
            }

        def random_duration(lines):
            miles = course_distance_miles(course_lines_for(lines))
            seconds = int(miles * random.uniform(480, 720))
            return f"{seconds // 3600}:{(seconds % 3600) // 60:02d}", seconds

        entrants = []
        held_back = None

        for i in range(args.teams):
            lines = random.choice(line_choices_team)
            is_held_back = (i == hold_back_index)
            names = [next(name_iter) for _ in range(team_sizes[i])]

            captain_user = make_user(names[0])
            captain_client = new_client(captain_user, f"{team_names[i]}/captain")
            captain = SimUser(captain_user.id, captain_user.name, captain_user.email, captain_client)

            duration_str, duration_seconds = random_duration(lines)
            password = secrets.token_hex(3) if random.random() < 0.3 else None

            resp = captain_client.create_team(
                team_name=team_names[i], format='Team', lines=lines.value,
                estimated_duration=duration_str, comments='',
                password=password or '', email_opt_in='true',
            )
            if resp.status_code != 201:
                log(f"  ! Failed to create team '{team_names[i]}': {resp.status_code} {resp.text[:200]}")
                continue
            team_id = resp.json()['team_id']

            # Captain fills their own preferences while still PENDING.
            captain_client.join_team(team_id=team_id, **random_preferences(lines))

            entrant = Entrant(team_id=team_id, name=team_names[i], format='Team', lines=lines,
                               captain=captain, members=[captain], password=password)

            if is_held_back:
                held_back = entrant
                log(f"  Team '{team_names[i]}' created and held back PENDING (never approved).")
                entrants.append(entrant)
                continue

            approve_resp = admin_client.approve_team(team_id)
            entrant.approved = approve_resp.status_code == 200
            if not entrant.approved:
                log(f"  ! Approval failed for '{team_names[i]}': {approve_resp.status_code}")

            for member_name in names[1:]:
                member_user = make_user(member_name)
                member_client = new_client(member_user, f"{team_names[i]}/{member_name}")
                join_kwargs = random_preferences(lines)
                if password:
                    join_kwargs['team_password'] = password
                resp = member_client.join_team(team_id=team_id, **join_kwargs)
                if resp.status_code == 201:
                    entrant.members.append(SimUser(member_user.id, member_user.name, member_user.email, member_client))
                else:
                    log(f"  ! {member_name} failed to join '{team_names[i]}': {resp.status_code} {resp.text[:150]}")

            entrants.append(entrant)
            log(f"  Team '{team_names[i]}' ({lines.value}, {len(entrant.members)}/{team_sizes[i]} joined"
                f"{', password-protected' if password else ''})")

        for i in range(args.solos):
            lines = random.choice(line_choices_solo)
            captain_user = make_user(next(name_iter))
            captain_client = new_client(captain_user, f"{solo_names[i]}/solo")
            captain = SimUser(captain_user.id, captain_user.name, captain_user.email, captain_client)

            duration_str, duration_seconds = random_duration(lines)
            resp = captain_client.create_team(
                team_name=solo_names[i], format='Solo', lines=lines.value,
                estimated_duration=duration_str, comments='', password='', email_opt_in='true',
            )
            if resp.status_code != 201:
                log(f"  ! Failed to create solo entry '{solo_names[i]}': {resp.status_code} {resp.text[:200]}")
                continue
            team_id = resp.json()['team_id']
            admin_client.approve_team(team_id)

            entrant = Entrant(team_id=team_id, name=solo_names[i], format='Solo', lines=lines,
                               captain=captain, members=[captain], approved=True)
            entrants.append(entrant)
            log(f"  Solo entry '{solo_names[i]}' ({lines.value}) approved.")

        db.session.commit()

        # Gallery hashes for the live-watch links printed at the end.
        for entrant in entrants:
            team = db.session.get(Team, entrant.team_id)
            entrant.gallery_hash = team.gallery_hash

        # --- Login cookies, printed now (not after the race) so you can log
        # in and start watching before the timed simulation even begins. ---
        print()
        print("=" * 72)
        print(f"Login cookies (cookie name: '{admin_client.cookie_name}' -- set it in your browser's")
        print("  devtools, or send 'Cookie: <name>=<value>' as a header, to browse as that user):")
        print(f"  {'ROLE':<9s} {'ENTRY':<26s} {'NAME':<22s} {'EMAIL':<32s} COOKIE VALUE")
        print(f"  {'admin':<9s} {'':<26s} {admin_user.name:<22s} {admin_user.email:<32s} {admin_client.cookie_value}")
        for entrant in entrants:
            entry_label = entrant.name if entrant.approved else f"{entrant.name} [PENDING]"
            for i, m in enumerate(entrant.members):
                role = 'captain' if i == 0 else 'member'
                print(f"  {role:<9s} {entry_label:<26s} {m.name:<22s} {m.email:<32s} {m.client.cookie_value}")
        print("=" * 72)
        print()

        # --- Optional: populate the leg-assignment board ------------------
        if not args.no_assign_legs:
            log("Populating leg-assignment boards...")
            for entrant in entrants:
                if entrant.format != 'Team' or not entrant.approved or len(entrant.members) < 2:
                    continue
                board = entrant.captain.client.get_assignments(entrant.team_id).json()
                legs = board['course']['legs']
                active_members = board['members']
                if not legs or not active_members:
                    continue
                assignments = [
                    {
                        'start_exchange': leg['start']['id'],
                        'end_exchange': leg['end']['id'],
                        'membership_id': active_members[idx % len(active_members)]['membership_id'],
                    }
                    for idx, leg in enumerate(legs)
                ]
                entrant.captain.client.put_assignments(entrant.team_id, assignments)

        # --- Build each entrant's race-day timeline ------------------------
        from app.config import Config as RuntimeConfig
        max_photos = RuntimeConfig.MAX_PHOTOS_PER_TEAM

        for entrant in entrants:
            if not entrant.approved:
                continue
            course_lines = course_lines_for(entrant.lines)
            legs = course_service.legs_for(course_lines)
            exchanges = course_service.exchanges_by_id()
            if not legs:
                continue

            team = db.session.get(Team, entrant.team_id)
            total_seconds = team.estimated_duration_seconds
            total_distance = sum(leg['distance_mi'] for leg in legs)

            points = [{'exchange_id': legs[0]['start_exchange'], 'offset_seconds': 0}]
            cumulative = 0.0
            for leg in legs:
                cumulative += leg['distance_mi']
                points.append({
                    'exchange_id': leg['end_exchange'],
                    'offset_seconds': int(total_seconds * (cumulative / total_distance)) if total_distance else 0,
                })

            if len(points) > max_photos:
                middle = points[1:-1]
                keep = sorted(random.sample(range(len(middle)), max_photos - 2))
                points = [points[0]] + [middle[i] for i in keep] + [points[-1]]

            timeline = []
            for point in points:
                exch = exchanges.get(point['exchange_id'])
                if not exch:
                    continue
                lon, lat = exch['coordinates'][0], exch['coordinates'][1]
                timeline.append({
                    'exchange_id': point['exchange_id'],
                    'name': exch['name'],
                    'lat': lat, 'lon': lon,
                    'offset_seconds': point['offset_seconds'],
                })
            entrant.timeline = timeline

    # --- Race day: play out each entrant's timeline concurrently, in real
    # (compressed) time ---------------------------------------------------
    race_entrants = [e for e in entrants if e.approved and e.timeline]
    log(f"Race day starting: {len(race_entrants)} entrants on course "
        f"({args.race_seconds_per_hour}s real time per race hour).")

    stats = {'uploaded': 0, 'skipped': 0, 'no_gps': 0, 'failed': 0}
    stats_lock = threading.Lock()
    scale = args.race_seconds_per_hour / 3600.0
    start = time.monotonic()

    def run_entrant(entrant):
        for seq, point in enumerate(entrant.timeline, start=1):
            target = start + point['offset_seconds'] * scale
            delay = target - time.monotonic() + random.uniform(-1, 1)
            if delay > 0:
                time.sleep(delay)

            if random.random() < args.skip_prob:
                with stats_lock:
                    stats['skipped'] += 1
                log(f"  {entrant.name}: missed the photo at {point['name']}.")
                continue

            uploader = random.choice(entrant.members)
            has_gps = random.random() >= args.no_gps_prob
            capture_dt = _race_start() + timedelta(seconds=point['offset_seconds'])
            gps = (_jittered(point['lat']), _jittered(point['lon'])) if has_gps else None
            jpeg_bytes, filename = photo_factory.build(capture_dt, gps, filename=f"exchange_{seq:02d}.jpg")

            resp = uploader.client.upload_image(entrant.team_id, jpeg_bytes, filename)
            tag = '' if has_gps else ' (no GPS)'
            with stats_lock:
                if resp.status_code == 200:
                    stats['uploaded'] += 1
                    if not has_gps:
                        stats['no_gps'] += 1
                    log(f"  {entrant.name}: {uploader.name} uploaded a photo at {point['name']}{tag}.")
                else:
                    stats['failed'] += 1
                    log(f"  {entrant.name}: {uploader.name}'s upload at {point['name']} FAILED "
                        f"({resp.status_code}: {resp.text[:150]})")

    threads = [threading.Thread(target=run_entrant, args=(e,)) for e in race_entrants]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    elapsed = time.monotonic() - start
    log(f"Race day complete in {elapsed:.0f}s real time. "
        f"{stats['uploaded']} uploaded ({stats['no_gps']} without GPS), "
        f"{stats['skipped']} missed, {stats['failed']} failed.")

    # --- Summary --------------------------------------------------------
    print()
    print("=" * 72)
    held_back_note = f", 1 held back PENDING: '{held_back.name}'" if held_back else ""
    print(f"Teams:    {sum(1 for e in entrants if e.format == 'Team')} "
          f"({sum(1 for e in entrants if e.format == 'Team' and e.approved)} approved{held_back_note})")
    print(f"Solos:    {sum(1 for e in entrants if e.format == 'Solo')}")
    print(f"Orphans:  {len(orphan_users)} accounts created, never joined a team")
    print(f"Total accounts: {1 + sum(len(e.members) for e in entrants) + len(orphan_users)}")
    print()
    print(f"Live, no-login links (served by {base_url}):")
    print(f"  {base_url}/stats.json")
    print(f"  {base_url}/results.json")
    for e in entrants:
        if e.approved:
            print(f"  {base_url}/gallery/{e.gallery_hash}   ({e.name})")
    print("=" * 72)

    if args.no_hold:
        server.shutdown()
        return

    print(f"\nServer still running at {base_url} -- press Enter (or Ctrl-C) to stop it.")
    try:
        input()
    except EOFError:
        # Not attached to an interactive terminal (nohup/CI/background run):
        # there's nothing to wait on, so just stay up for Ctrl-C instead.
        log("Not attached to a terminal -- staying up. Ctrl-C to stop.")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
    except KeyboardInterrupt:
        pass
    server.shutdown()


def _race_start():
    from app.config import Config
    return Config.EVENT_START_TIME


if __name__ == '__main__':
    main()
