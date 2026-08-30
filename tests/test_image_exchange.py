"""Endpoint tests for manually assigning a photo to an exchange station.

A photo's station is normally derived from its GPS at upload time. When that
is missing or wrong, a team member may override it; these cover the permission
gate, validation against the real course, setting and clearing the override,
and the precedence the override has everywhere the station is read (gallery
and results.json).

Station ids are resolved from the real course rather than hardcoded, like the
leg-assignment tests.
"""

import datetime

import pytest

from tests.conftest import login


def _station_ids():
    """Two real exchange ids, as the Image columns store them (strings)."""
    from app.utils import load_exchange_points

    ids = [str(exchange_id) for exchange_id in load_exchange_points()]
    assert len(ids) >= 2, 'the course should have at least two exchanges'
    return ids[0], ids[1]


def _make_user(db, User, OAuthProvider, email, name, is_admin=False):
    from app.models import UserRole
    user = User(
        email=email,
        name=name,
        provider=OAuthProvider.GOOGLE,
        provider_id=email,
        role=UserRole.ADMIN if is_admin else UserRole.PARTICIPANT,
    )
    db.session.add(user)
    return user


@pytest.fixture()
def seeded(app):
    """An open team with a captain, a member, an outsider, one photo that GPS
    placed at a station and one it could not place, plus a second team with a
    photo of its own."""
    from app.config import Config
    from app.models import (
        db, User, Team, TeamMembership, TeamFormat, TeamStatus, Image,
        OAuthProvider,
    )

    gps_station, _ = _station_ids()
    capture_time = (
        Config.EVENT_START_TIME.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        + datetime.timedelta(hours=1)
    )

    with app.app_context():
        captain = _make_user(db, User, OAuthProvider, 'captain@example.com', 'Cap Tain')
        runner = _make_user(db, User, OAuthProvider, 'runner@example.com', 'Run Ner')
        outsider = _make_user(db, User, OAuthProvider, 'outsider@example.com', 'Out Sider')
        other_captain = _make_user(db, User, OAuthProvider, 'other@example.com', 'Other Cap')
        db.session.flush()

        team = Team(
            name='Test Team', format=TeamFormat.TEAM,
            estimated_duration_seconds=6 * 3600,
            status=TeamStatus.OPEN, captain_id=captain.id,
        )
        other_team = Team(
            name='Other Team', format=TeamFormat.TEAM,
            estimated_duration_seconds=6 * 3600,
            status=TeamStatus.OPEN, captain_id=other_captain.id,
        )
        db.session.add_all([team, other_team])
        db.session.flush()

        db.session.add_all([
            TeamMembership(user_id=captain.id, team_id=team.id),
            TeamMembership(user_id=runner.id, team_id=team.id),
            TeamMembership(user_id=other_captain.id, team_id=other_team.id),
        ])

        located = Image(
            filename='located.jpg', file_hash='hash-located',
            file_path=f'{team.id}/located.jpg', team_id=team.id,
            uploaded_by=runner.id, capture_time=capture_time,
            gps_lat=47.6, gps_lng=-122.3,
            associated_exchange_id=gps_station,
        )
        unlocated = Image(
            filename='unlocated.jpg', file_hash='hash-unlocated',
            file_path=f'{team.id}/unlocated.jpg', team_id=team.id,
            uploaded_by=runner.id, capture_time=capture_time + datetime.timedelta(minutes=5),
        )
        foreign = Image(
            filename='foreign.jpg', file_hash='hash-foreign',
            file_path=f'{other_team.id}/foreign.jpg', team_id=other_team.id,
            uploaded_by=other_captain.id, capture_time=capture_time,
        )
        db.session.add_all([located, unlocated, foreign])
        db.session.commit()

        return {
            'team_id': team.id,
            'gallery_hash': team.gallery_hash,
            'captain_id': captain.id,
            'runner_id': runner.id,
            'outsider_id': outsider.id,
            'located_image_id': located.id,
            'unlocated_image_id': unlocated.id,
            'foreign_image_id': foreign.id,
            'gps_station': gps_station,
        }


