"""Background notification worker.

Single always-on process that drains the email outbox (``NotificationLog``).
Web requests only enqueue PENDING rows; this is the only place that talks to
SES, so there is exactly one sender — no row locking or claim protocol needed.

Run it directly:  ``python -m app.worker``
"""

import logging
import time
from datetime import datetime, timedelta, timezone

import boto3

from app import create_app
from app.config import Config
from app.models import db, NotificationLog, NotificationStatus


def _naive_utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _backoff(attempts):
    """Exponential backoff capped at 30 minutes."""
    return timedelta(minutes=min(2 ** attempts, 30))


def _make_ses_client():
    return boto3.client(
        'ses',
        aws_access_key_id=Config.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=Config.AWS_SECRET_ACCESS_KEY,
        region_name=Config.AWS_REGION,
    )


def _deliver(ses, row):
    """Send one outbox row according to the configured mail backend."""
    recipient = row.recipient
    if Config.MAIL_BACKEND == 'noop':
        return
    if Config.MAIL_BACKEND == 'console':
        logging.info("[console mail] to=%s subject=%s", recipient.email, row.subject)
        return

    response = ses.send_email(
        Source=f"{Config.APPLICATION_NAME} <{Config.NOTIFICATION_EMAIL}>",
        Destination={'ToAddresses': [recipient.email]},
        ReplyToAddresses=[Config.CONTACT_EMAIL],
        Message={
            'Subject': {'Data': row.subject, 'Charset': 'UTF-8'},
            'Body': {'Html': {'Data': row.body_html, 'Charset': 'UTF-8'}},
        },
    )
    row.email_id = response['MessageId']


def _attempt(ses, row):
    try:
        _deliver(ses, row)
        row.status = NotificationStatus.SENT
        row.sent_at = _naive_utcnow()
        logging.info("Sent notification %s (%s) to %s", row.id, row.template_name, row.recipient.email)
    except Exception as e:
        row.attempts += 1
        row.error_message = str(e)
        if row.attempts >= Config.MAX_SEND_ATTEMPTS:
            row.status = NotificationStatus.FAILED
            row.failed_at = _naive_utcnow()
            logging.error("Notification %s FAILED after %d attempts: %s", row.id, row.attempts, e)
        else:
            row.next_attempt_at = _naive_utcnow() + _backoff(row.attempts)
            logging.warning("Notification %s attempt %d failed, will retry: %s", row.id, row.attempts, e)


def drain_once(ses, batch_size=50):
    """Send all currently-due notifications. Returns the number processed."""
    now = _naive_utcnow()
    due = (NotificationLog.query
           .filter(NotificationLog.status == NotificationStatus.PENDING,
                   NotificationLog.scheduled_for <= now,
                   NotificationLog.next_attempt_at <= now)
           .order_by(NotificationLog.scheduled_for)
           .limit(batch_size)
           .all())
    for row in due:
        _attempt(ses, row)
    if due:
        db.session.commit()
    return len(due)


def run_worker(poll_interval=None):
    poll_interval = poll_interval or Config.WORKER_POLL_INTERVAL
    app = create_app()
    ses = _make_ses_client() if Config.MAIL_BACKEND == 'ses' else None
    logging.info("Notification worker started (backend=%s, interval=%ss)",
                 Config.MAIL_BACKEND, poll_interval)
    with app.app_context():
        while True:
            try:
                drain_once(ses)
            except Exception as e:
                db.session.rollback()
                logging.error("Worker drain error: %s", e)
            time.sleep(poll_interval)


if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )
    run_worker()
