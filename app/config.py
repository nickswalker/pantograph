import datetime
import os


def get_secret(secret_name):
    """Get secrets from environment, Docker secrets, or local files"""
    # Check environment variable
    secret = os.getenv(secret_name.upper())
    if secret:
        return secret

    # Check Docker Secrets path
    try:
        with open(f"/run/secrets/{secret_name}") as secret_file:
            return secret_file.read().strip()
    except FileNotFoundError:
        pass

    # Fallback to local file
    try:
        with open(f"./config/{secret_name}.txt") as secret_file:
            return secret_file.read().strip()
    except FileNotFoundError:
        pass

    raise RuntimeError(f"Secret '{secret_name}' not found in any source")


class Config:
    # Application Name
    APPLICATION_NAME = "Pantograph"
    EVENT_NAME = "Light Rail Relay 2025"
    EVENT_URL = "https://raceconditionrunning.com/light-rail-relay-25"
    REGISTRATION_CLOSES_AT = datetime.datetime.fromisoformat("2025-09-25-23:59:59-07:00")  # ISO 8601 format with timezone

    EVENT_START_TIME = datetime.datetime.fromisoformat("2025-09-27T08:30:00-07:00")  # ISO 8601 format with timezone
    # For testing with old images
    #EVENT_START_TIME = datetime.datetime.fromisoformat("2024-09-28T08:30:00-07:00")  # ISO 8601 format with timezone

    # Core Flask configuration
    SECRET_KEY = get_secret('SECRET_KEY')

    # Session security configuration
    SESSION_COOKIE_SECURE = True  # Only send cookies over HTTPS
    SESSION_COOKIE_HTTPONLY = True  # Prevent JavaScript access to session cookies
    SESSION_COOKIE_SAMESITE = 'Lax'  # CSRF protection
    PERMANENT_SESSION_LIFETIME = 86400  # 24 hours in seconds

    # Database configuration - use data directory for persistence
    db_path = os.path.join(os.getcwd(), 'data', 'pantograph.db')
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{db_path}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # OAuth configuration
    GOOGLE_CLIENT_ID = get_secret('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = get_secret('GOOGLE_CLIENT_SECRET')
    GITHUB_CLIENT_ID = get_secret('GITHUB_CLIENT_ID')
    GITHUB_CLIENT_SECRET = get_secret('GITHUB_CLIENT_SECRET')
    MICROSOFT_CLIENT_ID = get_secret('MICROSOFT_CLIENT_ID')
    MICROSOFT_CLIENT_SECRET = get_secret('MICROSOFT_CLIENT_SECRET')

    # Admin configuration
    ADMIN_EMAIL = get_secret('ADMIN_EMAIL')
    CONTACT_EMAIL = get_secret('CONTACT_EMAIL')
    NOTIFICATION_EMAIL = get_secret('NOTIFICATION_EMAIL')

    # Site configuration
    CANONICAL_URL = get_secret('CANONICAL_URL')  # e.g. 'https://pantograph.example.com'

    # Email configuration - AWS SES
    AWS_ACCESS_KEY_ID = get_secret('AWS_ACCESS_KEY_ID')
    AWS_SECRET_ACCESS_KEY = get_secret('AWS_SECRET_ACCESS_KEY')
    AWS_REGION = get_secret('AWS_REGION')

    # Notification delivery (handled by the background worker).
    #   'ses'     - actually send via AWS SES
    #   'console' - log instead of sending (default outside production)
    #   'noop'    - mark sent without sending
    MAIL_BACKEND = os.getenv(
        'MAIL_BACKEND',
        'ses' if os.getenv('FLASK_ENV') == 'production' else 'console'
    )
    MAX_SEND_ATTEMPTS = 5          # retries before a notification is marked FAILED
    WORKER_POLL_INTERVAL = 30      # seconds between worker drains
    DIGEST_HOUR = 7               # local (event timezone) hour to send new-member digests

    # Upload configuration
    UPLOAD_FOLDER = './uploads'
    MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
    MAX_PHOTOS_PER_TEAM = 30
    ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.heic', '.tiff'}

    # Gallery thumbnails (served in the grid + map popups instead of full-size originals)
    THUMBNAIL_DIR = 'thumbs'        # subdirectory of each team's upload folder
    THUMBNAIL_MAX_SIZE = 400        # longest edge, px
    THUMBNAIL_QUALITY = 75


# OAuth providers configuration for templates
OAUTH_PROVIDERS = [
    {'name': 'google', 'display_name': 'Google'},
    {'name': 'github', 'display_name': 'GitHub'},
    {'name': 'microsoft', 'display_name': 'Microsoft'}
]