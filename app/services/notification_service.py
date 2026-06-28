"""Notification service — the email outbox.

Web requests call :func:`enqueue` (or :func:`enqueue_member_join`) to render an
email and insert a PENDING row into ``NotificationLog``. They never talk to SES.
A single background worker (see :mod:`app.worker`) drains the outbox and sends.

Rendering happens here, at enqueue time, while we hold a request/app context and
live ORM objects — the stored ``body_html`` is what the worker sends verbatim.
Direct emails are rendered once; digests are re-rendered as members coalesce in.
"""

import json
from datetime import datetime, timedelta, timezone

from flask import render_template, url_for

from app.config import Config
from app.models import (
    db, NotificationLog, NotificationStatus, NotificationType,
    TeamMembership, TeamMembershipStatus,
)


def _naive_utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _render(template_name, template_context):
    """Render an email template with the shared global context merged in."""
    context = {
        'contact_email': Config.CONTACT_EMAIL,
        'event_name': Config.EVENT_NAME,
        'event_url': Config.EVENT_URL,
        **(template_context or {}),
    }
    return render_template(f'emails/{template_name}.html', **context)


def enqueue(notification_type, recipient_user, subject, template_name,
            template_context=None, related_team=None, metadata=None,
            scheduled_for=None, dedup_key=None):
    """Render an email and queue it for delivery.

    Returns the created NotificationLog row, or None if ``dedup_key`` matches an
    existing pending/sent row (idempotency).
    """
    if dedup_key:
        existing = NotificationLog.query.filter(
            NotificationLog.dedup_key == dedup_key,
            NotificationLog.status.in_([NotificationStatus.PENDING, NotificationStatus.SENT]),
        ).first()
        if existing:
            return None

    row = NotificationLog(
        notification_type=notification_type,
        recipient_user_id=recipient_user.id,
        related_team_id=related_team.id if related_team else None,
        status=NotificationStatus.PENDING,
        subject=subject,
        template_name=template_name,
        body_html=_render(template_name, template_context),
        notification_data=json.dumps(metadata) if metadata else None,
        scheduled_for=scheduled_for or _naive_utcnow(),
        next_attempt_at=_naive_utcnow(),
        dedup_key=dedup_key,
    )
    db.session.add(row)
    db.session.commit()
    return row


# --- New-member digests (coalescing) ---

def _next_digest_time():
    """Next DIGEST_HOUR in the event timezone, as a naive UTC datetime."""
    tz = Config.EVENT_START_TIME.tzinfo
    now_local = datetime.now(tz)
    target = now_local.replace(hour=Config.DIGEST_HOUR, minute=0, second=0, microsecond=0)
    if target <= now_local:
        target += timedelta(days=1)
    return target.astimezone(timezone.utc).replace(tzinfo=None)


def _render_digest(team, member_user_ids):
    """Render the digest body for the given member user ids (in join order)."""
    captain = team.captain
    # Re-fetch memberships so the template gets live objects (.user, pace, etc.).
    memberships = [
        TeamMembership.query.filter_by(user_id=uid, team_id=team.id).first()
        for uid in member_user_ids
    ]
    new_members = [m for m in memberships if m is not None]
    total_members = TeamMembership.query.filter_by(
        team_id=team.id, status=TeamMembershipStatus.ACTIVE
    ).count()
    subject = f"New Team Member{'s' if len(new_members) > 1 else ''} - {team.name}"
    body = _render('new_members_digest', {
        'team': team,
        'captain_name': captain.name,
        'new_members': new_members,
        'total_members': total_members,
        'team_url': url_for('teams.team_members', team_id=team.id, _external=True),
    })
    return subject, body, new_members


def enqueue_member_join(team, membership):
    """Coalesce a new member into the team's pending digest for the next window.

    Captains who have disabled notifications get no digest (decided here, at join
    time). If a pending digest already exists for this captain/team/window, the
    member is appended and the row is re-rendered; otherwise a new one is queued.
    """
    captain = team.captain
    if not captain.captain_notifications_enabled:
        return None

    window = _next_digest_time()
    dedup_key = f"digest:{team.captain_id}:{team.id}:{window:%Y%m%d}"

    row = NotificationLog.query.filter_by(
        dedup_key=dedup_key, status=NotificationStatus.PENDING
    ).first()

    if row:
        data = json.loads(row.notification_data) if row.notification_data else {'member_user_ids': []}
        if membership.user_id not in data['member_user_ids']:
            data['member_user_ids'].append(membership.user_id)
        subject, body, _ = _render_digest(team, data['member_user_ids'])
        row.subject = subject
        row.body_html = body
        row.notification_data = json.dumps(data)
        db.session.commit()
        return row

    member_user_ids = [membership.user_id]
    subject, body, _ = _render_digest(team, member_user_ids)
    row = NotificationLog(
        notification_type=NotificationType.NEW_MEMBERS_DIGEST,
        recipient_user_id=captain.id,
        related_team_id=team.id,
        status=NotificationStatus.PENDING,
        subject=subject,
        template_name='new_members_digest',
        body_html=body,
        notification_data=json.dumps({'member_user_ids': member_user_ids}),
        scheduled_for=window,
        next_attempt_at=window,
        dedup_key=dedup_key,
    )
    db.session.add(row)
    db.session.commit()
    return row
