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

class TeamLines(enum.Enum):
    """Which Link line(s) a team runs.

    BOTH's display value is 'Interline' -- SQLAlchemy's Enum column stores
    the member *name* ('ONE'/'TWO'/'BOTH'), not this value, so this is a
    label-only rename and doesn't touch existing rows.
    """
    ONE = '1 Line'
    TWO = '2 Line'
    BOTH = 'Interline'

class TeamMembershipStatus(enum.Enum):
    ACTIVE = 'active'
    WITHDRAWN = 'withdrawn'
    REMOVED = 'removed'

class OAuthProvider(enum.Enum):
    GOOGLE = 'google'
    GITHUB = 'github'
    MICROSOFT = 'microsoft'

class UserRole(enum.Enum):
    """A user's privilege tier, PARTICIPANT < MANAGER < ADMIN. See
    PermissionChecker in app/permissions.py for what each can actually do --
    in short, MANAGER can run the event (approve/manage teams, roster, legs)
    event-wide but never sees a team's photos; ADMIN can do that plus every
    destructive/system-level action (hard delete, baton serials, email
    templates/bulk sends, photos)."""
    PARTICIPANT = 'participant'
    MANAGER = 'manager'
    ADMIN = 'admin'

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
    PENDING = 'pending'   # queued; eligible to send once scheduled_for passes
    SENT = 'sent'
    FAILED = 'failed'     # retries exhausted

class Team(db.Model):
    id = db.Column(db.String(8), primary_key=True, default=lambda: secrets.token_urlsafe(6))
    name = db.Column(db.String(255), unique=True, nullable=False)
    gallery_hash = db.Column(db.String(8), unique=True, nullable=False, default=lambda: secrets.token_urlsafe(6))  # Public gallery view hash
    format = db.Column(db.Enum(TeamFormat), nullable=False)
    lines = db.Column(db.Enum(TeamLines), nullable=False, default=TeamLines.ONE)
    estimated_duration_seconds = db.Column(db.Integer, nullable=False)  # Stored as total seconds
    comments = db.Column(db.Text, nullable=True)
    password_hash = db.Column(db.String(255), nullable=True)  # Optional password for joining
    invite_token = db.Column(db.String(32), unique=True, nullable=True) # Shareable, revocable invite token
    status = db.Column(db.Enum(TeamStatus), nullable=False, default=TeamStatus.PENDING)
    # An Interline team runs two branches concurrently, so it needs two batons.
    previous_baton_serial = db.Column(db.String(12), nullable=True)
    previous_baton_serial_2 = db.Column(db.String(12), nullable=True)
    baton_serial = db.Column(db.String(12), nullable=True)
    baton_serial_2 = db.Column(db.String(12), nullable=True)
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
    def batons_required(self):
        """Two for Interline (one per branch), otherwise one."""
        return 2 if self.lines == TeamLines.BOTH else 1

    @property
    def course_miles(self):
        """Total distance of the line(s) this team runs."""
        from app.utils import course_distance_miles, course_lines_for
        return course_distance_miles(course_lines_for(self.lines))

    def runs_line(self, line):
        """Whether this team runs ``line`` (a TeamLines member)."""
        return self.lines in (line, TeamLines.BOTH)

    @property
    def previous_baton_serials(self):
        """Serials of batons the team already owns from a previous year."""
        return [s for s in (self.previous_baton_serial, self.previous_baton_serial_2) if s]

    @property
    def baton_serials(self):
        """Serials of batons issued to the team."""
        return [s for s in (self.baton_serial, self.baton_serial_2) if s]

    @property
    def batons_to_purchase(self):
        """Batons still to be bought, after crediting any they bring."""
        return max(self.batons_required - len(self.previous_baton_serials), 0)

    @property
    def members(self):
        """Get all users who are members of this team"""
        return [membership.user for membership in self.memberships]

    def __repr__(self):
        return f'<Team {self.name}>'

class TeamMembership(db.Model):
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
    role = db.Column(db.Enum(UserRole), nullable=False, default=UserRole.PARTICIPANT)
    email_opt_in = db.Column(db.Boolean, default=False)
    captain_notifications_enabled = db.Column(db.Boolean, default=True)

    @property
    def teams(self):
        """Get all teams this user is a member of"""
        return [membership.team for membership in self.memberships]

    @property
    def is_admin(self):
        return self.role == UserRole.ADMIN

    @property
    def is_manager(self):
        """Manager-*or-above* -- true for admins too, since admin is a
        strict superset of manager privileges. See PermissionChecker in
        app/permissions.py for what each tier can actually do."""
        return self.role in (UserRole.MANAGER, UserRole.ADMIN)

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
    __table_args__ = (db.UniqueConstraint('team_id', 'file_hash', name='unique_team_filehash'),)
    id = db.Column(db.String(8), primary_key=True, default=lambda: secrets.token_urlsafe(6))
    filename = db.Column(db.String(255), nullable=False)  # Original filename
    file_hash = db.Column(db.String(64), nullable=False)  # SHA-256 hash of ORIGINAL file contents (not any converted, resized version)
    file_path = db.Column(db.String(500), nullable=False)  # Storage path relative to uploads
    team_id = db.Column(db.String(8), db.ForeignKey('team.id'), nullable=False)
    uploaded_by = db.Column(db.String(8), db.ForeignKey('user.id'), nullable=False)
    upload_time = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    associated_exchange_id = db.Column(db.String(4), nullable=True)
    manual_exchange_id = db.Column(db.String(4), nullable=True)

    # EXIF data
    capture_time = db.Column(db.DateTime, nullable=False)
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


