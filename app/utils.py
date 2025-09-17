import logging
import os
import json
from datetime import datetime
from PIL import Image
from PIL.ExifTags import TAGS, GPSTAGS
from app.config import Config
import boto3

from app.models import TeamMembershipStatus


def get_registration_deadline_info():
    """Get registration deadline information for template display."""
    try:
        deadline = Config.REGISTRATION_CLOSES_AT
        now = datetime.now(deadline.tzinfo)

        return {
            'deadline': deadline,
            'deadline_str': deadline.strftime('%B %d, %Y at %I:%M %p'),
            'is_closed': now > deadline,
            'days_remaining': (deadline - now).days if now <= deadline else 0
        }
    except Exception as e:
        import logging
        logging.warning(f"Failed to parse registration deadline: {e}")
        return {
            'deadline': None,
            'deadline_str': 'TBD',
            'is_closed': False,
            'days_remaining': 9999
        }


def format_hh_mm_from_seconds(total_seconds):
    """Converts total seconds to a HH:MM formatted string."""
    if total_seconds is None:
        return ""
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    return f"{hours:d}:{minutes:02d}"

def format_mm_ss_from_seconds(total_seconds):
    """Converts total seconds to a MM:SS formatted string."""
    if total_seconds is None:
        return ""
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes:d}:{seconds:02d}"

def parse_hh_mm_to_seconds(duration_str):
    """Converts a HH:MM string to total seconds.
    Raises ValueError if format is incorrect or minutes are invalid.
    """
    if not duration_str:
        return 0

    parts = duration_str.split(':')
    if len(parts) != 2:
        raise ValueError("Duration must be in HH:MM format.")

    try:
        hours = int(parts[0])
        minutes = int(parts[1])
    except ValueError:
        raise ValueError("Duration components must be integers.")

    if not (0 <= minutes < 60):
        raise ValueError("Minutes component must be between 00 and 59.")

    return hours * 3600 + minutes * 60

def parse_mm_ss_to_seconds(pace_str):
    """Converts a MM:SS string to total seconds.
    Raises ValueError if format is incorrect or seconds are invalid.
    """
    if not pace_str:
        return 0

    parts = pace_str.split(':')
    if len(parts) != 2:
        raise ValueError("Pace must be in MM:SS format.")

    try:
        minutes = int(parts[0])
        seconds = int(parts[1])
    except ValueError:
        raise ValueError("Pace components must be integers.")

    if not (0 <= seconds < 60):
        raise ValueError("Seconds component must be between 00 and 59.")

    return minutes * 60 + seconds

def load_station_names():
    """Load station names from lrr_1_line.geojson file"""
    try:
        with open('data/lrr_1_line.geojson', 'r') as f:
            data = json.load(f)
        sorted_features = sorted(data["features"], key=lambda item: item['properties']['id'], reverse=True)
        station_names = [feature['properties']['name'] for feature in sorted_features]
        return station_names
    except (FileNotFoundError, KeyError, json.JSONDecodeError) as e:
        print(f"Error loading station names: {e}")
        return []


def get_exif_data(image_path):
    """Extract EXIF data from image"""
    image = Image.open(image_path)
    raw_exif = image.getexif() or {}
    exif_data = {TAGS.get(key, key): value for key, value in raw_exif.items()}
    if 'GPSInfo' not in exif_data:
        return exif_data
    gps_data = {GPSTAGS.get(key, key): value for key, value in raw_exif.get_ifd(0x8825).items()}
    return {**gps_data, **exif_data}


def get_gps_data(exif_data):
    """Convert GPS EXIF data to decimal degrees"""
    if 'GPSLatitude' not in exif_data or 'GPSLongitude' not in exif_data:
        return None

    def convert_to_degrees(value):
        d, m, s = value
        return d + (m / 60.0) + (s / 3600.0)

    lat = convert_to_degrees(exif_data['GPSLatitude'])
    lon = convert_to_degrees(exif_data['GPSLongitude'])

    # Adjust for hemisphere
    if exif_data.get('GPSLatitudeRef') == 'S':
        lat = -lat
    if exif_data.get('GPSLongitudeRef') == 'W':
        lon = -lon

    return lat, lon


