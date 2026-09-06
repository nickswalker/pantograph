"""Rendering tests for the team members page header."""

import re

import pytest

from tests.conftest import login
from tests.test_assignments import seeded, _make_user  # noqa: F401  (fixture reuse)


def _header(app, client, seeded, lines):
    """The members page header for a team running ``lines``."""
    from app.models import db, Team
    with app.app_context():
        team = Team.query.filter_by(id=seeded['team_id']).first()
        team.lines = lines
        db.session.commit()

    login(client, seeded['captain_id'])
    response = client.get(f"/team/{seeded['team_id']}/members")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    # From the team name down to the roster card, wherever the pills sit.
    return re.search(r'<h1.*?<div class="row">', body, re.S).group(0)


def test_interline_team_gets_one_labelled_pill(app, client, seeded):
    from app.models import TeamLines
    header = _header(app, client, seeded, TeamLines.BOTH)

    assert header.count('class="line-pill"') == 1
    assert 'line-name-1' in header and 'line-name-2' in header
    assert 'Interline' in header


@pytest.mark.parametrize('line_name,badge', [('ONE', '1'), ('TWO', '2')])
def test_single_line_team_keeps_its_line_pill(app, client, seeded, line_name, badge):
    from app.models import TeamLines
    header = _header(app, client, seeded, getattr(TeamLines, line_name))

    assert header.count('class="line-pill"') == 1
    assert f'line-name-{badge}' in header
    assert 'Interline' not in header
    assert '</span>Line</span>' in header


# --- Willingness to run alone ---

def _roster(app, client, seeded, willing):
    """The members page body, with the runner's willingness set to ``willing``."""
    from app.models import db, TeamMembership
    with app.app_context():
        membership = TeamMembership.query.filter_by(id=seeded['runner_membership_id']).first()
        membership.willing_to_lead = willing
        db.session.commit()

    login(client, seeded['captain_id'])
    response = client.get(f"/team/{seeded['team_id']}/members")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def _member_cell(body, name):
    """The <tr> for ``name`` on the roster (withdrawn rows grey the name)."""
    for row in re.findall(r'<tr[^>]*>.*?</tr>', body, re.S):
        if re.search(rf'<strong[^>]*>{name}</strong>', row):
            return row
    raise AssertionError(f'no roster row for {name!r}')


def test_leg_leader_badge_is_gone(app, client, seeded):
    assert 'Leg Leader' not in _roster(app, client, seeded, True)


def test_willingness_shows_a_check_when_yes(app, client, seeded):
    row = _member_cell(_roster(app, client, seeded, True), 'Run Ner')
    assert 'Willing to run alone:' in row
    assert 'checkmark-circle-outline' in row
    assert 'close-circle-outline' not in row


def test_willingness_shows_an_x_when_no(app, client, seeded):
    """The old badge could only say "no" by being absent."""
    row = _member_cell(_roster(app, client, seeded, False), 'Run Ner')
    assert 'Willing to run alone:' in row
    assert 'close-circle-outline' in row
    assert 'checkmark-circle-outline' not in row


# --- Withdrawn and removed rows ---

def test_withdrawn_member_shows_their_pace(app, client, seeded):
    """The cell used to read membership.planned_pace, which does not exist --
    Jinja quietly resolved it to undefined and the line never rendered."""
    from app.models import db, TeamMembership
    with app.app_context():
        membership = TeamMembership.query.filter_by(id=seeded['withdrawn_membership_id']).first()
        membership.planned_pace_seconds = 555
        db.session.commit()

    login(client, seeded['captain_id'])
    body = client.get(f"/team/{seeded['team_id']}/members").get_data(as_text=True)
    row = _member_cell(body, 'Quit Ter')
    assert '9:15/mi' in row


def test_captain_badge_is_grey(app, client, seeded):
    login(client, seeded['captain_id'])
    body = client.get(f"/team/{seeded['team_id']}/members").get_data(as_text=True)
    row = _member_cell(body, 'Cap Tain')
    assert 'bg-secondary ms-2 rounded-pill">Captain' in row
    assert 'bg-success' not in row


# --- Roster group headers ---

def test_roster_has_no_active_header(app, client, seeded):
    """Only departures from the roster get a divider."""
    login(client, seeded['captain_id'])
    body = client.get(f"/team/{seeded['team_id']}/members").get_data(as_text=True)
    assert 'Active (' not in body
    assert 'Withdrawn (1)' in body   # the seeded team has one


def test_roster_count_is_active_members(app, client, seeded):
    """The count the dropped "Active" header carried: two active, one withdrawn."""
    login(client, seeded['captain_id'])
    body = client.get(f"/team/{seeded['team_id']}/members").get_data(as_text=True)
    assert 'Roster (2)' in body
