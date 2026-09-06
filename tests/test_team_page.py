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