def get_capture_time(exif_data):
    """Extract capture time from EXIF data dict, like the working script version"""
    date_time = exif_data.get('DateTimeOriginal') or exif_data.get('DateTime')
    if not date_time:
        return None
    try:
        from datetime import datetime
        return datetime.strptime(date_time, '%Y:%m:%d %H:%M:%S')
    except ValueError:
        return None


def is_allowed_image(filename):
    """Check if filename has allowed image extension"""
    return os.path.splitext(filename)[1].lower() in Config.ALLOWED_EXTENSIONS


def validate_image_content(file_content):
    """Validate image file content using magic numbers (file signatures)"""

    # Image file signatures (magic numbers)
    image_signatures = {
        b'\xFF\xD8\xFF': ['jpg', 'jpeg'],  # JPEG
        b'\x89\x50\x4E\x47\x0D\x0A\x1A\x0A': ['png'],  # PNG
        b'\x47\x49\x46\x38\x37\x61': ['gif'],  # GIF87a
        b'\x47\x49\x46\x38\x39\x61': ['gif'],  # GIF89a
        b'\x42\x4D': ['bmp'],  # BMP
        b'\x52\x49\x46\x46': ['webp'],  # WEBP (needs further validation)
        b'\x49\x49\x2A\x00': ['tiff'],  # TIFF (little endian)
        b'\x4D\x4D\x00\x2A': ['tiff'],  # TIFF (big endian)
    }

    if len(file_content) < 12:
        return False

    # Check magic numbers
    for signature, formats in image_signatures.items():
        if file_content.startswith(signature):
            return True

    # Special case for WEBP - needs additional validation
    if file_content.startswith(b'\x52\x49\x46\x46') and len(file_content) >= 12:
        if file_content[8:12] == b'WEBP':
            return True

    # Special case for HEIC/HEIF - check for ftyp box with HEIC brands
    if len(file_content) >= 24:
        # HEIC files start with ftyp box, check for various HEIC brand types
        heic_brands = [b'heic', b'heix', b'hevc', b'hevx', b'heim', b'heis', b'hevm', b'hevs', b'avci']

        # Look for ftyp box (starts at offset 4) and check brand at offset 8
        if file_content[4:8] == b'ftyp':
            brand = file_content[8:12]
            if brand in heic_brands:
                return True

            # Also check compatible brands starting at offset 16
            for i in range(16, min(len(file_content) - 3, 64), 4):
                if file_content[i:i+4] in heic_brands:
                    return True

    return False


def secure_filename_enhanced(filename):
    """Enhanced secure filename that removes more potential security risks"""
    from werkzeug.utils import secure_filename as werkzeug_secure_filename
    import re

    # Use werkzeug's secure_filename first
    filename = werkzeug_secure_filename(filename)

    # Additional security measures
    # Remove any remaining special characters and normalize
    filename = re.sub(r'[^\w\-_.]', '', filename)

    # Ensure filename is not empty
    if not filename or filename.startswith('.'):
        filename = 'upload.bin'

    # Limit filename length
    if len(filename) > 100:
        name, ext = os.path.splitext(filename)
        filename = name[:95] + ext

    return filename


def find_team_by_id(team_id):
    """Find team in database by id"""
    from app.models import Team
    return Team.query.filter_by(id=team_id).first()


def find_team_by_gallery_hash(gallery_hash):
    """Find team in database by gallery_hash"""
    from app.models import Team
    return Team.query.filter_by(gallery_hash=gallery_hash).first()


