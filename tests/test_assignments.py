"""Endpoint tests for the leg-assignment API.

Covers permission denial (non-captain PUT, non-member GET), validation
failures (unknown/foreign/withdrawn memberships, duplicate legs, legs off the
team's registered line), and round-trip persistence of the full-replacement
PUT.
"""

import pytest

from tests.conftest import login


def _leg(position, team_lines=None):
    """The leg at ``position`` in running order, as a PUT payload fragment."""
    from app.models import TeamLines
    from app.services import course_service
    from app.utils import course_lines_for

    lines = course_lines_for(team_lines or TeamLines.ONE)
    leg = course_service.legs_for(lines)[position]
    return {'start_exchange': leg['start_exchange'], 'end_exchange': leg['end_exchange']}


def _off_course_leg():
    """A leg that exists on the 2 Line but not the 1 Line."""
    from app.services import course_service
    from app.utils import LINE_1, LINE_2

    one = {(l['start_exchange'], l['end_exchange']) for l in course_service.legs_for((LINE_1,))}
    for leg in course_service.legs_for((LINE_2,)):
        if (leg['start_exchange'], leg['end_exchange']) not in one:
            return {'start_exchange': leg['start_exchange'], 'end_exchange': leg['end_exchange']}
    raise AssertionError('the two lines should not be identical')


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
    # The seeded team runs the 1 Line: its own 13 legs plus the 13 shared
    # trunk legs it has in common with the 2 Line.
    assert len(course['legs']) == 26
    assert body['lines'] == '1 Line'
    first_leg = course['legs'][0]
    assert set(first_leg.keys()) == {
        'start', 'end', 'distance', 'ascent', 'descent', 'lines', 'sequence',
    }
    assert set(first_leg['start'].keys()) == {'id', 'name', 'station_code', 'line_code'}
    assert isinstance(first_leg['start']['name'], str)
    assert isinstance(first_leg['end']['name'], str)
    assert first_leg['lines'] == ['lrr_1line']
    # Federal Way Downtown is exchange 168: line "1", station code "68".
    assert (first_leg['start']['line_code'], first_leg['start']['station_code']) == ('1', '68')

    # A trunk exchange carries both lines in its code, which renders as two circles.
    trunk_leg = next(leg for leg in course['legs'] if len(leg['lines']) > 1)
    assert trunk_leg['start']['line_code'] == '12'
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
        {**_leg(1), 'membership_id': seeded['runner_membership_id']},
    ])
    assert response.status_code == 403


def test_outsider_put_denied(client, seeded):
    login(client, seeded['outsider_id'])
    assert _put(client, seeded, []).status_code == 403


def test_admin_can_put(client, seeded):
    login(client, seeded['admin_id'])
    response = _put(client, seeded, [
        {**_leg(3), 'membership_id': seeded['runner_membership_id']},
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
        {**_leg(1), 'membership_id': 'does-not-exist'},
    ])
    assert response.status_code == 400
    assert 'not part of this team' in response.get_json()['error']


def test_put_rejects_other_teams_membership(client, seeded):
    login(client, seeded['captain_id'])
    response = _put(client, seeded, [
        {**_leg(1), 'membership_id': seeded['foreign_membership_id']},
    ])
    assert response.status_code == 400
    assert 'not part of this team' in response.get_json()['error']


def test_put_rejects_withdrawn_membership(client, seeded):
    login(client, seeded['captain_id'])
    response = _put(client, seeded, [
        {**_leg(1), 'membership_id': seeded['withdrawn_membership_id']},
    ])
    assert response.status_code == 400
    assert 'active member' in response.get_json()['error']


def test_put_rejects_same_member_twice_on_one_leg(client, seeded):
    login(client, seeded['captain_id'])
    response = _put(client, seeded, [
        {**_leg(2), 'membership_id': seeded['runner_membership_id']},
        {**_leg(2), 'membership_id': seeded['runner_membership_id']},
    ])
    assert response.status_code == 400
    assert 'more than once' in response.get_json()['error']


def test_two_members_may_share_a_leg(client, seeded):
    login(client, seeded['captain_id'])
    shared = [
        {**_leg(2), 'membership_id': seeded['captain_membership_id']},
        {**_leg(2), 'membership_id': seeded['runner_membership_id']},
    ]
    response = _put(client, seeded, shared)
    assert response.status_code == 200

    body = client.get(_url(seeded)).get_json()
    assert sorted(body['assignments'], key=lambda a: a['membership_id']) == sorted(
        shared, key=lambda a: a['membership_id']
    )


