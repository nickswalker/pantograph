"""Public photo observations preserve scoring and expose no private identifiers."""
import datetime

from tests.test_image_exchange import seeded  # noqa: F401


def test_observations_use_capture_time_and_manual_exchange(app, client, seeded):
    from app.models import Image, db
    with app.app_context():
        photo = db.session.get(Image, seeded['located_image_id'])
        photo.manual_exchange_id = 'manual'
        photo.upload_time = photo.capture_time + datetime.timedelta(hours=2)
        captured = photo.capture_time.replace(tzinfo=datetime.UTC).isoformat()
        uploaded = photo.upload_time.replace(tzinfo=datetime.UTC).isoformat()
        db.session.commit()
    response = client.get('/results.json')
    row = next(row for row in response.json['results'] if row['name'] == 'Test Team')
    assert row['observations'] == {'manual': {'capturedAt': captured, 'uploadedAt': uploaded}}
    assert row['exchangeTimes']['manual'] == 3600
    assert set(row) == {'name', 'category', 'teamSize', 'lines', 'exchangeTimes', 'observations'}
    assert response.json['lastUpdated'].endswith('+00:00')
    assert response.headers['Cache-Control'] == 'no-cache'


def test_start_observation_is_not_clamped(app, client, seeded):
    from app.config import Config
    from app.models import Image, db
    from app.blueprints.main import _line_start_exchanges
    with app.app_context():
        photo = db.session.get(Image, seeded['located_image_id'])
        start = _line_start_exchanges()['1']
        photo.manual_exchange_id = start
        photo.capture_time = Config.EVENT_START_TIME.astimezone(datetime.UTC).replace(tzinfo=None) - datetime.timedelta(minutes=5)
        captured = photo.capture_time.replace(tzinfo=datetime.UTC).isoformat()
        db.session.commit()
    row = next(row for row in client.get('/results.json').json['results'] if row['name'] == 'Test Team')
    assert row['exchangeTimes'][start] == 0
    assert row['observations'][start]['capturedAt'] == captured


def test_unchanged_results_revalidate_to_304(client, seeded):
    first = client.get('/results.json')
    etag = first.headers['ETag']
    again = client.get('/results.json', headers={'If-None-Match': etag})
    assert again.status_code == 304
    assert again.data == b''
    assert again.headers['Access-Control-Allow-Origin'] == '*'


def test_results_are_cached_for_the_configured_window(app, client, seeded):
    from app.models import Image, db
    app.config['RESULTS_CACHE_SECONDS'] = 60
    first = client.get('/results.json')
    with app.app_context():
        photo = db.session.get(Image, seeded['located_image_id'])
        photo.manual_exchange_id = 'manual'
        db.session.commit()
    cached = client.get('/results.json')
    assert cached.data == first.data
    assert cached.headers['ETag'] == first.headers['ETag']

    # Once the window lapses the change shows up, under a new ETag.
    app.extensions['results_cache']['expires'] = 0
    fresh = client.get('/results.json', headers={'If-None-Match': first.headers['ETag']})
    assert fresh.status_code == 200
    assert 'manual' in next(r for r in fresh.json['results'] if r['name'] == 'Test Team')['exchangeTimes']


def _put_both_photos_at_merge(app, seeded, lines):
    """Place the seeded team's two photos (an hour in, and five minutes
    later) at the exchange where the branches join."""
    from app.blueprints.main import _merge_exchange
    from app.models import Image, Team, db
    with app.app_context():
        merge = _merge_exchange()
        db.session.get(Team, seeded['team_id']).lines = lines
        first = db.session.get(Image, seeded['located_image_id'])
        first.manual_exchange_id = merge
        captured = first.capture_time.replace(tzinfo=datetime.UTC).isoformat()
        for photo in Image.query.filter_by(team_id=seeded['team_id']):
            photo.manual_exchange_id = merge
        db.session.commit()
    return merge, captured


def test_interline_team_reports_both_arrivals_at_the_merge(app, client, seeded):
    from app.models import TeamLines
    merge, captured = _put_both_photos_at_merge(app, seeded, TeamLines.BOTH)
    body = client.get('/results.json').json
    row = next(row for row in body['results'] if row['name'] == 'Test Team')
    assert body['merge'] == {'exchange': merge}
    # The split is still the later photo; the earlier one rides alongside.
    assert row['exchangeTimes'][merge] == 3900
    assert row['earlierArrivals'][merge]['time'] == 3600
    assert row['earlierArrivals'][merge]['capturedAt'] == captured


def test_single_merge_photo_is_not_an_earlier_arrival(app, client, seeded):
    from app.blueprints.main import _merge_exchange
    from app.models import Image, Team, TeamLines, db
    with app.app_context():
        merge = _merge_exchange()
        db.session.get(Team, seeded['team_id']).lines = TeamLines.BOTH
        db.session.get(Image, seeded['located_image_id']).manual_exchange_id = merge
        db.session.commit()
    row = next(row for row in client.get('/results.json').json['results'] if row['name'] == 'Test Team')
    assert row['exchangeTimes'][merge] == 3600
    assert row['earlierArrivals'] == {}


def test_single_line_team_has_no_earlier_arrivals(app, client, seeded):
    from app.models import TeamLines
    merge, _ = _put_both_photos_at_merge(app, seeded, TeamLines.ONE)
    row = next(row for row in client.get('/results.json').json['results'] if row['name'] == 'Test Team')
    assert row['exchangeTimes'][merge] == 3900
    assert 'earlierArrivals' not in row
