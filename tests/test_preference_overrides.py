"""Endpoint tests for captain overrides of stated member preferences.

Covers permissions (captain/admin write, member read-only, note privacy),
validation (bounds, unknown fields, off-course stations, the null-vs-absent
distinction), and the effect that matters: the board serves the *effective*
preference values, so badges and the solver see the captain's adjustment
without either of them knowing overrides exist.
"""

import pytest

from tests.conftest import login
from tests.test_assignments import seeded, _make_user  # noqa: F401  (fixture reuse)


def _url(seeded, membership_key='runner_membership_id'):
    return f"/team/{seeded['team_id']}/members/{seeded[membership_key]}/preference-overrides"


def _board(client, seeded):
    return client.get(f"/team/{seeded['team_id']}/assignments").get_json()


def _member(client, seeded, membership_key='runner_membership_id'):
    members = {m['membership_id']: m for m in _board(client, seeded)['members']}
    return members[seeded[membership_key]]


# --- Permissions ---

def test_override_requires_login(client, seeded):
    response = client.put(_url(seeded), json={'overrides': {'preferred_miles': 3.0}})
    assert response.status_code in (302, 401)


def test_member_cannot_override(client, seeded):
    login(client, seeded['runner_id'])
    response = client.put(_url(seeded), json={'overrides': {'preferred_miles': 3.0}})
    assert response.status_code == 403


def test_outsider_cannot_override(client, seeded):
    login(client, seeded['outsider_id'])
    assert client.put(_url(seeded), json={'overrides': {}}).status_code == 403


def test_admin_can_override(client, seeded):
    login(client, seeded['admin_id'])
    response = client.put(_url(seeded), json={'overrides': {'preferred_miles': 3.0}})
    assert response.status_code == 200


def test_override_on_foreign_membership_is_404(client, seeded):
    login(client, seeded['captain_id'])
    url = f"/team/{seeded['team_id']}/members/{seeded['foreign_membership_id']}/preference-overrides"
    assert client.put(url, json={'overrides': {'preferred_miles': 3.0}}).status_code == 404


# --- Effective values ---

def test_override_replaces_the_value_the_board_reasons_with(client, seeded):
    login(client, seeded['captain_id'])
    assert _member(client, seeded)['preferred_miles'] == 6.5

    response = client.put(_url(seeded), json={
        'overrides': {'preferred_miles': 4.0, 'planned_pace_seconds': 660},
        'note': 'Coming back from a calf strain',
    })
    assert response.status_code == 200

    member = _member(client, seeded)
    # Effective values ride in the same keys the metrics/solver already read.
    assert member['preferred_miles'] == 4.0
    assert member['planned_pace_seconds'] == 660
    # ...with the member's own answers preserved alongside them.
    assert member['stated']['preferred_miles'] == 6.5
    assert member['stated']['planned_pace_seconds'] == 600
    assert member['overrides'] == {'preferred_miles': 4.0, 'planned_pace_seconds': 660}


def test_null_override_drops_the_preference_entirely(client, seeded):
    """Distinct from "no override": the constraint stops being scored at all."""
    login(client, seeded['captain_id'])
    response = client.put(_url(seeded, 'captain_membership_id'),
                          json={'overrides': {'preferred_station': None}})
    assert response.status_code == 200

    member = _member(client, seeded, 'captain_membership_id')
    assert member['preferred_station'] is None
    assert member['stated']['preferred_station'] == 'Northgate'
    assert member['overrides'] == {'preferred_station': None}


def test_omitted_field_keeps_the_stated_value(client, seeded):
    login(client, seeded['captain_id'])
    client.put(_url(seeded), json={'overrides': {'preferred_miles': 4.0}})

    member = _member(client, seeded)
    assert member['planned_pace_seconds'] == 600      # untouched
    assert 'planned_pace_seconds' not in member['overrides']


def test_override_equal_to_the_stated_value_is_not_recorded(client, seeded):
    """Otherwise a chip would read as permanently "adjusted" while saying nothing."""
    login(client, seeded['captain_id'])
    response = client.put(_url(seeded), json={'overrides': {'preferred_miles': 6.5}})
    assert response.status_code == 200
    assert _member(client, seeded)['overrides'] == {}


def test_willing_to_lead_override(client, seeded):
    login(client, seeded['captain_id'])
    assert client.put(_url(seeded), json={'overrides': {'willing_to_lead': True}}).status_code == 200

    member = _member(client, seeded)
    assert member['willing_to_lead'] is True
    assert member['stated']['willing_to_lead'] is False


