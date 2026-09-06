"""Rendering tests for the admin board's team table.

The board is shared by managers and admins, so what each of them can do to a
given team differs row by row. A row that offers nothing should not sprout an
empty "..." menu.
"""

import re

import pytest

from tests.conftest import login
from tests.test_assignments import seeded, _make_user  # noqa: F401  (fixture reuse)


def _row(body, team_name):
    """The <tr> of the team table whose name cell reads ``team_name``."""
    for row in re.findall(r'<tr>.*?</tr>', body, re.S):
        if f'<strong>{team_name}</strong>' in row:
            return row
    raise AssertionError(f'no row for team {team_name!r}')


def _set_status(app, team_id, status):
    from app.models import db, Team
    with app.app_context():
        team = Team.query.filter_by(id=team_id).first()
        team.status = status
        db.session.commit()


def _make_manager(app, user_id):
    from app.models import db, User, UserRole
    with app.app_context():
        user = User.query.filter_by(id=user_id).first()
        user.role = UserRole.MANAGER
        db.session.commit()


def _board(client, user_id):
    login(client, user_id)
    response = client.get('/admin/')
    assert response.status_code == 200
    return response.get_data(as_text=True)


@pytest.mark.parametrize('status_name', ['CANCELLED', 'WITHDRAWN'])
def test_manager_gets_no_menu_when_no_actions_apply(app, client, seeded, status_name):
    """A manager can neither approve nor withdraw a dead team, and can't delete."""
    from app.models import TeamStatus
    _set_status(app, seeded['team_id'], getattr(TeamStatus, status_name))
    _make_manager(app, seeded['runner_id'])

    row = _row(_board(client, seeded['runner_id']), 'Test Team')
    assert 'dropdown-toggle' not in row


def test_manager_keeps_the_menu_where_actions_remain(app, client, seeded):
    from app.models import TeamStatus
    _set_status(app, seeded['team_id'], TeamStatus.OPEN)
    _make_manager(app, seeded['runner_id'])

    row = _row(_board(client, seeded['runner_id']), 'Test Team')
    assert 'dropdown-toggle' in row
    assert 'Withdraw' in row


def test_admin_keeps_delete_on_a_cancelled_team(app, client, seeded):
    """Admins always have Delete -- and it shouldn't trail a stray divider."""
    from app.models import TeamStatus
    _set_status(app, seeded['team_id'], TeamStatus.CANCELLED)

    row = _row(_board(client, seeded['admin_id']), 'Test Team')
    assert 'dropdown-toggle' in row
    assert 'Delete' in row
    assert 'Withdraw' not in row
    assert 'dropdown-divider' not in row


def test_pending_team_offers_the_full_menu(app, client, seeded):
    from app.models import TeamStatus
    _set_status(app, seeded['team_id'], TeamStatus.PENDING)

    row = _row(_board(client, seeded['admin_id']), 'Test Team')
    for action in ('Approve', 'Cancel', 'Send Payment Reminder', 'Withdraw', 'Delete'):
        assert action in row, action
    assert 'dropdown-divider' in row


# --- The Line(s) column ---

def _set_lines(app, team_id, lines):
    from app.models import db, Team
    with app.app_context():
        team = Team.query.filter_by(id=team_id).first()
        team.lines = lines
        db.session.commit()


def test_interline_team_gets_one_pill_with_both_badges(app, client, seeded):
    from app.models import TeamLines
    _set_lines(app, seeded['team_id'], TeamLines.BOTH)

    row = _row(_board(client, seeded['admin_id']), 'Test Team')
    assert row.count('class="line-pill') == 1
    assert 'line-name-1' in row and 'line-name-2' in row
    assert 'title="Interline"' in row


def test_single_line_team_gets_one_badge(app, client, seeded):
    from app.models import TeamLines
    _set_lines(app, seeded['team_id'], TeamLines.TWO)

    row = _row(_board(client, seeded['admin_id']), 'Test Team')
    assert row.count('class="line-pill') == 1
    assert 'line-name-1' not in row and 'line-name-2' in row
    assert 'title="2 Line"' in row
