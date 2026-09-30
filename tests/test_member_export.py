"""Tests for the captain's member CSV/TSV export.

Names and comments are member-supplied, so the export must not hand the
captain a spreadsheet that runs formulas or a malformed download header.
"""

import csv
import io

import pytest

from tests.conftest import login
from tests.test_assignments import seeded, _make_user  # noqa: F401  (fixture reuse)


def _set_runner(app, seeded, name, comments):
    from app.models import db, TeamMembership
    with app.app_context():
        membership = db.session.get(TeamMembership, seeded['runner_membership_id'])
        membership.user.name = name
        membership.comments = comments
        db.session.commit()


@pytest.mark.parametrize('fmt, delimiter', [('csv', ','), ('tsv', '\t')])
def test_export_neutralizes_formulas(app, client, seeded, fmt, delimiter):
    _set_runner(app, seeded, '=HYPERLINK("http://evil.example","x")', '+1 then -2, @SUM(A1)')
    login(client, seeded['captain_id'])

    body = client.get(f"/team/{seeded['team_id']}/members/{fmt}").get_data(as_text=True)
    rows = list(csv.DictReader(io.StringIO(body), delimiter=delimiter))
    runner = next(r for r in rows if 'HYPERLINK' in r['name'])

    assert runner['name'] == '\'=HYPERLINK("http://evil.example","x")'
    assert runner['comments'] == "'+1 then -2, @SUM(A1)"
    # Ordinary values are left alone.
    assert any(r['name'] == 'Cap Tain' for r in rows)


def test_export_filename_is_sanitized(app, client, seeded):
    from app.models import db, Team
    with app.app_context():
        db.session.get(Team, seeded['team_id']).name = 'Bob\'s "Fast" Team'
        db.session.commit()
    login(client, seeded['captain_id'])

    disposition = client.get(f"/team/{seeded['team_id']}/members/csv").headers['Content-Disposition']
    assert disposition == 'attachment; filename="Bobs_Fast_Team_members.csv"'
