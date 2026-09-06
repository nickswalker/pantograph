"""Tests for the audit log.

Two properties matter beyond "a row gets written": the row names the *actor*
(an audit log that can't tell you who did it is just a timestamp), and it
survives the deletion of what it describes (a hard delete is the event you
most need a record of).
"""

import pytest

from tests.conftest import login
from tests.test_assignments import seeded, _make_user  # noqa: F401  (fixture reuse)


def _events(app, verb=None):
    from app.models import AuditEvent
    with app.app_context():
        query = AuditEvent.query
        if verb:
            query = query.filter_by(verb=verb)
        return query.order_by(AuditEvent.occurred_at.asc()).all()


def _set_status(app, team_id, status):
    from app.models import db, Team
    with app.app_context():
        team = Team.query.filter_by(id=team_id).first()
        team.status = status
        db.session.commit()


# --- Team status transitions ---

def test_approval_records_actor_and_transition(app, client, seeded):
    from app.models import AuditVerb, TeamStatus, AuditTargetType
    _set_status(app, seeded['team_id'], TeamStatus.PENDING)

    login(client, seeded['admin_id'])
    assert client.post(f"/admin/team/{seeded['team_id']}/approve").status_code == 200

    events = _events(app, AuditVerb.TEAM_APPROVED)
    assert len(events) == 1
    event = events[0]
    assert event.actor_id == seeded['admin_id']
    assert event.actor_label == 'Ad Min'
    assert event.target_type == AuditTargetType.TEAM
    assert event.target_id == seeded['team_id']
    assert event.details == {'from': 'pending', 'to': 'open'}


def test_approve_and_reopen_are_distinguishable(app, client, seeded):
    """Both land on OPEN; a column diff couldn't tell them apart."""
    from app.models import AuditVerb, TeamStatus
    _set_status(app, seeded['team_id'], TeamStatus.PENDING)

    login(client, seeded['admin_id'])
    client.post(f"/admin/team/{seeded['team_id']}/approve")
    client.post(f"/team/{seeded['team_id']}/close")
    client.post(f"/team/{seeded['team_id']}/reopen")

    assert [e.verb for e in _events(app)] == [
        AuditVerb.TEAM_APPROVED, AuditVerb.TEAM_CLOSED, AuditVerb.TEAM_REOPENED,
    ]


def test_captain_transition_records_the_captain_as_actor(app, client, seeded):
    from app.models import AuditVerb
    login(client, seeded['captain_id'])
    assert client.post(f"/team/{seeded['team_id']}/withdraw").status_code == 200

    event = _events(app, AuditVerb.TEAM_WITHDRAWN)[0]
    assert event.actor_id == seeded['captain_id']
    assert event.details == {'from': 'open', 'to': 'withdrawn'}


def test_rejected_transition_records_nothing(app, client, seeded):
    """A team that is already open can't be reopened -- and nothing is logged."""
    login(client, seeded['captain_id'])
    assert client.post(f"/team/{seeded['team_id']}/reopen").status_code == 400
    assert _events(app) == []


# --- Deletion: the record must outlive its subject ---

def test_team_deletion_is_logged_and_survives_the_team(app, client, seeded):
    from app.models import AuditVerb, Team
    login(client, seeded['admin_id'])
    assert client.delete(f"/admin/team/{seeded['team_id']}").status_code == 200

    with app.app_context():
        assert Team.query.filter_by(id=seeded['team_id']).first() is None

    event = _events(app, AuditVerb.TEAM_DELETED)[0]
    assert event.target_id == seeded['team_id']
    assert event.target_label == 'Test Team'   # snapshot outlives the row
    assert event.actor_id == seeded['admin_id']
    assert event.details['status'] == 'open'


# --- Role changes ---