# --- Clearing ---

def test_empty_overrides_clear_the_record(client, seeded):
    login(client, seeded['captain_id'])
    client.put(_url(seeded), json={'overrides': {'preferred_miles': 4.0}, 'note': 'why'})

    assert client.put(_url(seeded), json={'overrides': {}}).status_code == 200
    member = _member(client, seeded)
    assert member['overrides'] == {}
    assert member['preferred_miles'] == 6.5
    assert member['override_note'] is None


def test_delete_reverts_to_stated_preferences(client, seeded):
    login(client, seeded['captain_id'])
    client.put(_url(seeded), json={'overrides': {'preferred_miles': 4.0}})

    response = client.delete(_url(seeded))
    assert response.status_code == 200
    assert response.get_json()['member']['preferred_miles'] == 6.5
    assert _member(client, seeded)['overrides'] == {}


def test_delete_without_an_override_is_a_no_op(client, seeded):
    login(client, seeded['captain_id'])
    assert client.delete(_url(seeded)).status_code == 200


# --- Privacy: overrides are a captain-side planning aid ---

def test_overrides_are_invisible_to_the_rest_of_the_team(client, seeded):
    login(client, seeded['captain_id'])
    client.put(_url(seeded), json={
        'overrides': {'preferred_miles': 4.0}, 'note': 'Injured, agreed in person',
    })
    captain_view = _member(client, seeded)
    assert captain_view['preferred_miles'] == 4.0
    assert captain_view['overrides'] == {'preferred_miles': 4.0}
    assert captain_view['override_note'] == 'Injured, agreed in person'

    # A member's board is the pre-override one: their own stated values, with
    # no sign that an adjustment exists at all.
    login(client, seeded['runner_id'])
    member_view = _member(client, seeded)
    assert member_view['preferred_miles'] == 6.5
    for key in ('overrides', 'stated', 'stale_override_fields', 'override_note'):
        assert key not in member_view


def test_admin_sees_the_captains_adjustments(client, seeded):
    login(client, seeded['captain_id'])
    client.put(_url(seeded), json={'overrides': {'preferred_miles': 4.0}, 'note': 'Injured'})

    login(client, seeded['admin_id'])
    member = _member(client, seeded)
    assert member['preferred_miles'] == 4.0
    assert member['override_note'] == 'Injured'


def test_members_page_hides_adjustments_from_members(client, seeded):
    login(client, seeded['captain_id'])
    client.put(_url(seeded), json={'overrides': {'preferred_miles': 4.0}})
    assert 'Adjusted for leg assignments:' in (
        client.get(f"/team/{seeded['team_id']}/members").get_data(as_text=True)
    )

    login(client, seeded['runner_id'])
    body = client.get(f"/team/{seeded['team_id']}/members").get_data(as_text=True)
    assert 'Adjusted for leg assignments:' not in body
    assert '4 mi' not in body


# --- Validation ---

def test_rejects_non_object_body(client, seeded):
    login(client, seeded['captain_id'])
    assert client.put(_url(seeded), json=[1, 2, 3]).status_code == 400


def test_rejects_unknown_field(client, seeded):
    login(client, seeded['captain_id'])
    response = client.put(_url(seeded), json={'overrides': {'favourite_colour': 'blue'}})
    assert response.status_code == 400
    assert 'Unknown preference field' in response.get_json()['error']


@pytest.mark.parametrize('miles', [0, -1, 0.05, 500, 'far', True])
def test_rejects_out_of_range_miles(client, seeded, miles):
    login(client, seeded['captain_id'])
    response = client.put(_url(seeded), json={'overrides': {'preferred_miles': miles}})
    assert response.status_code == 400


@pytest.mark.parametrize('pace', [0, 30, 99999, '9:30', 540.5, True])
def test_rejects_bad_pace(client, seeded, pace):
    """Paces are whole seconds per mile; the MM:SS parsing happens in the UI."""
    login(client, seeded['captain_id'])
    response = client.put(_url(seeded), json={'overrides': {'planned_pace_seconds': pace}})
    assert response.status_code == 400


def test_rejects_station_off_the_teams_course(client, seeded):
    login(client, seeded['captain_id'])
    response = client.put(_url(seeded), json={'overrides': {'preferred_station': 'Narnia'}})
    assert response.status_code == 400
    assert 'not a station' in response.get_json()['error']


