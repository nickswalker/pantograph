"""Team domain service.

Owns the team status state machine. These functions contain no HTTP concerns:
they validate a requested transition against the team's current status, apply
it, and commit. Invalid transitions raise :class:`TeamStateError` carrying a
user-facing message; route handlers translate that into a 400 response.

Every transition is recorded to the audit log in the same transaction, so
the admin board can say who last changed a team's status and when. Callers
pass the acting user as ``actor``; omitting it records the change as having
been made by the system.

The valid status transitions are:

    PENDING  --approve-->  OPEN (Team) / CLOSED (Solo)
    PENDING  --cancel-->   CANCELLED
    OPEN     --close-->    CLOSED
    CLOSED   --reopen-->   OPEN
    OPEN     --withdraw-->  WITHDRAWN
    CLOSED   --withdraw-->  WITHDRAWN
    WITHDRAWN --unwithdraw--> OPEN
"""

from app.models import db, AuditVerb, TeamStatus, TeamFormat
from app.services import audit_service
from app.services.exceptions import ServiceError


class TeamStateError(ServiceError):
    """Raised when a requested team status transition is not permitted.

    The message is safe to surface to the end user; these are always 400s.
    """


def _transition(team, new_status, verb, actor):
    """Apply a validated transition, log it, and commit -- all as one unit."""
    previous_status = team.status
    team.status = new_status
    audit_service.record(verb, target=team, actor=actor,
                         **{'from': previous_status.value, 'to': new_status.value})
    db.session.commit()
    return team


def withdraw_team(team, actor=None):
    """Withdraw an open or closed team."""
    if team.status == TeamStatus.WITHDRAWN:
        raise TeamStateError('Team is already withdrawn')
    if team.status not in (TeamStatus.OPEN, TeamStatus.CLOSED):
        raise TeamStateError(f"{team.status.value} teams can't be withdrawn")

    return _transition(team, TeamStatus.WITHDRAWN, AuditVerb.TEAM_WITHDRAWN, actor)


def unwithdraw_team(team, actor=None):
    """Restore a withdrawn team to open."""
    if team.status != TeamStatus.WITHDRAWN:
        raise TeamStateError('Team is not withdrawn')

    return _transition(team, TeamStatus.OPEN, AuditVerb.TEAM_UNWITHDRAWN, actor)


def cancel_team(team, actor=None):
    """Cancel a pending team."""
    if team.status != TeamStatus.PENDING:
        raise TeamStateError('Only pending teams can be cancelled')

    return _transition(team, TeamStatus.CANCELLED, AuditVerb.TEAM_CANCELLED, actor)


def close_team(team, actor=None):
    """Close an open team to new registrations."""
    if team.status != TeamStatus.OPEN:
        raise TeamStateError('Only open teams can be closed')

    return _transition(team, TeamStatus.CLOSED, AuditVerb.TEAM_CLOSED, actor)


def reopen_team(team, actor=None):
    """Reopen a closed team for new registrations."""
    if team.status != TeamStatus.CLOSED:
        raise TeamStateError('Team is not closed')

    return _transition(team, TeamStatus.OPEN, AuditVerb.TEAM_REOPENED, actor)


def approve_team(team, actor=None):
    """Approve a pending team.

    Solo entries move straight to CLOSED (they accept no other members); Team
    entries move to OPEN so others can join.
    """
    if team.status != TeamStatus.PENDING:
        raise TeamStateError('Only pending teams can be approved')

    new_status = TeamStatus.CLOSED if team.format == TeamFormat.SOLO else TeamStatus.OPEN
    return _transition(team, new_status, AuditVerb.TEAM_APPROVED, actor)
