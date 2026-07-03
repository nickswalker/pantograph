"""Endpoint tests for the leg-assignment API.

Covers permission denial (non-captain PUT, non-member GET), validation
failures (unknown/foreign/withdrawn memberships, duplicate legs, bad leg
indexes), and round-trip persistence of the full-replacement PUT.
"""

import pytest

from tests.conftest import login


def _make_user(db, User, OAuthProvider, email, name, is_admin=False):
    user = User(
        email=email,
        name=name,
        provider=OAuthProvider.GOOGLE,
        provider_id=email,
        is_admin=is_admin,
    )
    db.session.add(user)
    return user


@pytest.fixture()
def seeded(app):
    """A team with a captain, two members (one withdrawn), an admin, an
    outsider, and a second team with its own member."""
    from app.models import (
        db, User, Team, TeamMembership, TeamFormat, TeamStatus,
        TeamMembershipStatus, OAuthProvider,
    )

    with app.app_context():
        captain = _make_user(db, User, OAuthProvider, 'captain@example.com', 'Cap Tain')
        runner = _make_user(db, User, OAuthProvider, 'runner@example.com', 'Run Ner')
        quitter = _make_user(db, User, OAuthProvider, 'quitter@example.com', 'Quit Ter')
        outsider = _make_user(db, User, OAuthProvider, 'outsider@example.com', 'Out Sider')
        admin = _make_user(db, User, OAuthProvider, 'admin@example.com', 'Ad Min', is_admin=True)
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

        captain_membership = TeamMembership(
            user_id=captain.id, team_id=team.id, willing_to_lead=True,
            preferred_miles=5.0, planned_pace_seconds=540,
            preferred_station='Northgate',
        )
        runner_membership = TeamMembership(
            user_id=runner.id, team_id=team.id, willing_to_lead=False,
            preferred_miles=6.5, planned_pace_seconds=600,
        )
        withdrawn_membership = TeamMembership(
            user_id=quitter.id, team_id=team.id, willing_to_lead=False,
            status=TeamMembershipStatus.WITHDRAWN,
        )
        foreign_membership = TeamMembership(
            user_id=other_captain.id, team_id=other_team.id, willing_to_lead=True,
        )
        db.session.add_all([
            captain_membership, runner_membership,
            withdrawn_membership, foreign_membership,
        ])
        db.session.commit()

        return {
            'team_id': team.id,
            'captain_id': captain.id,
            'runner_id': runner.id,
            'outsider_id': outsider.id,
            'admin_id': admin.id,
            'captain_membership_id': captain_membership.id,
            'runner_membership_id': runner_membership.id,
            'withdrawn_membership_id': withdrawn_membership.id,
            'foreign_membership_id': foreign_membership.id,
        }


def _url(seeded):
    return f"/team/{seeded['team_id']}/assignments"


def _put(client, seeded, payload):
    return client.put(_url(seeded), json=payload)


# --- Permissions ---

def test_get_requires_login(client, seeded):
    response = client.get(_url(seeded))
    assert response.status_code in (302, 401)


def test_non_member_get_denied(client, seeded):
    login(client, seeded['outsider_id'])
    assert client.get(_url(seeded)).status_code == 403


def test_member_can_get(client, seeded):
    login(client, seeded['runner_id'])
    response = client.get(_url(seeded))
    assert response.status_code == 200
    body = response.get_json()
    assert body['team_id'] == seeded['team_id']
    assert body['assignments'] == []
    course = body['course']
    assert course is not None
    assert course['event'] == 'lrr2026'
    assert 'units' in course
    assert len(course['legs']) == 22
    first_leg = next(leg for leg in course['legs'] if leg['index'] == 0)
    assert set(first_leg.keys()) == {'index', 'start', 'end', 'distance', 'ascent', 'descent'}
    assert set(first_leg['start'].keys()) == {'id', 'name'}
    assert isinstance(first_leg['start']['name'], str)
    assert isinstance(first_leg['end']['name'], str)
    members = {m['membership_id']: m for m in body['members']}
    # Active members are listed; the withdrawn (unassigned) member is not.
    assert seeded['captain_membership_id'] in members
    assert seeded['runner_membership_id'] in members
    assert seeded['withdrawn_membership_id'] not in members
    runner = members[seeded['runner_membership_id']]
    assert runner['name'] == 'Run Ner'
    assert runner['status'] == 'active'
    assert runner['willing_to_lead'] is False
    assert runner['preferred_miles'] == 6.5
    assert runner['planned_pace_seconds'] == 600
    assert runner['preferred_station'] is None
    assert 'avatar_url' in runner and 'user_id' in runner