def test_rejects_null_willing_to_lead(client, seeded):
    """Non-nullable on the membership: reverting means omitting the field."""
    login(client, seeded['captain_id'])
    response = client.put(_url(seeded), json={'overrides': {'willing_to_lead': None}})
    assert response.status_code == 400


def test_a_captain_cannot_set_what_a_member_could_not_have_stated(client, seeded):
    """Bounds match registration: the whole course is too far for one runner."""
    from app.models import TeamLines
    from app.utils import max_preferred_miles

    login(client, seeded['captain_id'])
    over_cap = max_preferred_miles(TeamLines.ONE) + 1
    response = client.put(_url(seeded), json={'overrides': {'preferred_miles': over_cap}})
    assert response.status_code == 400


# --- Staleness ---

def test_override_is_flagged_when_the_member_changes_their_answer(client, seeded):
    login(client, seeded['captain_id'])
    client.put(_url(seeded), json={'overrides': {'preferred_miles': 4.0}})
    assert _member(client, seeded)['stale_override_fields'] == []

    from app.models import db, TeamMembership
    with client.application.app_context():
        membership = db.session.get(TeamMembership, seeded['runner_membership_id'])
        membership.preferred_miles = 3.0
        db.session.commit()

    member = _member(client, seeded)
    # The captain's value still wins -- silently outvoting a fresh answer is
    # the failure mode, so the board says it out loud instead.
    assert member['preferred_miles'] == 4.0
    assert member['stale_override_fields'] == ['preferred_miles']


def test_unrelated_edits_do_not_make_an_override_stale(client, seeded):
    login(client, seeded['captain_id'])
    client.put(_url(seeded), json={'overrides': {'preferred_miles': 4.0}})

    from app.models import db, TeamMembership
    with client.application.app_context():
        membership = db.session.get(TeamMembership, seeded['runner_membership_id'])
        membership.planned_pace_seconds = 555
        db.session.commit()

    assert _member(client, seeded)['stale_override_fields'] == []


# --- Lifecycle / reporting ---

def test_override_is_deleted_with_its_membership(client, seeded):
    login(client, seeded['captain_id'])
    client.put(_url(seeded), json={'overrides': {'preferred_miles': 4.0}})

    from app.models import db, MembershipPreferenceOverride, TeamMembership
    with client.application.app_context():
        membership = db.session.get(TeamMembership, seeded['runner_membership_id'])
        db.session.delete(membership)
        db.session.commit()
        assert MembershipPreferenceOverride.query.count() == 0


def test_member_export_reports_stated_values_and_the_adjustment(client, seeded):
    login(client, seeded['captain_id'])
    client.put(_url(seeded, 'captain_membership_id'), json={
        'overrides': {'preferred_miles': 4.0, 'preferred_station': None},
        'note': 'Injured',
    })

    body = client.get(f"/team/{seeded['team_id']}/members/csv").get_data(as_text=True)
    row = next(line for line in body.splitlines() if line.startswith('Cap Tain'))
    assert '5.0' in row and 'Northgate' in row   # what the member actually said
    assert 'miles 5 mi -> 4 mi' in row
    assert 'end station Northgate -> no preference' in row
    assert 'Injured' in row


# --- Template wiring ---

def test_board_page_renders_the_override_dialog_for_the_captain_only(client, seeded):
    login(client, seeded['captain_id'])
    body = client.get(f"/team/{seeded['team_id']}/legs").get_data(as_text=True)
    assert 'id="override-modal"' in body
    assert 'data-override-field="preferred_miles"' in body
    # Station choices come from the team's own line, like the registration form.
    assert 'Northgate' in body

    login(client, seeded['runner_id'])
    body = client.get(f"/team/{seeded['team_id']}/legs").get_data(as_text=True)
    assert 'id="override-modal"' not in body


def test_members_page_shows_stated_values_and_names_the_adjustment(client, seeded):
    login(client, seeded['captain_id'])
    client.put(_url(seeded), json={'overrides': {'preferred_miles': 4.0}})

    body = client.get(f"/team/{seeded['team_id']}/members").get_data(as_text=True)
    assert '6.5' in body                              # the member's own answer, still shown
    assert 'Adjusted for leg assignments:' in body
    assert 'miles 6.5 mi -&gt; 4 mi' in body
