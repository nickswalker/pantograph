"""Team domain service.

Owns the team status state machine. These functions contain no HTTP concerns:
they validate a requested transition against the team's current status, apply
it, and commit. Invalid transitions raise :class:`TeamStateError` carrying a
user-facing message; route handlers translate that into a 400 response.

The valid status transitions are:

    PENDING  --approve-->  OPEN (Team) / CLOSED (Solo)
    PENDING  --cancel-->   CANCELLED
    OPEN     --close-->    CLOSED
    CLOSED   --reopen-->   OPEN
    OPEN     --withdraw-->  WITHDRAWN
    CLOSED   --withdraw-->  WITHDRAWN
    WITHDRAWN --unwithdraw--> OPEN
"""

from app.models import db, TeamStatus, TeamFormat
from app.services.exceptions import ServiceError


class TeamStateError(ServiceError):
    """Raised when a requested team status transition is not permitted.

    The message is safe to surface to the end user; these are always 400s.
    """


def withdraw_team(team):
    """Withdraw an open or closed team."""
    if team.status == TeamStatus.WITHDRAWN:
        raise TeamStateError('Team is already withdrawn')
    if team.status not in (TeamStatus.OPEN, TeamStatus.CLOSED):
        raise TeamStateError(f"{team.status.value} teams can't be withdrawn")

    team.status = TeamStatus.WITHDRAWN
    db.session.commit()
    return team


def unwithdraw_team(team):
    """Restore a withdrawn team to open."""
    if team.status != TeamStatus.WITHDRAWN:
        raise TeamStateError('Team is not withdrawn')

    team.status = TeamStatus.OPEN
    db.session.commit()
    return team


def cancel_team(team):
    """Cancel a pending team."""
    if team.status != TeamStatus.PENDING:
        raise TeamStateError('Only pending teams can be cancelled')

    team.status = TeamStatus.CANCELLED
    db.session.commit()
    return team


def close_team(team):
    """Close an open team to new registrations."""
    if team.status != TeamStatus.OPEN:
        raise TeamStateError('Only open teams can be closed')

    team.status = TeamStatus.CLOSED
    db.session.commit()
    return team


def reopen_team(team):
    """Reopen a closed team for new registrations."""
    if team.status != TeamStatus.CLOSED:
        raise TeamStateError('Team is not closed')

    team.status = TeamStatus.OPEN
    db.session.commit()
    return team


def approve_team(team):
    """Approve a pending team.

    Solo entries move straight to CLOSED (they accept no other members); Team
    entries move to OPEN so others can join.
    """
    if team.status != TeamStatus.PENDING:
        raise TeamStateError('Only pending teams can be approved')

    team.status = TeamStatus.CLOSED if team.format == TeamFormat.SOLO else TeamStatus.OPEN
    db.session.commit()
    return team