class LegAssignment(db.Model):
    """A team member assigned to run one leg of the relay.

    Keyed by membership (not user) so that a member's withdrawal naturally
    flags their legs. Several members may share a leg and a member may be
    assigned multiple legs, but the same member cannot be assigned to the
    same leg twice. Legs may be left unassigned while drafting.

    A leg is identified by its exchange pair rather than a running index,
    because index is per-line: leg 20 is Roosevelt->Northgate on the 1 Line
    but Northgate->Pinehurst on the 2 Line. The pair is stable across lines,
    so a shared trunk leg is one leg no matter which line a team registered
    for, and an Interline team cannot double-assign it.
    """

    id = db.Column(db.String(8), primary_key=True, default=lambda: secrets.token_urlsafe(6))
    team_id = db.Column(db.String(8), db.ForeignKey('team.id'), nullable=False)
    membership_id = db.Column(db.String(8), db.ForeignKey('team_membership.id'), nullable=False)
    start_exchange = db.Column(db.Integer, nullable=False)
    end_exchange = db.Column(db.Integer, nullable=False)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    # Relationships
    team = db.relationship('Team', backref='leg_assignments')
    membership = db.relationship('TeamMembership', backref='leg_assignments')

    # A member may appear on a leg at most once (but legs may be shared)
    __table_args__ = (
        db.UniqueConstraint('team_id', 'start_exchange', 'end_exchange', 'membership_id',
                            name='unique_team_leg_membership'),
    )

    @property
    def leg_key(self):
        return (self.start_exchange, self.end_exchange)

    def __repr__(self):
        return f'<LegAssignment leg {self.start_exchange}->{self.end_exchange} of team {self.team_id}>'


class MembershipPreferenceOverride(db.Model):
    """A captain's adjustment of a member's stated join preferences.

    The member's own answers on :class:`TeamMembership` are their testimony
    about themselves and are never rewritten; this is a separate, attributed
    layer that the leg-assignment board (and only the board -- badges,
    metrics, and the solver) reads on top of them.

    ``overrides`` is a sparse JSON object keyed by preference field name:

    * key absent  -> use what the member stated
    * key present -> use this value instead
    * key present with a ``null`` value -> treat the member as having stated
      no preference at all (drop the constraint, rather than substituting a
      different one)

    That three-way distinction is why this is JSON rather than a row of
    nullable columns: a nullable column cannot tell "no override" apart from
    "override to no preference", and the second case is a real one (a stated
    end station that cannot be made to work with the rest of the board).

    ``stated_snapshot`` records what the member had stated for the overridden
    fields at the moment the override was written, so the board can flag an
    override that a later edit by the member has silently outvoted.
    """

    id = db.Column(db.String(8), primary_key=True, default=lambda: secrets.token_urlsafe(6))
    membership_id = db.Column(db.String(8), db.ForeignKey('team_membership.id'),
                              nullable=False, unique=True)
    overrides = db.Column(db.Text, nullable=False, default='{}')       # JSON object
    stated_snapshot = db.Column(db.Text, nullable=True)                # JSON object
    note = db.Column(db.Text, nullable=True)                           # captain-visible rationale
    created_by = db.Column(db.String(8), db.ForeignKey('user.id'), nullable=False)

    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    membership = db.relationship(
        'TeamMembership',
        backref=db.backref('preference_override', uselist=False,
                           cascade='all, delete-orphan'),
    )
    author = db.relationship('User', foreign_keys=[created_by])

    def __repr__(self):
        return f'<MembershipPreferenceOverride for membership {self.membership_id}>'


def _naive_utcnow():
    """Current UTC time as a naive datetime, for consistent SQLite comparisons."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class NotificationLog(db.Model):
    """Email outbox + audit log.

    Every notification is a row here. Web requests only ever insert PENDING rows
    (with the email already rendered into ``body_html``); a single background
    worker drains rows whose ``scheduled_for`` has passed and sends them, moving
    them to SENT or, after exhausting retries, FAILED. Terminal rows remain as
    the audit trail.
    """

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    notification_type = db.Column(db.Enum(NotificationType), nullable=False)
    recipient_user_id = db.Column(db.String(8), db.ForeignKey('user.id'), nullable=False)
    related_team_id = db.Column(db.String(8), db.ForeignKey('team.id'), nullable=True)
    status = db.Column(db.Enum(NotificationStatus), nullable=False, default=NotificationStatus.PENDING)

    # Email details
    subject = db.Column(db.String(255), nullable=True)
    template_name = db.Column(db.String(100), nullable=True)
    body_html = db.Column(db.Text, nullable=True)  # pre-rendered at enqueue time
    email_id = db.Column(db.String(255), nullable=True)

    # Metadata for notification-specific data (JSON)
    notification_data = db.Column(db.Text, nullable=True)  # JSON string for flexible data

    # Queue mechanics
    scheduled_for = db.Column(db.DateTime, default=_naive_utcnow, index=True)  # eligible-at
    attempts = db.Column(db.Integer, nullable=False, default=0)
    next_attempt_at = db.Column(db.DateTime, default=_naive_utcnow, index=True)  # backoff gate
    dedup_key = db.Column(db.String(255), nullable=True, index=True)  # idempotency / digest coalescing

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