def parse_exif_datetime(exif_datetime_str):
    """Parse EXIF datetime string to Python datetime"""
    if not exif_datetime_str:
        return None
    try:
        from datetime import datetime
        return datetime.strptime(exif_datetime_str, "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None


def get_mime_type(file_path):
    """Get MIME type from file extension"""
    import mimetypes
    mime_type, _ = mimetypes.guess_type(file_path)
    return mime_type or 'application/octet-stream'


def convert_heic_to_jpeg(heic_path, jpeg_path, quality=85):
    """Convert HEIC file to JPEG format using pillow-heif"""
    try:
        # pillow-heif is already registered in __init__.py
        image = Image.open(heic_path)

        # Convert to RGB if necessary (HEIC can be in other color spaces)
        if image.mode != 'RGB':
            image = image.convert('RGB')

        # Save as JPEG with specified quality
        image.save(jpeg_path, 'JPEG', quality=quality, optimize=True)
        return True
    except Exception as e:
        print(f"Error converting HEIC to JPEG: {e}")
        return False


def is_heic_file(filename):
    """Check if file is HEIC format"""
    return filename.lower().endswith(('.heic', '.heif'))


def send_email_with_logging(notification_type, recipient_user, subject, template_name, template_context=None, related_team=None, metadata=None):
    """Send email with notification logging - immediate dispatch pattern"""
    from flask import render_template
    from app.models import NotificationLog, NotificationStatus, db
    import json

    # Create log entry with PENDING status
    log_entry = NotificationLog(
        notification_type=notification_type,
        recipient_user_id=recipient_user.id,
        related_team_id=related_team.id if related_team else None,
        subject=subject,
        template_name=template_name,
        notification_data=json.dumps(metadata) if metadata else None,
        status=NotificationStatus.PENDING
    )

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
            **(template_context or {})
        }

        email_html = render_template(f'emails/{template_name}.html', **context)

        # Don't send email unless we're in production
        if os.getenv("FLASK_ENV") == 'production':
            response = ses_client.send_email(
                Source=f"{Config.APPLICATION_NAME} <{Config.NOTIFICATION_EMAIL}>",
                Destination={
                    'ToAddresses': [recipient_user.email]
                },
                ReplyToAddresses=[Config.CONTACT_EMAIL],
                Message={
                    'Subject': {
                        'Data': subject,
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
            log_entry.email_id = response['MessageId']
        else:
            logging.info(f"Not sending email in development mode: {subject} to {recipient_user.email}")
        logging.info(f"Email to {recipient_user.email} sent with subject: {subject}")

        # Update log entry to SENT
        from datetime import datetime, timezone
        log_entry.status = NotificationStatus.SENT
        log_entry.sent_at = datetime.now(timezone.utc)
        db.session.add(log_entry)
        db.session.commit()

        return True

    except Exception as e:
        # Update log entry to FAILED
        from datetime import datetime, timezone
        log_entry.status = NotificationStatus.FAILED
        log_entry.failed_at = datetime.now(timezone.utc)
        log_entry.error_message = str(e)
        logging.error(f"Error sending email: {e}")

        try:
            db.session.add(log_entry)
            db.session.commit()
        except Exception as db_error:
            logging.error(f"Failed to update notification log: {db_error}")
            db.session.rollback()

        print(f"Failed to send email to {recipient_user.email}: {e}")
        return False


def log_member_join_event(team, new_member):
    """Log a member join event for later digest processing (queue pattern)"""
    from app.models import NotificationLog, NotificationType, NotificationStatus, db
    import json

    # Create log entry for the captain with member join details
    metadata = {
        'member_name': new_member.user.name,
        'member_email': new_member.user.email,
        'joined_at': new_member.joined_at.isoformat(),
        'preferred_miles': float(new_member.preferred_miles) if new_member.preferred_miles else None,
        'planned_pace_seconds': new_member.planned_pace_seconds,
        'willing_to_lead': new_member.willing_to_lead
    }

    log_entry = NotificationLog(
        notification_type=NotificationType.NEW_MEMBERS_DIGEST,
        recipient_user_id=team.captain_id,
        related_team_id=team.id,
        status=NotificationStatus.PENDING,
        template_name='new_members_digest',
        notification_data=json.dumps(metadata)
    )

    db.session.add(log_entry)
    db.session.commit()

    print(f"Logged member join event: {new_member.user.name} joined {team.name}")


def process_new_members_digests():
    """Process pending member join notifications and send daily digest emails"""
    from app.models import NotificationLog, NotificationType, NotificationStatus, TeamMembership, db
    from flask import url_for
    import json
    from datetime import datetime, timezone
    from collections import defaultdict

    # Find all pending member join notifications, grouped by team captain
    pending_notifications = NotificationLog.query.filter_by(
        notification_type=NotificationType.NEW_MEMBERS_DIGEST,
        status=NotificationStatus.PENDING
    ).all()

    if not pending_notifications:
        print("No pending member join notifications to process")
        return

    # Group by captain (recipient) and team
    captain_team_notifications = defaultdict(lambda: defaultdict(list))

    for notification in pending_notifications:
        captain_id = notification.recipient_user_id
        team_id = notification.related_team_id
        captain_team_notifications[captain_id][team_id].append(notification)

    # Process each captain's notifications
    for captain_id, team_notifications in captain_team_notifications.items():
        for team_id, notifications in team_notifications.items():
            try:
                # Get team and captain info
                team = notifications[0].team
                captain = notifications[0].recipient

                # Check if captain wants to receive digest notifications
                if not captain.captain_notifications_enabled:
                    print(f"Skipping digest for {captain.email} - notifications disabled")
                    # Mark notifications as sent even though we're not sending
                    for notification in notifications:
                        notification.status = NotificationStatus.SENT
                        notification.sent_at = datetime.now(timezone.utc)
                    continue

                # Get current team member count
                total_members = TeamMembership.query.filter_by(
                    team_id=team_id,
                    status=TeamMembershipStatus.ACTIVE
                ).count()

                # Parse member info from notification metadata
                new_members = []
                for notification in notifications:
                    metadata = json.loads(notification.notification_data) if notification.notification_data else {}
                    # Find the actual membership record for template rendering
                    member_email = metadata.get('member_email')
                    if member_email:
                        from app.models import User
                        user = User.query.filter_by(email=member_email).first()
                        if user:
                            membership = TeamMembership.query.filter_by(
                                user_id=user.id,
                                team_id=team_id
                            ).first()
                            if membership:
                                new_members.append(membership)

                if not new_members:
                    continue

                # Generate team URL
                team_url = url_for('teams.team_members', team_id=team.id, _external=True)

                # Prepare email context
                subject = f"New Team Member{'s' if len(new_members) > 1 else ''} - {team.name}"
                context = {
                    'team': team,
                    'captain_name': captain.name,
                    'new_members': new_members,
                    'total_members': total_members,
                    'team_url': team_url
                }

                # Send digest email using immediate dispatch
                success = send_email_with_logging(
                    notification_type=NotificationType.NEW_MEMBERS_DIGEST,
                    recipient_user=captain,
                    subject=subject,
                    template_name='new_members_digest',
                    template_context=context,
                    related_team=team,
                    metadata={'member_count': len(new_members)}
                )

                if success:
                    # Mark all individual notifications as sent
                    for notification in notifications:
                        notification.status = NotificationStatus.SENT
                        notification.sent_at = datetime.now(timezone.utc)

                    print(f"Sent new members digest to {captain.email} for team {team.name} ({len(new_members)} members)")

            except Exception as e:
                print(f"Failed to process digest for captain {captain_id}, team {team_id}: {e}")
                # Leave as pending for retry on next run
                continue

    db.session.commit()
    print(f"Processed {len(pending_notifications)} member join notifications")

