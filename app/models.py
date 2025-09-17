import enum
import secrets
import uuid
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime, timezone
from passlib.hash import argon2

db = SQLAlchemy()

# --- Enums for Data Integrity ---

class TeamStatus(enum.Enum):
    PENDING = 'pending'
    OPEN = 'open'
    CLOSED = 'closed'
    WITHDRAWN = 'withdrawn'
    CANCELLED = 'cancelled'

class TeamFormat(enum.Enum):
    SOLO = 'Solo'
    TEAM = 'Team'

class TeamMembershipStatus(enum.Enum):
    ACTIVE = 'active'
    WITHDRAWN = 'withdrawn'
    REMOVED = 'removed'

class OAuthProvider(enum.Enum):
    GOOGLE = 'google'
    GITHUB = 'github'
    MICROSOFT = 'microsoft'

class NotificationType(enum.Enum):
    TEAM_APPROVAL = 'team_approved'
    NEW_MEMBERS_DIGEST = 'new_members_digest'
    TEAM_MEMBER_REMOVAL = 'member_removed'
    MEMBER_JOINED = 'member_joined'
    TEAM_CREATION = 'team_created'
    CAPTAIN_TRANSFER = 'captain_transferred'
    EVENT_UPDATE = 'event_updated'
    MISSING_PHOTOS = 'missing_photos'
    PAYMENT_REMINDER = 'payment_reminder'
    REGISTRATION_REMINDER = 'registration_reminder'

class NotificationStatus(enum.Enum):
    PENDING = 'pending'
    SENT = 'sent'
    FAILED = 'failed'
    BOUNCED = 'bounced'

class Team(db.Model):
    id = db.Column(db.String(8), primary_key=True, default=lambda: secrets.token_urlsafe(6))
    name = db.Column(db.String(255), unique=True, nullable=False)
    gallery_hash = db.Column(db.String(8), unique=True, nullable=False, default=lambda: secrets.token_urlsafe(6))  # Public gallery view hash
    format = db.Column(db.Enum(TeamFormat), nullable=False)
    estimated_duration_seconds = db.Column(db.Integer, nullable=False)  # Stored as total seconds
    comments = db.Column(db.Text, nullable=True)
    password_hash = db.Column(db.String(255), nullable=True)  # Optional password for joining
    invite_token = db.Column(db.String(32), unique=True, nullable=True) # Shareable, revocable invite token
    status = db.Column(db.Enum(TeamStatus), nullable=False, default=TeamStatus.PENDING)
    previous_baton_serial = db.Column(db.String(12), nullable=True)
    baton_serial = db.Column(db.String(12), nullable=True)
    captain_id = db.Column(db.String(8), db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    captain = db.relationship('User', foreign_keys=[captain_id], backref='captained_teams')

    def set_password(self, password):
        """Hashes and sets the team password."""
        if password:
            self.password_hash = argon2.hash(password)
        else:
            self.password_hash = None

    def check_password(self, password):
        """Verifies the team password against the stored hash."""
        if self.password_hash is None:
            return False  # No password is set
        return argon2.verify(password, self.password_hash)

    @property
    def has_password(self):
        """Returns True if the team has a password set."""
        return self.password_hash is not None

    @property
    def members(self):
        """Get all users who are members of this team"""
        return [membership.user for membership in self.memberships]

    def __repr__(self):
        return f'<Team {self.name}>'

class TeamMembership(db.Model):
    __tablename__ = 'team_membership'

    id = db.Column(db.String(8), primary_key=True, default=lambda: secrets.token_urlsafe(6))
    user_id = db.Column(db.String(8), db.ForeignKey('user.id'), nullable=False)
    team_id = db.Column(db.String(8), db.ForeignKey('team.id'), nullable=False)

    # Join preferences
    willing_to_lead = db.Column(db.Boolean, nullable=False, default=False)
    preferred_miles = db.Column(db.Numeric(4, 1), nullable=True)
    planned_pace_seconds = db.Column(db.Integer, nullable=True)  # Stored as seconds per mile
    preferred_station = db.Column(db.String(255), nullable=True)
    comments = db.Column(db.Text, nullable=True)
    status = db.Column(db.Enum(TeamMembershipStatus), nullable=False, default=TeamMembershipStatus.ACTIVE)

    joined_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships to User and Team
    user = db.relationship('User', backref='memberships')
    team = db.relationship('Team', backref='memberships')

    # Unique constraint to prevent duplicate memberships
    __table_args__ = (db.UniqueConstraint('user_id', 'team_id', name='unique_membership'),)

    def __repr__(self):
        return f'<TeamMembership {self.user.name} in {self.team.name}>'

class User(db.Model, UserMixin):
    id = db.Column(db.String(8), primary_key=True, default=lambda: secrets.token_urlsafe(6))
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(255), nullable=False)
    avatar_url = db.Column(db.String(500), nullable=True)
    provider = db.Column(db.Enum(OAuthProvider), nullable=False)
    provider_id = db.Column(db.String(255), nullable=False)  # OAuth provider user ID
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_login = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    is_admin = db.Column(db.Boolean, default=False)
    email_opt_in = db.Column(db.Boolean, default=False)
    captain_notifications_enabled = db.Column(db.Boolean, default=True)

    @property
    def teams(self):
        """Get all teams this user is a member of"""
        return [membership.team for membership in self.memberships]

    # Unique constraint on provider + provider_id
    __table_args__ = (db.UniqueConstraint('provider', 'provider_id', name='provider_user_uc'),)

    def is_captain_of(self, team):
        """Check if user is captain of a specific team"""
        return team.captain_id == self.id

    def get_captained_teams(self):
        """Get all teams this user captains"""
        return Team.query.filter_by(captain_id=self.id).all()

    def __repr__(self):
        return f'<User {self.email}>'