def test_non_captain_put_denied(client, seeded):
    login(client, seeded['runner_id'])
    response = _put(client, seeded, [
        {'leg_index': 1, 'membership_id': seeded['runner_membership_id']},
    ])
    assert response.status_code == 403


def test_outsider_put_denied(client, seeded):
    login(client, seeded['outsider_id'])
    assert _put(client, seeded, []).status_code == 403


def test_admin_can_put(client, seeded):
    login(client, seeded['admin_id'])
    response = _put(client, seeded, [
        {'leg_index': 3, 'membership_id': seeded['runner_membership_id']},
    ])
    assert response.status_code == 200


# --- Validation ---

def test_put_rejects_non_list_body(client, seeded):
    login(client, seeded['captain_id'])
    response = client.put(_url(seeded), json={'nope': True})
    assert response.status_code == 400


def test_put_rejects_unknown_membership(client, seeded):
    login(client, seeded['captain_id'])
    response = _put(client, seeded, [
        {'leg_index': 1, 'membership_id': 'does-not-exist'},
    ])
    assert response.status_code == 400
    assert 'not part of this team' in response.get_json()['error']


def test_put_rejects_other_teams_membership(client, seeded):
    login(client, seeded['captain_id'])
    response = _put(client, seeded, [
        {'leg_index': 1, 'membership_id': seeded['foreign_membership_id']},
    ])
    assert response.status_code == 400
    assert 'not part of this team' in response.get_json()['error']


def test_put_rejects_withdrawn_membership(client, seeded):
    login(client, seeded['captain_id'])
    response = _put(client, seeded, [
        {'leg_index': 1, 'membership_id': seeded['withdrawn_membership_id']},
    ])
    assert response.status_code == 400
    assert 'active member' in response.get_json()['error']


def test_put_rejects_same_member_twice_on_one_leg(client, seeded):
    login(client, seeded['captain_id'])
    response = _put(client, seeded, [
        {'leg_index': 2, 'membership_id': seeded['runner_membership_id']},
        {'leg_index': 2, 'membership_id': seeded['runner_membership_id']},
    ])
    assert response.status_code == 400
    assert 'more than once' in response.get_json()['error']


def test_two_members_may_share_a_leg(client, seeded):
    login(client, seeded['captain_id'])
    shared = [
        {'leg_index': 2, 'membership_id': seeded['captain_membership_id']},
        {'leg_index': 2, 'membership_id': seeded['runner_membership_id']},
    ]
    response = _put(client, seeded, shared)
    assert response.status_code == 200

    body = client.get(_url(seeded)).get_json()
    assert sorted(body['assignments'], key=lambda a: a['membership_id']) == sorted(
        shared, key=lambda a: a['membership_id']
    )


@pytest.mark.parametrize('bad_leg', [-1, 1.5, 'one', None, True])
def test_put_rejects_bad_leg_index(client, seeded, bad_leg):
    login(client, seeded['captain_id'])
    response = _put(client, seeded, [
        {'leg_index': bad_leg, 'membership_id': seeded['runner_membership_id']},
    ])
    assert response.status_code == 400
    assert 'non-negative integer' in response.get_json()['error']