@pytest.mark.parametrize('bad_exchange', [1.5, 'one', None, True])
def test_put_rejects_non_integer_exchange(client, seeded, bad_exchange):
    """bool is a subclass of int, so True must be rejected explicitly."""
    login(client, seeded['captain_id'])
    response = _put(client, seeded, [
        {'start_exchange': bad_exchange, 'end_exchange': 167,
         'membership_id': seeded['runner_membership_id']},
    ])
    assert response.status_code == 400
    assert 'must be an integer exchange id' in response.get_json()['error']


def test_put_accepts_first_leg(client, seeded):
    """The first leg of the course is assignable like any other."""
    login(client, seeded['captain_id'])
    response = _put(client, seeded, [
        {**_leg(0), 'membership_id': seeded['runner_membership_id']},
    ])
    assert response.status_code == 200
    assert response.get_json()['assignments'] == [
        {**_leg(0), 'membership_id': seeded['runner_membership_id']},
    ]


def test_put_rejects_leg_off_the_teams_line(client, seeded):
    """A 1 Line team cannot assign a leg that only exists on the 2 Line."""
    login(client, seeded['captain_id'])
    response = _put(client, seeded, [
        {**_off_course_leg(), 'membership_id': seeded['runner_membership_id']},
    ])
    assert response.status_code == 400
    assert 'not part of' in response.get_json()['error']


def test_put_rejects_missing_membership_id(client, seeded):
    login(client, seeded['captain_id'])
    response = _put(client, seeded, [_leg(1)])
    assert response.status_code == 400


def test_failed_put_preserves_existing_assignments(client, seeded):
    login(client, seeded['captain_id'])
    good = [{**_leg(1), 'membership_id': seeded['captain_membership_id']}]
    assert _put(client, seeded, good).status_code == 200

    bad = [
        {**_leg(1), 'membership_id': seeded['runner_membership_id']},
        {**_leg(2), 'membership_id': seeded['withdrawn_membership_id']},
    ]
    assert _put(client, seeded, bad).status_code == 400

    body = client.get(_url(seeded)).get_json()
    assert body['assignments'] == [
        {**_leg(1), 'membership_id': seeded['captain_membership_id']},
    ]


# --- Round-trip persistence ---

def test_round_trip_and_full_replacement(client, seeded):
    login(client, seeded['captain_id'])

    first = [
        {**_leg(1), 'membership_id': seeded['captain_membership_id']},
        {**_leg(2), 'membership_id': seeded['runner_membership_id']},
        {**_leg(3), 'membership_id': seeded['runner_membership_id']},
    ]
    response = _put(client, seeded, first)
    assert response.status_code == 200
    assert response.get_json()['assignments'] == first

    body = client.get(_url(seeded)).get_json()
    assert body['assignments'] == first

    # Full replacement: leg 3 unassigned, leg 2 reassigned, leg 5 added.
    second = [
        {**_leg(2), 'membership_id': seeded['captain_membership_id']},
        {**_leg(5), 'membership_id': seeded['runner_membership_id']},
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
        {**_leg(1), 'membership_id': seeded['runner_membership_id']},
    ]).status_code == 200

    from app.models import db, TeamMembership, TeamMembershipStatus
    with client.application.app_context():
        membership = db.session.get(TeamMembership, seeded['runner_membership_id'])
        membership.status = TeamMembershipStatus.WITHDRAWN
        db.session.commit()

    body = client.get(_url(seeded)).get_json()
    assert body['assignments'] == [
        {**_leg(1), 'membership_id': seeded['runner_membership_id']},
    ]
    members = {m['membership_id']: m for m in body['members']}
    assert members[seeded['runner_membership_id']]['status'] == 'withdrawn'


# --- Page: the view / edit split ---
#
# The Legs page defaults to a read-only schedule for everyone, captains
# included; the drag-and-drop board is behind an explicit edit mode that only
# a captain/site admin gets, reachable (and bookmarkable) at ?edit=1.

def _page(seeded, query=''):
    return f"/team/{seeded['team_id']}/legs{query}"


def test_board_includes_event_start_time(client, seeded):
    """The schedule needs a start to accumulate handoff times from."""
    login(client, seeded['runner_id'])
    course = client.get(_url(seeded)).get_json()['course']
    assert 'event_start_time' in course
    from app.config import Config
    assert course['event_start_time'] == Config.EVENT_START_TIME.isoformat()


def test_legs_page_defaults_to_the_schedule_for_a_member(client, seeded):
    login(client, seeded['runner_id'])
    response = client.get(_page(seeded))
    assert response.status_code == 200
    body = response.get_data(as_text=True)

    assert 'id="legs-schedule"' in body
    assert 'id="legs-my-assignment"' in body
    # A plain member never gets the board, in either mode.
    assert 'id="legs-edit"' not in body
    assert 'id="legs-bench"' not in body
    assert 'id="legs-save-btn"' not in body
    assert 'id="legs-mode-edit"' not in body