class Image(db.Model):
    id = db.Column(db.String(8), primary_key=True, default=lambda: secrets.token_urlsafe(6))
    filename = db.Column(db.String(255), nullable=False)  # Original filename
    file_path = db.Column(db.String(500), nullable=False)  # Storage path relative to uploads
    team_id = db.Column(db.String(8), db.ForeignKey('team.id'), nullable=False)
    uploaded_by = db.Column(db.String(8), db.ForeignKey('user.id'), nullable=False)
    upload_time = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # EXIF data
    capture_time = db.Column(db.DateTime, nullable=True)
    gps_lat = db.Column(db.Numeric(10, 7), nullable=True)
    gps_lng = db.Column(db.Numeric(10, 7), nullable=True)

    # File info
    file_size = db.Column(db.Integer, nullable=True)
    mime_type = db.Column(db.String(100), nullable=True)

    # Relationships
    team = db.relationship('Team', backref='images')
    uploader = db.relationship('User', backref='uploaded_images')

    def __repr__(self):
        return f'<Image {self.filename} by {self.uploader.name}>'


class NotificationLog(db.Model):
    __tablename__ = 'notification_log'

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    notification_type = db.Column(db.Enum(NotificationType), nullable=False)
    recipient_user_id = db.Column(db.String(8), db.ForeignKey('user.id'), nullable=False)
    related_team_id = db.Column(db.String(8), db.ForeignKey('team.id'), nullable=True)
    status = db.Column(db.Enum(NotificationStatus), nullable=False, default=NotificationStatus.PENDING)

    # Email details
    subject = db.Column(db.String(255), nullable=True)
    template_name = db.Column(db.String(100), nullable=True)
    email_id = db.Column(db.String(255), nullable=True)

    # Metadata for notification-specific data (JSON)
    notification_data = db.Column(db.Text, nullable=True)  # JSON string for flexible data

    # Timestamps
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    sent_at = db.Column(db.DateTime, nullable=True)
    failed_at = db.Column(db.DateTime, nullable=True)

    # Error details for failed notifications
    error_message = db.Column(db.Text, nullable=True)

    # Relationships
    recipient = db.relationship('User', backref='received_notifications')
    team = db.relationship('Team', backref='team_notifications')

    def __repr__(self):
        return f'<NotificationLog {self.notification_type.value} to {self.recipient.email}>'