def _url(seeded, image_id):
    return f"/team/{seeded['team_id']}/images/{image_id}/exchange"


def _post(client, seeded, image_id, payload):
    return client.post(_url(seeded, image_id), json=payload)


def _stored(client, image_id):
    """(manual_exchange_id, associated_exchange_id) as persisted."""
    from app.models import db, Image

    with client.application.app_context():
        image = db.session.get(Image, image_id)
        return image.manual_exchange_id, image.associated_exchange_id


# --- Permissions ---

def test_requires_login(client, seeded):
    response = _post(client, seeded, seeded['located_image_id'], {'exchange_id': None})
    assert response.status_code in (302, 401)


def test_non_member_denied(client, seeded):
    _, other_station = _station_ids()
    login(client, seeded['outsider_id'])
    response = _post(client, seeded, seeded['located_image_id'], {'exchange_id': other_station})
    assert response.status_code == 403
    assert _stored(client, seeded['located_image_id'])[0] is None


def test_other_teams_image_not_found(client, seeded):
    """An image the team doesn't own is not addressable through its own URL."""
    _, other_station = _station_ids()
    login(client, seeded['captain_id'])
    response = _post(client, seeded, seeded['foreign_image_id'], {'exchange_id': other_station})
    assert response.status_code == 404
    assert _stored(client, seeded['foreign_image_id'])[0] is None


# --- Validation ---

@pytest.mark.parametrize('payload', [
    {'nope': True},           # no exchange_id at all
    [],                       # not an object
])
def test_rejects_malformed_body(client, seeded, payload):
    login(client, seeded['runner_id'])
    response = _post(client, seeded, seeded['located_image_id'], payload)
    assert response.status_code == 400
    assert 'exchange_id' in response.get_json()['error']


@pytest.mark.parametrize('bad_station', ['999', 'Northgate', '0'])
def test_rejects_station_not_on_the_course(client, seeded, bad_station):
    login(client, seeded['runner_id'])
    response = _post(client, seeded, seeded['located_image_id'], {'exchange_id': bad_station})
    assert response.status_code == 400
    assert 'not an exchange on this course' in response.get_json()['error']
    # The automatic association is left exactly as it was.
    assert _stored(client, seeded['located_image_id']) == (None, seeded['gps_station'])


@pytest.mark.parametrize('bad_value', [True, 1.5, {'id': '168'}, ['168']])
def test_rejects_non_id_value(client, seeded, bad_value):
    """bool is a subclass of int, so True must be rejected explicitly."""
    login(client, seeded['runner_id'])
    response = _post(client, seeded, seeded['located_image_id'], {'exchange_id': bad_value})
    assert response.status_code == 400
    assert _stored(client, seeded['located_image_id'])[0] is None


# --- Setting and clearing ---

def test_member_can_override_the_gps_match(client, seeded):
    _, other_station = _station_ids()
    login(client, seeded['runner_id'])
    response = _post(client, seeded, seeded['located_image_id'], {'exchange_id': other_station})
    assert response.status_code == 200

    body = response.get_json()
    assert body['success'] is True
    assert body['image_id'] == seeded['located_image_id']
    assert body['exchange']['id'] == other_station
    assert body['exchange']['source'] == 'manual'
    assert isinstance(body['exchange']['name'], str)
    assert body['exchange']['station_code'] == other_station[-2:]
    assert body['exchange']['line_codes']

    # The GPS-derived association is preserved underneath the override.
    assert _stored(client, seeded['located_image_id']) == (other_station, seeded['gps_station'])


def test_can_assign_a_photo_gps_never_placed(client, seeded):
    _, other_station = _station_ids()
    login(client, seeded['runner_id'])
    response = _post(client, seeded, seeded['unlocated_image_id'], {'exchange_id': other_station})
    assert response.status_code == 200
    assert response.get_json()['exchange']['id'] == other_station
    assert _stored(client, seeded['unlocated_image_id']) == (other_station, None)