def test_legs_page_marks_the_viewers_own_membership(client, seeded):
    """So the schedule can lead with the viewer's own legs."""
    login(client, seeded['runner_id'])
    body = client.get(_page(seeded)).get_data(as_text=True)
    assert f'data-membership-id="{seeded["runner_membership_id"]}"' in body
    assert f'data-membership-id="{seeded["captain_membership_id"]}"' not in body


def test_legs_page_defaults_captain_to_view_mode_too(client, seeded):
    login(client, seeded['captain_id'])
    body = client.get(_page(seeded)).get_data(as_text=True)

    # Both renderings are present, but the board starts hidden and nothing
    # switches it on.
    assert 'id="legs-schedule"' in body
    assert '<div id="legs-edit" class="d-none">' in body
    assert 'id="legs-mode-edit"' in body
    assert "setMode('edit');" not in body


def test_legs_page_opens_the_board_on_edit_query(client, seeded):
    login(client, seeded['captain_id'])
    body = client.get(_page(seeded, '?edit=1')).get_data(as_text=True)
    assert "setMode('edit');" in body
    assert 'id="legs-bench"' in body


def test_admin_without_a_membership_gets_the_page_without_a_my_legs_card(client, seeded):
    """A site admin who doesn't run on the team has no legs to lead with."""
    login(client, seeded['admin_id'])
    body = client.get(_page(seeded, '?edit=1')).get_data(as_text=True)
    assert 'id="legs-my-assignment" data-membership-id=""' in body
    # ...but still gets the board, since they can manage the team.
    assert "setMode('edit');" in body


def test_edit_query_does_nothing_for_a_plain_member(client, seeded):
    login(client, seeded['runner_id'])
    body = client.get(_page(seeded, '?edit=1')).get_data(as_text=True)
    assert 'id="legs-edit"' not in body
    assert "setMode('edit');" not in body


# --- Course ordering: the Y ---
#
# The course converges: each line's branch runs from its own terminus to
# International District/Chinatown, and from there they share one trunk north.
# legs_for() returns them in the order the race is run -- branches first, then
# the trunk once -- so a Both Lines team's board and schedule show two starts
# feeding a single shared stretch.

def test_both_lines_orders_branches_before_the_shared_trunk():
    from app.services import course_service
    from app.utils import LINE_1, LINE_2

    legs = course_service.legs_for((LINE_1, LINE_2))
    shared = [i for i, leg in enumerate(legs) if len(leg['lines']) > 1]

    # The trunk is one unbroken run at the very end.
    assert shared == list(range(len(legs) - len(shared), len(legs)))
    assert len(shared) == 13
    # Both branches feed the trunk's first exchange.
    trunk_start = legs[shared[0]]['start_exchange']
    branch_ends = {leg['end_exchange'] for leg in legs[:shared[0]]
                   if leg['end_exchange'] not in {l['start_exchange'] for l in legs[:shared[0]]}}
    assert branch_ends == {trunk_start}


def test_both_lines_has_exactly_two_starts_and_one_finish():
    from app.services import course_service
    from app.utils import LINE_1, LINE_2

    legs = course_service.legs_for((LINE_1, LINE_2))
    starts = {leg['start_exchange'] for leg in legs}
    ends = {leg['end_exchange'] for leg in legs}
    # A terminus is an exchange no leg ends at: one per branch.
    assert len(starts - ends) == 2
    assert len(ends - starts) == 1


@pytest.mark.parametrize('line', ['LINE_1', 'LINE_2'])
def test_single_line_order_is_one_unbroken_chain(line):
    """Only a both-lines course splits; one line is still start to finish."""
    from app.services import course_service
    from app import utils

    legs = course_service.legs_for((getattr(utils, line),))
    for previous, leg in zip(legs, legs[1:]):
        assert previous['end_exchange'] == leg['start_exchange']


def test_end_station_options_follow_the_same_course_order():
    """The registration form's station list is ordered like the schedule, so a
    member picking a stop sees the branches and then the trunk, not one line
    interrupted by the other."""
    from app.services import course_service
    from app.utils import LINE_1, LINE_2, _exchange_ids_in_running_order

    lines = (LINE_1, LINE_2)
    legs = course_service.legs_for(lines)
    exchanges = _exchange_ids_in_running_order(lines)

    # Every exchange appears once, in first-touched order along the legs.
    assert len(exchanges) == len(set(exchanges))
    expected = []
    for leg in legs:
        for exchange_id in (leg['start_exchange'], leg['end_exchange']):
            if exchange_id not in expected:
                expected.append(exchange_id)
    assert exchanges == expected
    # The 2 Line's terminus comes after the 1 Line's branch, not interleaved.
    assert exchanges.index(265) > exchanges.index(1253)
