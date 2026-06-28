"""Team membership domain service.

Owns membership lifecycle operations (withdraw / unwithdraw / remove / transfer
captaincy) and the team-registration flow that creates or updates a member's
preferences. No HTTP concerns: validation failures raise
:class:`~app.services.exceptions.ServiceError` (with an appropriate status),
which route handlers translate into JSON responses.

Notification side effects (emails, digest logging) stay in the route handlers;
these functions only own the persistence and business rules.
"""

import re
from dataclasses import dataclass
from typing import Optional

from app.models import db, Team, TeamMembership, User, TeamStatus, TeamFormat, TeamMembershipStatus
from app.services.exceptions import ServiceError
from app.utils import parse_mm_ss_to_seconds


# --- Membership lifecycle operations ---

def withdraw_membership(team, membership):
    """Withdraw a member from a team. Captains cannot withdraw themselves."""
    if membership.status == TeamMembershipStatus.WITHDRAWN:
        raise ServiceError('Membership is already withdrawn')
    if membership.user_id == team.captain_id:
        raise ServiceError('Captain cannot withdraw from their own team. Make someone else captain first.')

    membership.status = TeamMembershipStatus.WITHDRAWN
    db.session.commit()
    return membership


def unwithdraw_membership(membership):
    """Restore a withdrawn membership to active."""
    if membership.status != TeamMembershipStatus.WITHDRAWN:
        raise ServiceError('Membership is not withdrawn')

    membership.status = TeamMembershipStatus.ACTIVE
    db.session.commit()
    return membership


def remove_member(team, user_id):
    """Remove a member (by user id) from a team. Captains cannot be removed."""
    membership = TeamMembership.query.filter_by(team_id=team.id, user_id=user_id).first()
    if not membership:
        raise ServiceError('Membership not found', status=404)
    if membership.user_id == team.captain_id:
        raise ServiceError('Cannot remove team captain')

    membership.status = TeamMembershipStatus.REMOVED
    db.session.commit()
    return membership


def transfer_captain(team, user_id):
    """Transfer captaincy to an active member of the team.

    Returns ``(previous_captain, new_captain)`` so the caller can notify them.
    """
    new_captain = User.query.get(user_id)
    if not new_captain:
        raise ServiceError('New captain not found', status=404)

    membership = TeamMembership.query.filter_by(
        team_id=team.id,
        user_id=user_id,
        status=TeamMembershipStatus.ACTIVE
    ).first()
    if not membership:
        raise ServiceError('New captain must be an active member of the team')

    previous_captain = team.captain
    team.captain_id = user_id
    db.session.commit()
    return previous_captain, new_captain


# --- Registration flow ---

@dataclass
class RegistrationInput:
    """Parsed registration form fields."""
    team_id: Optional[str]
    invite_token: Optional[str]
    willing_to_lead: bool
    preferred_miles: Optional[str]
    planned_pace_str: Optional[str]
    preferred_station: Optional[str]
    comments: str
    waiver_agreed: bool
    team_password: str
    email_opt_in: bool


@dataclass
class RegistrationResult:
    """Outcome of a registration, for the caller to act on."""
    team: Team
    message: str
    # The membership that should trigger notifications, or None if no notify.
    membership_to_log: Optional[TeamMembership]
    # True when the user moved from one team to another (suppresses welcome email).
    is_switching: bool


def _resolve_team(data: RegistrationInput):
    """Resolve the target team from an invite token or an explicit team id."""
    if data.invite_token:
        team = Team.query.filter_by(invite_token=data.invite_token).first()
        if not team:
            raise ServiceError('The invitation link is invalid or has expired.')
        if data.team_id and data.team_id != team.id:
            raise ServiceError('Invitation and selected team do not match.')
        return team

    if not data.team_id:
        raise ServiceError('Please select a team')
    return db.session.get(Team, data.team_id)


def _authorize_join(user, team, data: RegistrationInput, is_editing_existing_membership):
    """Enforce the rules for whether ``user`` may register for ``team``."""
    if is_editing_existing_membership:
        return

    if team.status == TeamStatus.OPEN:
        # Open teams may require a password (captains and invite tokens bypass).
        if team.password_hash and not data.invite_token and team.captain_id != user.id:
            if not data.team_password:
                raise ServiceError('This team requires a password')
            if not team.check_password(data.team_password):
                raise ServiceError('Incorrect team password')
    elif team.status == TeamStatus.PENDING:
        # Only the captain may register for a team still pending approval.
        if team.captain_id != user.id:
            raise ServiceError('This team is pending approval and not yet open for joining.', status=403)
    else:  # withdrawn, closed, cancelled, etc.
        raise ServiceError(f"Team '{team.name}' is not currently accepting new members.", status=403)


