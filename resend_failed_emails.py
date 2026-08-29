#!/usr/bin/env python3
"""
Resend failed email notifications.
Run this inside the Docker container to retry sending previously failed emails.
"""

from app import create_app
from app.models import NotificationLog, NotificationStatus, db
import json

def main():
    app = create_app()

    # Configure Flask for URL generation outside request context
    from app.config import Config
    from urllib.parse import urlparse

    canonical_url = Config.CANONICAL_URL
    parsed_url = urlparse(canonical_url)
    app.config['SERVER_NAME'] = parsed_url.netloc
    app.config['PREFERRED_URL_SCHEME'] = parsed_url.scheme
    app.config['APPLICATION_ROOT'] = parsed_url.path or '/'

    with app.app_context():
        # Find all failed notifications
        failed_notifications = NotificationLog.query.filter_by(
            status=NotificationStatus.FAILED
        ).all()

        if not failed_notifications:
            print("No failed notifications found")
            return

        print(f"Found {len(failed_notifications)} failed notifications to retry")

        success_count = 0
        retry_count = 0

        for notification in failed_notifications:
            try:
                # Parse template context from notification data
                template_context = {}
                if notification.notification_data:
                    metadata = json.loads(notification.notification_data)
                    # Add any context needed for the email template
                    template_context.update(metadata)

                # Get recipient and related team
                recipient = notification.recipient
                team = notification.team

                if not recipient:
                    print(f"Skipping notification {notification.id}: recipient not found")
                    continue

                print(f"Retrying email to {recipient.email}: {notification.subject}")

                # Attempt to resend by directly updating this notification record
                from app.utils import send_email_with_logging
                from datetime import datetime, timezone
                import boto3
                import os
                from flask import render_template

                try:
                    # Initialize SES client
                    ses_client = boto3.client(
                        'ses',
                        aws_access_key_id=Config.AWS_ACCESS_KEY_ID,
                        aws_secret_access_key=Config.AWS_SECRET_ACCESS_KEY,
                        region_name=Config.AWS_REGION
                    )

                    context = {
                        'contact_email': Config.CONTACT_EMAIL,
                        'event_name': Config.EVENT_NAME,
                        'event_url': Config.EVENT_URL,
                        'team': team,
                        'recipient': recipient,
                        **template_context
                    }

                    # Add template-specific context
                    if notification.template_name == 'member_joined' and team:
                        from app.models import TeamMembership
                        membership = TeamMembership.query.filter_by(
                            user_id=recipient.id,
                            team_id=team.id
                        ).first()
                        context['membership'] = membership


                    email_html = render_template(f'emails/{notification.template_name}.html', **context)

                    # Send email
                    if os.getenv("FLASK_ENV") == 'production':
                        response = ses_client.send_email(
                            Source=f"{Config.APPLICATION_NAME} <{Config.NOTIFICATION_EMAIL}>",
                            Destination={
                                'ToAddresses': [recipient.email]
                            },
                            ReplyToAddresses=[Config.CONTACT_EMAIL],
                            Message={
                                'Subject': {
                                    'Data': notification.subject,
                                    'Charset': 'UTF-8'
                                },
                                'Body': {
                                    'Html': {
                                        'Data': email_html,
                                        'Charset': 'UTF-8'
                                    }
                                }
                            }
                        )
                        notification.email_id = response['MessageId']
                    else:
                        print(f"Not sending email in development mode: {notification.subject} to {recipient.email}")

                    # Update the existing notification record to SENT
                    notification.status = NotificationStatus.SENT
                    notification.sent_at = datetime.now(timezone.utc)
                    notification.error_message = None  # Clear previous error

                    success_count += 1
                    print(f"✓ Successfully resent to {recipient.email}")

                except Exception as email_error:
                    # Update error message but keep as FAILED
                    notification.error_message = f"Retry failed: {str(email_error)}"
                    retry_count += 1
                    print(f"✗ Failed to resend to {recipient.email}: {email_error}")

            except Exception as e:
                retry_count += 1
                print(f"✗ Error retrying notification {notification.id}: {e}")

        db.session.commit()
        print(f"\nResend complete: {success_count} successful, {retry_count} failed")

if __name__ == "__main__":
    main()