def test_integer_station_id_accepted(client, seeded):
    """Ids are stored as strings but a client may send the number."""
    _, other_station = _station_ids()
    login(client, seeded['runner_id'])
    response = _post(client, seeded, seeded['located_image_id'], {'exchange_id': int(other_station)})
    assert response.status_code == 200
    assert _stored(client, seeded['located_image_id'])[0] == other_station


@pytest.mark.parametrize('cleared', [None, '', '  '])
def test_clearing_falls_back_to_the_gps_match(client, seeded, cleared):
    _, other_station = _station_ids()
    login(client, seeded['runner_id'])
    assert _post(client, seeded, seeded['located_image_id'],
                 {'exchange_id': other_station}).status_code == 200

    response = _post(client, seeded, seeded['located_image_id'], {'exchange_id': cleared})
    assert response.status_code == 200
    exchange = response.get_json()['exchange']
    assert exchange['id'] == seeded['gps_station']
    assert exchange['source'] == 'gps'
    assert _stored(client, seeded['located_image_id']) == (None, seeded['gps_station'])


def test_clearing_a_photo_without_gps_leaves_no_station(client, seeded):
    _, other_station = _station_ids()
    login(client, seeded['runner_id'])
    assert _post(client, seeded, seeded['unlocated_image_id'],
                 {'exchange_id': other_station}).status_code == 200

    response = _post(client, seeded, seeded['unlocated_image_id'], {'exchange_id': None})
    assert response.status_code == 200
    assert response.get_json()['exchange'] is None
    assert _stored(client, seeded['unlocated_image_id']) == (None, None)


# --- What the override feeds ---

def test_gallery_shows_the_override_and_the_public_one_stays_read_only(client, seeded):
    _, other_station = _station_ids()
    login(client, seeded['runner_id'])
    assert _post(client, seeded, seeded['located_image_id'],
                 {'exchange_id': other_station}).status_code == 200

    from app.utils import load_exchange_points

    station_name = load_exchange_points()[int(other_station)]['name']

    private = client.get(f"/team/{seeded['team_id']}/gallery")
    assert private.status_code == 200
    private_html = private.get_data(as_text=True)
    assert '<select class="js-station-select-inline"' in private_html
    # The override, not the station GPS matched, is the one selected on the card.
    assert f'<option value="{other_station}" selected>' in private_html
    assert f'>{station_name}</option>' in private_html

    # Same template, but the public gallery offers no way to edit -- even to a
    # signed-in member who could edit from the private one. It still shows the
    # corrected station.
    public = client.get(f"/gallery/{seeded['gallery_hash']}")
    assert public.status_code == 200
    public_html = public.get_data(as_text=True)
    assert '<select class="js-station-select-inline"' not in public_html
    assert 'id="station-select-' not in public_html
    # Italicized, since it's a manual correction rather than a GPS match.
    assert f'<span tabindex="0" title="{station_name}" class="fst-italic">{station_name}</span>' in public_html


def test_results_prefer_the_manual_override(client, seeded):
    """A manual correction wins in results.json, and counts on its own when
    GPS never produced an association."""
    _, other_station = _station_ids()
    login(client, seeded['runner_id'])
    assert _post(client, seeded, seeded['located_image_id'],
                 {'exchange_id': other_station}).status_code == 200

    body = client.get('/results.json').get_json()
    team_result = next(r for r in body['results'] if r['name'] == 'Test Team')
    assert other_station in team_result['exchangeTimes']
    assert seeded['gps_station'] not in team_result['exchangeTimes']

    # And a photo GPS never placed shows up once someone assigns it.
    gps_station = seeded['gps_station']
    assert _post(client, seeded, seeded['unlocated_image_id'],
                 {'exchange_id': gps_station}).status_code == 200
    body = client.get('/results.json').get_json()
    team_result = next(r for r in body['results'] if r['name'] == 'Test Team')
    assert gps_station in team_result['exchangeTimes']
