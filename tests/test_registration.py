"""Regression coverage for rejoining a team after withdrawing/being removed.

unique_membership is on (user_id, team_id); withdraw/remove only flip status
rather than delete the row. membership_service.register() must reactivate
that stale row instead of inserting a duplicate that would violate the
constraint -- and /my-registration must not hand back a stale row as if it
were the user's current one.
"""

import pytest

from tests.conftest import login
from tests.test_assignments import seeded, _make_user  # noqa: F401  (fixture reuse)


def _rejoin_input(team_id, **overrides):
    from app.services.membership_service import RegistrationInput

    fields = dict(
        team_id=team_id, invite_token=None, willing_to_lead=True,
        preferred_miles='5.0', planned_pace_str='9:00',
        preferred_station=None, comments='back for more',
        waiver_agreed=True, team_password='', email_opt_in=False,
    )
    fields.update(overrides)
    return RegistrationInput(**fields)


def test_rejoining_after_withdrawal_reactivates_the_same_row(app, seeded):
    """A withdrawn member who registers for the same team again gets their
    old row reactivated, not a duplicate-insert IntegrityError."""
    from app.models import db, User, TeamMembership, TeamMembershipStatus
    from app.services.membership_service import register

    with app.app_context():
        user = db.session.get(User, seeded['quitter_id'])
        data = _rejoin_input(seeded['team_id'])

        result = register(user, data, mode='join')

        assert result.team.id == seeded['team_id']
        rows = TeamMembership.query.filter_by(
            user_id=seeded['quitter_id'], team_id=seeded['team_id']
        ).all()
        # Still exactly one row for this (user, team) pair -- reactivated,
        # not duplicated -- and it's the same row that was withdrawn.
        assert len(rows) == 1
        assert rows[0].id == seeded['withdrawn_membership_id']
        assert rows[0].status == TeamMembershipStatus.ACTIVE
        assert rows[0].comments == 'back for more'


def test_my_registration_ignores_a_stale_membership(client, app, seeded):
    """/my-registration must not surface a withdrawn row as the user's
    current membership -- it should route them to join fresh instead."""
    login(client, seeded['quitter_id'])
    response = client.get('/my-registration')
    # No active membership or captained team -> redirected to join a team,
    # not shown an edit form for the team they withdrew from.
    assert response.status_code == 302
    assert response.headers['Location'].endswith('/join-team') or 'join-team' in response.headers['Location']