def _validate_preferences(data: RegistrationInput):
    """Validate and normalize preferred miles / pace / waiver. Returns (miles, pace_seconds)."""
    try:
        preferred_miles_numeric = float(data.preferred_miles)
        if not (0.1 <= preferred_miles_numeric <= 36):
            raise ServiceError('Preferred miles must be a number between 0.1 and 36')
    except (TypeError, ValueError):
        raise ServiceError('Preferred miles must be a valid number')

    if not data.planned_pace_str or not re.match(r'^\d{1,2}:\d{2}$', data.planned_pace_str):
        raise ServiceError('Planned pace must be in MM:SS format')
    planned_pace_seconds = parse_mm_ss_to_seconds(data.planned_pace_str)

    if not data.waiver_agreed:
        raise ServiceError('You must agree to the waiver terms')

    return preferred_miles_numeric, planned_pace_seconds


def register(user, data: RegistrationInput, mode='join') -> RegistrationResult:
    """Create or update ``user``'s membership/preferences for a team.

    Mirrors the original ``handle_team_registration_post`` logic: resolve the
    target team, authorize, validate, then apply one of four outcomes (switch
    teams, update existing, captain joining own team, or fresh join).
    """
    # Existing associations for this user.
    existing_captained_team = Team.query.filter_by(captain_id=user.id).first()
    existing_membership = TeamMembership.query.filter_by(
        user_id=user.id, status=TeamMembershipStatus.ACTIVE
    ).first()

    team = _resolve_team(data)

    is_editing_existing_membership = (
        mode == 'edit' and existing_membership and existing_membership.team_id == team.id
    )
    _authorize_join(user, team, data, is_editing_existing_membership)

    if team.format == TeamFormat.SOLO:
        raise ServiceError('Solo teams cannot be joined.')

    is_switching_teams = existing_membership and existing_membership.team_id != team.id

    preferred_miles_numeric, planned_pace_seconds = _validate_preferences(data)

    # Update email opt-in for all paths.
    user.email_opt_in = data.email_opt_in

    preferred_station = data.preferred_station if data.preferred_station else None
    comments = data.comments if data.comments else None

    membership_to_log = None
    if is_switching_teams:
        # Switch to a different team: drop old membership, create new one.
        db.session.delete(existing_membership)
        membership = TeamMembership(
            user_id=user.id, team_id=team.id,
            willing_to_lead=data.willing_to_lead,
            preferred_miles=preferred_miles_numeric,
            planned_pace_seconds=planned_pace_seconds,
            preferred_station=preferred_station, comments=comments,
        )
        db.session.add(membership)
        membership_to_log = membership
        message = f'Successfully switched to {team.name}'
    elif existing_membership:
        # Update existing membership in place.
        existing_membership.willing_to_lead = data.willing_to_lead
        existing_membership.preferred_miles = preferred_miles_numeric
        existing_membership.planned_pace_seconds = planned_pace_seconds
        existing_membership.preferred_station = preferred_station
        existing_membership.comments = comments
        message = f'Successfully updated preferences for {team.name}'
    elif existing_captained_team:
        # Captain creating their own membership (captains have none by default).
        membership = TeamMembership(
            user_id=user.id, team_id=team.id,
            willing_to_lead=data.willing_to_lead,
            preferred_miles=preferred_miles_numeric,
            planned_pace_seconds=planned_pace_seconds,
            preferred_station=preferred_station, comments=comments,
        )
        db.session.add(membership)
        # Don't log a captain joining their own team for the digest.
        message = f'Successfully added preferences for {team.name}'
    else:
        # Fresh join.
        membership = TeamMembership(
            user_id=user.id, team_id=team.id,
            willing_to_lead=data.willing_to_lead,
            preferred_miles=preferred_miles_numeric,
            planned_pace_seconds=planned_pace_seconds,
            preferred_station=preferred_station, comments=comments,
        )
        db.session.add(membership)
        if team.captain_id != user.id:
            membership_to_log = membership
        message = f'Successfully joined {team.name}'

    db.session.commit()

    return RegistrationResult(
        team=team,
        message=message,
        membership_to_log=membership_to_log,
        is_switching=bool(is_switching_teams),
    )