def test_role_grant_and_revoke_are_logged(app, client, seeded):
    from app.models import AuditVerb
    login(client, seeded['admin_id'])
    url = f"/admin/user/{seeded['runner_id']}/role"

    assert client.patch(url, json={'role': 'manager'}).status_code == 200
    assert client.patch(url, json={'role': 'participant'}).status_code == 200

    granted = _events(app, AuditVerb.ROLE_GRANTED)[0]
    revoked = _events(app, AuditVerb.ROLE_REVOKED)[0]
    assert granted.target_id == seeded['runner_id']
    assert granted.details == {'from': 'participant', 'to': 'manager'}
    assert revoked.details == {'from': 'manager', 'to': 'participant'}
    assert revoked.actor_id == seeded['admin_id']


def test_refused_role_change_records_nothing(app, client, seeded):
    """A no-op role change returns 200 but is not an event."""
    login(client, seeded['admin_id'])
    assert client.patch(f"/admin/user/{seeded['runner_id']}/role",
                        json={'role': 'participant'}).status_code == 200
    assert _events(app) == []


# --- Membership changes ---

def test_member_removal_is_logged_against_the_membership(app, client, seeded):
    from app.models import AuditVerb, AuditTargetType
    login(client, seeded['captain_id'])
    response = client.post(f"/team/{seeded['team_id']}/members/{seeded['runner_id']}/remove")
    assert response.status_code == 200

    event = _events(app, AuditVerb.MEMBER_REMOVED)[0]
    assert event.target_type == AuditTargetType.MEMBERSHIP
    assert event.target_label == 'Run Ner'
    assert event.actor_id == seeded['captain_id']
    assert event.details['team_name'] == 'Test Team'


def test_captain_transfer_is_logged(app, client, seeded):
    from app.models import AuditVerb
    login(client, seeded['captain_id'])
    response = client.post(f"/team/{seeded['team_id']}/members/{seeded['runner_id']}/promote")
    assert response.status_code == 200

    event = _events(app, AuditVerb.CAPTAIN_TRANSFERRED)[0]
    assert event.details == {'from': 'Cap Tain', 'to': 'Run Ner'}


# --- What the admin board reads ---

def test_latest_status_change_wins(app, client, seeded):
    from app.services import audit_service
    login(client, seeded['captain_id'])
    client.post(f"/team/{seeded['team_id']}/close")
    client.post(f"/team/{seeded['team_id']}/reopen")

    with app.app_context():
        from app.models import AuditVerb
        latest = audit_service.latest_team_status_changes()
        assert latest[seeded['team_id']].verb == AuditVerb.TEAM_REOPENED


def test_admin_page_shows_who_changed_the_status(app, client, seeded):
    login(client, seeded['captain_id'])
    client.post(f"/team/{seeded['team_id']}/close")

    login(client, seeded['admin_id'])
    body = client.get('/admin/').get_data(as_text=True)
    assert 'Closed by Cap Tain' in body


def test_pending_team_falls_back_to_its_creation_date(app, client, seeded):
    from app.models import TeamStatus
    _set_status(app, seeded['team_id'], TeamStatus.PENDING)

    login(client, seeded['admin_id'])
    body = client.get('/admin/').get_data(as_text=True)
    assert 'Registered ·' in body


# --- The activity card ---

def test_activity_card_lists_recent_events(app, client, seeded):
    login(client, seeded['captain_id'])
    client.post(f"/team/{seeded['team_id']}/close")

    login(client, seeded['admin_id'])
    body = client.get('/admin/').get_data(as_text=True)
    assert 'Closed team Test Team to new members' in body
    assert 'Cap Tain' in body


def test_activity_card_is_hidden_from_managers(app, client, seeded):
    """Managers share the admin board, but the audit log is admin-only."""
    from app.models import db, User, UserRole
    with app.app_context():
        user = User.query.filter_by(id=seeded['runner_id']).first()
        user.role = UserRole.MANAGER
        db.session.commit()

    login(client, seeded['captain_id'])
    client.post(f"/team/{seeded['team_id']}/close")

    login(client, seeded['runner_id'])
    response = client.get('/admin/')
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert 'Closed team Test Team' not in body
    assert 'No activity recorded yet.' not in body


def test_activity_card_shows_an_empty_state(app, client, seeded):
    login(client, seeded['admin_id'])
    body = client.get('/admin/').get_data(as_text=True)
    assert 'No activity recorded yet.' in body
