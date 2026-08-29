"""Leg assignment domain service.

Owns the persistence and business rules for the leg-assignment board:
reading a team's board state (assignments + members with their join
preferences) and atomically replacing the team's assignment set. No HTTP
concerns: validation failures raise
:class:`~app.services.exceptions.ServiceError` (with an appropriate status),
which route handlers translate into JSON responses.

Legs are identified by their ``(start_exchange, end_exchange)`` pair and the
set of assignable legs is scoped to the team's registered line(s).
"""

from sqlalchemy.orm import joinedload

from app.config import Config
from app.models import db, LegAssignment, TeamMembership, TeamMembershipStatus
from app.services import course_service, preference_service
from app.services.exceptions import ServiceError
from app.utils import course_lines_for


def _course_leg_order(team):
    """Leg key -> position in the team's course, for ordering assignments.

    Exchange ids do not ascend in running order (the 1 Line branch counts
    down 168..154, then the trunk runs 1253..1240), so assignments have to be
    ordered against the course rather than sorted by id.
    """
    return {
        course_service.leg_key(leg['start_exchange'], leg['end_exchange']): position
        for position, leg in enumerate(course_service.legs_for(course_lines_for(team.lines)))
    }


def _assignable_leg_keys(team):
    """Leg keys the team may assign, per its registered line(s)."""
    return set(_course_leg_order(team))


def _serialize_assignment(assignment):
    return {
        'start_exchange': assignment.start_exchange,
        'end_exchange': assignment.end_exchange,
        'membership_id': assignment.membership_id,
    }


def _serialize_member(membership, include_private=False):
    """One member entry for the board.

    For a captain/admin the preference fields carry the **effective** values
    -- what the member stated, with any captain override applied on top (see
    :mod:`app.services.preference_service`) -- plus a ``stated``/``overrides``
    sidecar so the UI can show what was adjusted. Resolving it here is what
    makes an override influence the satisfaction badges and the solver
    without either of them knowing overrides exist.

    Everyone else gets the member's own stated preferences and no sidecar at
    all. Overrides are the captain's planning aid: adjusted numbers shown to
    the rest of the team would either need explaining or would quietly
    misreport what people asked for, so their view is the pre-override one.
    """
    member = {
        'membership_id': membership.id,
        'user_id': membership.user_id,
        'name': membership.user.name,
        'avatar_url': membership.user.avatar_url,
        'status': membership.status.value,
    }
    if include_private:
        member.update(preference_service.effective_preferences(membership))
        member.update(preference_service.serialize(membership, include_note=True))
    else:
        member.update(preference_service.stated_preferences(membership))
    return member


def serialize_member(membership, include_private=False):
    """Public alias of :func:`_serialize_member`, for callers that need to
    hand a single refreshed member back to the board (e.g. after a captain
    edits that member's preference overrides)."""
    return _serialize_member(membership, include_private=include_private)


def get_board(team, include_private=False):
    """Return the board state for ``team``: assignments plus members.

    Members include every ACTIVE membership and any non-active membership
    that still holds an assignment (its non-active ``status`` lets the UI
    flag those legs, per the keyed-by-membership design).

    ``include_private`` switches the member entries between the captain's
    view (preferences with any captain override applied, plus what was
    adjusted and why) and everyone else's (the members' own stated
    preferences, with no sign that overrides exist) -- see
    :func:`_serialize_member`.
    """
    order = _course_leg_order(team)
    assignments = sorted(
        LegAssignment.query.filter_by(team_id=team.id).all(),
        key=lambda a: (order.get(a.leg_key, len(order)), a.membership_id),
    )
    memberships = (
        TeamMembership.query.options(
            joinedload(TeamMembership.user),
            joinedload(TeamMembership.preference_override),
        )
        .filter_by(team_id=team.id)
        .all()
    )

    assigned_membership_ids = {a.membership_id for a in assignments}
    visible = [
        m for m in memberships
        if m.status == TeamMembershipStatus.ACTIVE or m.id in assigned_membership_ids
    ]

    course = course_service.course_for(team.lines)
    course['estimated_duration_seconds'] = team.estimated_duration_seconds
    # Every team starts together (there are no waves -- Team has no start
    # column), so the event start is all the schedule view needs to turn
    # per-leg durations into wall-clock handoff times.
    course['event_start_time'] = Config.EVENT_START_TIME.isoformat()

    return {
        'team_id': team.id,
        'lines': team.lines.value,
        'assignments': [_serialize_assignment(a) for a in assignments],
        'members': [_serialize_member(m, include_private=include_private) for m in visible],
        'course': course,
    }


def replace_assignments(team, assignments_payload):
    """Atomically replace ``team``'s entire assignment set.

    ``assignments_payload`` is a list of ``{'start_exchange': int,
    'end_exchange': int, 'membership_id': str}`` dicts. Validates that each
    leg is on the team's registered line(s), that no (leg, member) pair
    appears twice -- several members may share a leg -- and that each
    membership belongs to this team and is ACTIVE. On any validation failure
    the existing assignments are left untouched.

    Returns the saved assignments in serialized form.
    """
    if not isinstance(assignments_payload, list):
        raise ServiceError('Assignments must be a list')

    memberships_by_id = {
        m.id: m for m in TeamMembership.query.filter_by(team_id=team.id).all()
    }
    assignable = _assignable_leg_keys(team)

    seen_pairs = set()
    validated = []
    for item in assignments_payload:
        if not isinstance(item, dict):
            raise ServiceError('Each assignment must be an object with a leg and membership_id')

        start = item.get('start_exchange')
        end = item.get('end_exchange')
        # bool is a subclass of int; reject it explicitly.
        for value, field in ((start, 'start_exchange'), (end, 'end_exchange')):
            if isinstance(value, bool) or not isinstance(value, int):
                raise ServiceError(f'{field} must be an integer exchange id')

        if (start, end) not in assignable:
            raise ServiceError(
                f'Leg {start}->{end} is not part of this team\'s {team.lines.value} course'
            )

        membership_id = item.get('membership_id')
        if not membership_id or not isinstance(membership_id, str):
            raise ServiceError('membership_id is required for each assignment')
        membership = memberships_by_id.get(membership_id)
        if membership is None:
            raise ServiceError(f'Membership {membership_id} is not part of this team')
        if membership.status != TeamMembershipStatus.ACTIVE:
            raise ServiceError(f'{membership.user.name} is no longer an active member of this team')

        # Several members may share a leg, but the same member may appear on
        # a given leg only once.
        pair = (start, end, membership_id)
        if pair in seen_pairs:
            raise ServiceError(
                f'{membership.user.name} is assigned to leg {start}->{end} more than once'
            )
        seen_pairs.add(pair)

        validated.append(pair)

    # Full replacement in a single transaction: the delete and the inserts
    # commit together or not at all.
    try:
        LegAssignment.query.filter_by(team_id=team.id).delete()
        for start, end, membership_id in validated:
            db.session.add(LegAssignment(
                team_id=team.id,
                membership_id=membership_id,
                start_exchange=start,
                end_exchange=end,
            ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    order = _course_leg_order(team)
    validated.sort(key=lambda item: (order.get((item[0], item[1]), len(order)), item[2]))
    return [
        {'start_exchange': start, 'end_exchange': end, 'membership_id': membership_id}
        for start, end, membership_id in validated
    ]