def test_put_accepts_leg_zero(client, seeded):
    """Leg indexes are 0-based (data/legs_2026.json leg 0 is the first leg),
    so leg_index == 0 must be a valid assignment, not rejected as falsy."""
    login(client, seeded['captain_id'])
    response = _put(client, seeded, [
        {'leg_index': 0, 'membership_id': seeded['runner_membership_id']},
    ])
    assert response.status_code == 200
    assert response.get_json()['assignments'] == [
        {'leg_index': 0, 'membership_id': seeded['runner_membership_id']},
    ]


def test_put_rejects_leg_index_outside_course(client, seeded):
    """The course has 22 legs (indexes 0-21, see data/legs_2026.json); once
    course data is wired in, out-of-range indexes must be rejected."""
    login(client, seeded['captain_id'])
    response = _put(client, seeded, [
        {'leg_index': 22, 'membership_id': seeded['runner_membership_id']},
    ])
    assert response.status_code == 400
    assert 'does not exist in the course' in response.get_json()['error']


def test_put_rejects_missing_membership_id(client, seeded):
    login(client, seeded['captain_id'])
    response = _put(client, seeded, [{'leg_index': 1}])
    assert response.status_code == 400


def test_failed_put_preserves_existing_assignments(client, seeded):
    login(client, seeded['captain_id'])
    good = [{'leg_index': 1, 'membership_id': seeded['captain_membership_id']}]
    assert _put(client, seeded, good).status_code == 200

    bad = [
        {'leg_index': 1, 'membership_id': seeded['runner_membership_id']},
        {'leg_index': 2, 'membership_id': seeded['withdrawn_membership_id']},
    ]
    assert _put(client, seeded, bad).status_code == 400

    body = client.get(_url(seeded)).get_json()
    assert body['assignments'] == [
        {'leg_index': 1, 'membership_id': seeded['captain_membership_id']},
    ]


# --- Round-trip persistence ---

def test_round_trip_and_full_replacement(client, seeded):
    login(client, seeded['captain_id'])

    first = [
        {'leg_index': 1, 'membership_id': seeded['captain_membership_id']},
        {'leg_index': 2, 'membership_id': seeded['runner_membership_id']},
        {'leg_index': 3, 'membership_id': seeded['runner_membership_id']},
    ]
    response = _put(client, seeded, first)
    assert response.status_code == 200
    assert response.get_json()['assignments'] == first

    body = client.get(_url(seeded)).get_json()
    assert body['assignments'] == first

    # Full replacement: leg 3 unassigned, leg 2 reassigned, leg 5 added.
    second = [
        {'leg_index': 2, 'membership_id': seeded['captain_membership_id']},
        {'leg_index': 5, 'membership_id': seeded['runner_membership_id']},
    ]
    assert _put(client, seeded, second).status_code == 200
    body = client.get(_url(seeded)).get_json()
    assert body['assignments'] == second

    # Wrapped form is accepted too; empty list clears the board.
    response = client.put(_url(seeded), json={'assignments': []})
    assert response.status_code == 200
    body = client.get(_url(seeded)).get_json()
    assert body['assignments'] == []


def test_withdrawn_member_with_assignment_stays_visible(client, seeded):
    """A member who withdraws after being assigned still appears in the
    member list (flagged by status) so the UI can mark their legs."""
    login(client, seeded['captain_id'])
    assert _put(client, seeded, [
        {'leg_index': 1, 'membership_id': seeded['runner_membership_id']},
    ]).status_code == 200

    from app.models import db, TeamMembership, TeamMembershipStatus
    with client.application.app_context():
        membership = db.session.get(TeamMembership, seeded['runner_membership_id'])
        membership.status = TeamMembershipStatus.WITHDRAWN
        db.session.commit()

    body = client.get(_url(seeded)).get_json()
    assert body['assignments'] == [
        {'leg_index': 1, 'membership_id': seeded['runner_membership_id']},
    ]
    members = {m['membership_id']: m for m in body['members']}
    assert members[seeded['runner_membership_id']]['status'] == 'withdrawn'
