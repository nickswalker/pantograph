#!/usr/bin/env python3
"""
Announce LRR 2026 to people who opted in to be notified, last season.

This year's DB was reset, so those accounts no longer exist -- there's no
User row to hang a NotificationLog off of. Rather than fabricate fake user
rows just to satisfy that foreign key, this sends directly via SES, reusing
the same delivery shape as app/worker.py::_deliver (From/Reply-To/Subject/
Body), driven off a standalone CSV export instead of the `user` table:

    lrr26_optin_recipients.csv   -- name,email (not committed; see .gitignore)

Progress is tracked in a local sent-log (lrr26_announce_sent.jsonl) so the
script is safe to re-run after an interruption -- already-sent addresses are
skipped.

Usage:
    uv run python send_lrr_announcement.py --dry-run   # render + list only
    uv run python send_lrr_announcement.py --live       # actually send via SES
"""

import argparse
import csv
import json
import logging
import time
from pathlib import Path
from urllib.parse import urlparse

from app import create_app
from app.config import Config

SUBJECT = "Light Rail Relay Returns October 3rd"
TEMPLATE_NAME = "lrr_reminder"

RECIPIENTS_CSV = Path(__file__).parent / "lrr26_optin_recipients.csv"
SENT_LOG = Path(__file__).parent / "lrr26_announce_sent.jsonl"

# SES default sending rate is modest; pace requests so we don't get throttled.
SEND_DELAY_SECONDS = 0.5


def load_recipients():
    with open(RECIPIENTS_CSV, newline='') as f:
        return [(row['name'], row['email']) for row in csv.DictReader(f)]


def load_already_sent():
    if not SENT_LOG.exists():
        return set()
    sent = set()
    with open(SENT_LOG) as f:
        for line in f:
            line = line.strip()
            if line:
                sent.add(json.loads(line)['email'])
    return sent


def record_sent(email, message_id):
    with open(SENT_LOG, 'a') as f:
        f.write(json.dumps({'email': email, 'message_id': message_id,
                             'sent_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true',
                         help="Actually send via SES. Without this, nothing is sent "
                              "(renders and lists recipients only).")
    parser.add_argument('--dry-run', action='store_true',
                         help="Alias for the default (no --live) behavior.")
    parser.add_argument('--only', metavar='EMAIL',
                         help="Send (or dry-run) to just this one address from the CSV, "
                              "ignoring the sent-log skip -- for test sends. Doesn't get "
                              "recorded in the sent-log, so it won't affect the real batch run.")
    args = parser.parse_args()

    recipients = load_recipients()

    if args.only:
        todo = [(name, email) for name, email in recipients if email == args.only]
        if not todo:
            print(f"No recipient with email {args.only!r} found in {RECIPIENTS_CSV.name}.")
            return
        print(f"Test mode: sending only to {todo[0][0]} <{todo[0][1]}> (not recorded in sent-log).")
    else:
        already_sent = load_already_sent()
        todo = [(name, email) for name, email in recipients if email not in already_sent]
        print(f"{len(recipients)} recipient(s) in {RECIPIENTS_CSV.name}, "
              f"{len(already_sent)} already sent, {len(todo)} to go.")

    if not todo:
        print("Nothing to do.")
        return

    app = create_app()
    parsed = urlparse(Config.CANONICAL_URL)
    app.config['SERVER_NAME'] = parsed.netloc
    app.config['PREFERRED_URL_SCHEME'] = parsed.scheme
    app.config['APPLICATION_ROOT'] = parsed.path or '/'

    with app.app_context(), app.test_request_context():
        # Reuse the app's own template renderer so this picks up the same
        # global context (and ref tag) every other notification email gets.
        from app.services.notification_service import _render as render_email
        body_html = render_email(TEMPLATE_NAME, {})

    if not args.live:
        for name, email in todo:
            print(f"  [dry-run] would send: {name} <{email}>")
        print("\nRe-run with --live to actually send via SES.")
        return

    import boto3
    ses = boto3.client(
        'ses',
        aws_access_key_id=Config.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=Config.AWS_SECRET_ACCESS_KEY,
        region_name=Config.AWS_REGION,
    )

    sent = 0
    failed = 0
    for name, email in todo:
        try:
            response = ses.send_email(
                Source=f"{Config.APPLICATION_NAME} <{Config.NOTIFICATION_EMAIL}>",
                Destination={'ToAddresses': [email]},
                ReplyToAddresses=[Config.CONTACT_EMAIL],
                Message={
                    'Subject': {'Data': SUBJECT, 'Charset': 'UTF-8'},
                    'Body': {'Html': {'Data': body_html, 'Charset': 'UTF-8'}},
                },
            )
            if not args.only:
                record_sent(email, response['MessageId'])
            sent += 1
            print(f"  sent: {name} <{email}> ({response['MessageId']})")
        except Exception as e:
            failed += 1
            logging.exception(f"Failed to send to {name} <{email}>")
        time.sleep(SEND_DELAY_SECONDS)

    print(f"\nDone. {sent} sent, {failed} failed.")


if __name__ == '__main__':
    main()
