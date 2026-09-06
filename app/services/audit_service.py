"""Audit log service.

One append-only table (:class:`~app.models.AuditEvent`) records administrative
actions across the app. Two rules keep it trustworthy:

1. **Write at the choke point.** Every status transition already funnels
   through :mod:`app.services.team_service`, every membership change through
   :mod:`app.services.membership_service`; that is where the record belongs.
   Not in an ORM ``before_flush`` hook -- those see column diffs, which cannot
   tell an approval from a reopen, and would log housekeeping writes like
   ``last_login`` as if they were somebody's decision.

2. **Never commit here.** :func:`record` only stages the row, so it lands in
   the same transaction as the change it describes. If the caller rolls back,
   the claim that the thing happened rolls back with it.
"""

import json

from app.models import (
    db, AuditEvent, AuditTargetType, AuditVerb, TEAM_STATUS_VERBS,
    Team, TeamMembership, User,
)


def _describe(target):
    """``(target_type, target_id, target_label)`` for an auditable row.

    The label is a snapshot: audit rows outlive the teams and users they
    describe, so how the row read at the time is all we will have later.
    """
    if target is None:
        return AuditTargetType.SYSTEM, None, None
    if isinstance(target, Team):
        return AuditTargetType.TEAM, target.id, target.name
    if isinstance(target, User):
        return AuditTargetType.USER, target.id, target.name
    if isinstance(target, TeamMembership):
        member = target.user
        return AuditTargetType.MEMBERSHIP, target.id, member.name if member else None
    raise TypeError(f'Not an auditable target: {type(target).__name__}')


def record(verb, target=None, actor=None, **payload):
    """Stage an audit row for ``verb``. The caller's commit makes it durable.

    ``target`` is the Team, User or TeamMembership acted on, or ``None`` for an
    event-wide action that names no single row. ``actor`` is the user who did
    it; ``None`` means the system did (a scheduled job), which is why it is not
    an error to omit it. Remaining keyword arguments are stored as the JSON
    payload -- by convention ``from``/``to`` for a transition.
    """
    target_type, target_id, target_label = _describe(target)

    event = AuditEvent(
        verb=verb,
        actor_id=getattr(actor, 'id', None),
        actor_label=getattr(actor, 'name', None),
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
        payload=json.dumps(payload) if payload else None,
    )
    db.session.add(event)
    return event


#: How each status verb reads in the admin board's hover text.
STATUS_VERB_LABELS = {
    AuditVerb.TEAM_APPROVED: 'Approved',
    AuditVerb.TEAM_CANCELLED: 'Cancelled',
    AuditVerb.TEAM_CLOSED: 'Closed',
    AuditVerb.TEAM_REOPENED: 'Reopened',
    AuditVerb.TEAM_WITHDRAWN: 'Withdrawn',
    AuditVerb.TEAM_UNWITHDRAWN: 'Un-withdrawn',
}


def latest_team_status_changes():
    """The most recent status change per team, keyed by team id.

    One query feeding the whole admin table, rather than a lookup per row.
    Folding in Python (rather than a window function) keeps this working the
    same on SQLite; the log is a few thousand rows a year at this event's size.
    """
    events = (AuditEvent.query
              .filter(AuditEvent.verb.in_(TEAM_STATUS_VERBS),
                      AuditEvent.target_type == AuditTargetType.TEAM)
              .order_by(AuditEvent.occurred_at.asc(), AuditEvent.id.asc())
              .all())

    # Ascending order means each team's last write wins.
    return {event.target_id: event for event in events}
