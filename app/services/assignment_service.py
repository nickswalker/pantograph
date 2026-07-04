"""Leg assignment domain service.

Owns the persistence and business rules for the leg-assignment board:
reading a team's board state (assignments + members with their join
preferences) and atomically replacing the team's assignment set. No HTTP
concerns: validation failures raise
:class:`~app.services.exceptions.ServiceError` (with an appropriate status),
which route handlers translate into JSON responses.
"""

from sqlalchemy.orm import joinedload

from app.models import db, LegAssignment, TeamMembership, TeamMembershipStatus
from app.services import course_service
from app.services.exceptions import ServiceError


def _valid_leg_indexes():
    """Leg indexes defined by the course data.

    Returns a set of valid leg indexes (0-based, per WP1's ``legs_2026.json``:
    22 legs, ids 0-21). Callers additionally validate that a leg index is a
    non-negative integer regardless of course data availability.
    """
    return {leg['index'] for leg in course_service.load_course()['legs']}


def _serialize_course(team):
    """Trim WP1's course model down to what the board (and WP4's metrics)
    need.

    Each leg's index, start/end exchange id *and* display name, distance,
    ascent, descent. Full geometry is omitted here — not needed to render the
    board (a future map overlay may want it, but that's out of scope).

    WP4 needs two more things to compute badges client-side, added here
    cheaply (see docs/plans/leg-assignments.md, "Status & integration
    notes"): the commute-distance matrix (for the end-exchange near/violated
    check when a member's last leg doesn't end exactly at their preferred
    station) and a station-name -> exchange-id index (covering the real
    exchange names, spelling aliases like "U-District", and non-course
    stations like "Boeing Access Road" that intentionally carry no
    commute/leg data, so a preference pointing at one resolves but naturally
    falls out of the commute lookup as "no data").

    ``estimated_duration_seconds`` (Team model) rides along inside ``course``
    rather than as a new top-level GET field, because WP3's
    ``onAssignmentsChanged`` hook forwards ``state.course`` verbatim but only
    explicitly re-picks ``course``/``members``/``assignments`` -- putting it
    here means WP4 gets it for free with no board changes.
    """
    course = course_service.load_course()
    exchanges = course_service.exchanges_by_id()

    def _endpoint(exchange_id):
        exchange = exchanges.get(exchange_id)
        return {'id': exchange_id, 'name': exchange['name'] if exchange else None}

    return {
        'event': course.get('event'),
        'units': course.get('units'),
        'legs': [
            {
                'index': leg['index'],
                'start': _endpoint(leg['start']),
                'end': _endpoint(leg['end']),
                'distance': leg['distance'],
                'ascent': leg['ascent'],
                'descent': leg['descent'],
            }
            for leg in course['legs']
        ],
        'commute': course.get('commute', []),
        'station_index': course_service.station_name_to_exchange_id(),
        'estimated_duration_seconds': team.estimated_duration_seconds,
    }


def _serialize_assignment(assignment):
    return {
        'leg_index': assignment.leg_index,
        'membership_id': assignment.membership_id,
    }


def _serialize_member(membership):
    return {
        'membership_id': membership.id,
        'user_id': membership.user_id,
        'name': membership.user.name,
        'avatar_url': membership.user.avatar_url,
        'status': membership.status.value,
        'willing_to_lead': membership.willing_to_lead,
        'preferred_miles': float(membership.preferred_miles) if membership.preferred_miles is not None else None,
        'planned_pace_seconds': membership.planned_pace_seconds,
        'preferred_station': membership.preferred_station,
    }


def get_board(team):
    """Return the board state for ``team``: assignments plus members.

    Members include every ACTIVE membership and any non-active membership
    that still holds an assignment (its non-active ``status`` lets the UI
    flag those legs, per the keyed-by-membership design).
    """
    assignments = (
        LegAssignment.query.filter_by(team_id=team.id)
        .order_by(LegAssignment.leg_index)
        .all()
    )
    memberships = (
        TeamMembership.query.options(joinedload(TeamMembership.user))
        .filter_by(team_id=team.id)
        .all()
    )

    assigned_membership_ids = {a.membership_id for a in assignments}
    visible = [
        m for m in memberships
        if m.status == TeamMembershipStatus.ACTIVE or m.id in assigned_membership_ids
    ]

    return {
        'team_id': team.id,
        'assignments': [_serialize_assignment(a) for a in assignments],
        'members': [_serialize_member(m) for m in visible],
        'course': _serialize_course(team),
    }


def replace_assignments(team, assignments_payload):
    """Atomically replace ``team``'s entire assignment set.

    ``assignments_payload`` is a list of ``{'leg_index': int,
    'membership_id': str}`` dicts. Validates that each leg index is a
    non-negative integer that exists in the course (0-21, per
    ``data/legs_2026.json``), that no (leg, member) pair appears twice —
    several members may share a leg — and that each membership belongs to
    this team and is ACTIVE. On any validation failure the existing
    assignments are left untouched.

    Returns the saved assignments in serialized form, ordered by leg index.
    """
    if not isinstance(assignments_payload, list):
        raise ServiceError('Assignments must be a list')

    memberships_by_id = {
        m.id: m for m in TeamMembership.query.filter_by(team_id=team.id).all()
    }
    valid_leg_indexes = _valid_leg_indexes()

    seen_pairs = set()
    validated = []
    for item in assignments_payload:
        if not isinstance(item, dict):
            raise ServiceError('Each assignment must be an object with leg_index and membership_id')

        leg_index = item.get('leg_index')
        # bool is a subclass of int; reject it explicitly. Leg indexes are
        # 0-based (see data/legs_2026.json), so 0 is a valid leg index.
        if isinstance(leg_index, bool) or not isinstance(leg_index, int) or leg_index < 0:
            raise ServiceError('leg_index must be a non-negative integer')
        if valid_leg_indexes is not None and leg_index not in valid_leg_indexes:
            raise ServiceError(f'Leg {leg_index} does not exist in the course')

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
        pair = (leg_index, membership_id)
        if pair in seen_pairs:
            raise ServiceError(f'{membership.user.name} is assigned to leg {leg_index} more than once')
        seen_pairs.add(pair)

        validated.append(pair)

    # Full replacement in a single transaction: the delete and the inserts
    # commit together or not at all.
    try:
        LegAssignment.query.filter_by(team_id=team.id).delete()
        for leg_index, membership_id in validated:
            db.session.add(LegAssignment(
                team_id=team.id,
                membership_id=membership_id,
                leg_index=leg_index,
            ))
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return [
        {'leg_index': leg_index, 'membership_id': membership_id}
        for leg_index, membership_id in sorted(validated)
    ]