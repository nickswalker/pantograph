"""
Simple permission system for the Relay Photo Collector app.
Provides decorators for common permission patterns.
"""

from functools import wraps
from flask import abort, request
from flask_login import current_user, login_required
from app.config import Config
from app.models import Team, TeamMembership, User, db, TeamStatus, TeamMembershipStatus


def legs_feature_required(f):
    """Gate a leg-assignment-board route behind the LEGS_ENABLED deployment
    flag. 404s rather than 403s -- while unfinished, the feature should look
    entirely absent, not merely forbidden. Put this outermost (directly under
    @route) so it short-circuits before any auth/permission checks run."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not Config.LEGS_ENABLED:
            abort(404)
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Require admin privileges"""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


def manager_or_admin_required(f):
    """Require manager or admin privileges (User.is_manager is true for
    admins too -- see its docstring). For the scoped bits of the admin
    surface managers get (the dashboard, approving teams) -- anything more
    sensitive (hard-delete, baton serials, email templates/bulk sends,
    photos) stays behind plain admin_required."""
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_manager:
            abort(403)
        return f(*args, **kwargs)
    return decorated_function


def team_access_required(param_name='team_id'):
    """Require access to a team (member, captain, or admin).

    Deliberately does NOT admit managers -- this gates the gallery among
    other things, and a manager can manage a team without ever seeing its
    photos. Use team_management_access_required for member/legs-board pages
    a manager should reach despite not being on the roster.
    """
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            team_id = kwargs.get(param_name) or request.view_args.get(param_name)
            if not team_id:
                abort(400, description="Team ID required")

            team = Team.query.filter_by(id=team_id).first()
            if not team:
                abort(404, description="Team not found")

            if not PermissionChecker.can_access_team(current_user, team):
                abort(403, description="Access denied to team")

            # Add team to kwargs for convenience
            kwargs['team'] = team
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def team_management_access_required(param_name='team_id'):
    """Like team_access_required, but also admits managers -- for pages
    (member roster, legs board) a manager can view/run without being a
    member of the team. Gallery/photo routes must keep using
    team_access_required instead."""
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            team_id = kwargs.get(param_name) or request.view_args.get(param_name)
            if not team_id:
                abort(400, description="Team ID required")

            team = Team.query.filter_by(id=team_id).first()
            if not team:
                abort(404, description="Team not found")

            if not PermissionChecker.can_view_team_management(current_user, team):
                abort(403, description="Access denied to team")

            kwargs['team'] = team
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def team_captain_required(param_name='team_id'):
    """Require team captain privileges (captain or admin)"""
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            team_id = kwargs.get(param_name) or request.view_args.get(param_name)
            if not team_id:
                abort(400, description="Team ID required")

            team = Team.query.filter_by(id=team_id).first()
            if not team:
                abort(404, description="Team not found")

            if not PermissionChecker.can_manage_team(current_user, team):
                abort(403, description="Captain privileges required")

            # Add team to kwargs for convenience
            kwargs['team'] = team
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def team_captain_or_member_required(param_name='team_id'):
    """Require team captain or membership owner privileges"""
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            team_id = kwargs.get(param_name) or request.view_args.get(param_name)
            membership_id = kwargs.get('membership_id') or request.view_args.get('membership_id')
            user_id = kwargs.get('user_id') or request.view_args.get('user_id')

            if not team_id:
                abort(400, description="Team ID required")

            team = Team.query.filter_by(id=team_id).first()
            if not team:
                abort(404, description="Team not found")

            # If membership_id is provided, always fetch and validate it exists
            membership = None
            if membership_id:
                membership = TeamMembership.query.filter_by(id=membership_id).first()
                if not membership:
                    abort(404, description="Membership not found")
            elif user_id:
                # If user_id is provided, fetch membership for that user
                membership = TeamMembership.query.filter_by(user_id=user_id, team_id=team.id).first()
                if not membership:
                    abort(404, description="Membership not found for this user")

            # Check if user is admin or team captain
            if PermissionChecker.can_manage_team(current_user, team):
                kwargs['team'] = team
                if membership:
                    kwargs['membership'] = membership
                return f(*args, **kwargs)

            # If membership_id is provided, check if user owns the membership
            if membership and membership.user_id == current_user.id:
                kwargs['team'] = team
                kwargs['membership'] = membership
                return f(*args, **kwargs)

            abort(403, description="Captain or membership owner privileges required")
        return decorated_function
    return decorator


def user_self_or_admin_required(param_name='user_id'):
    """Require user to be acting on their own account or be admin"""
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            user_id = kwargs.get(param_name) or request.view_args.get(param_name)
            if not user_id:
                abort(400, description="User ID required")

            user = User.query.filter_by(id=user_id).first()
            if not user:
                abort(404, description="User not found")

            if not (current_user.is_admin or current_user.id == user_id):
                abort(403, description="Can only manage your own account")

            # Add user to kwargs for convenience
            kwargs['user'] = user
            return f(*args, **kwargs)
        return decorated_function
    return decorator


def team_upload_allowed(param_name='team_id'):
    """Check if team allows uploads (status and membership); admins bypass both,
    so they can always fix up any team's photos and station assignments."""
    def decorator(f):
        @wraps(f)
        @login_required
        def decorated_function(*args, **kwargs):
            team_id = kwargs.get(param_name) or request.view_args.get(param_name)
            if not team_id:
                abort(400, description="Team ID required")

            team = Team.query.filter_by(id=team_id).first()
            if not team:
                abort(404, description="Team not found")

            if current_user.is_admin:
                kwargs['team'] = team
                return f(*args, **kwargs)

            # Check if team status allows uploads
            if not PermissionChecker.team_allows_uploads(team):
                abort(403, description=f"Photo uploads are not allowed for a team with '{team.status}' status")

            # Check if current user has access to this team
            if not PermissionChecker.can_access_team(current_user, team):
                abort(403, description="You do not have permission to upload photos for this team")

            # Check if current user is removed from this team (captains can't be removed)
            if current_user.id != team.captain_id:
                membership = TeamMembership.query.filter_by(user_id=current_user.id, team_id=team.id).first()
                if not membership or membership.status in [TeamMembershipStatus.REMOVED, TeamMembershipStatus.WITHDRAWN]:
                    abort(403, description="You cannot upload photos because you have been removed from this team or have withdrawn")

            kwargs['team'] = team
            return f(*args, **kwargs)
        return decorated_function
    return decorator


# Permission checker functions (single source of truth for team permission
# predicates, used by the decorators above, route handlers, and templates).
class PermissionChecker:
    """Helper class for checking permissions in templates or business logic"""

    @staticmethod
    def can_access_team(user, team):
        """Check if user can access team (admin, captain, or non-removed member)"""
        if not user or not user.is_authenticated:
            return False

        if user.is_admin or team.captain_id == user.id:
            return True

        membership = TeamMembership.query.filter_by(user_id=user.id, team_id=team.id).first()
        return membership is not None and membership.status != TeamMembershipStatus.REMOVED

    @staticmethod
    def can_view_team_management(user, team):
        """Check if user can view a team's roster/legs-board even without
        being on it (admin, manager, captain, or non-removed member).

        Managers get this but NOT can_access_team's gallery use -- they can
        run a team's roster and leg board without ever seeing its photos.
        """
        if user and user.is_authenticated and user.is_manager:
            return True
        return PermissionChecker.can_access_team(user, team)

    @staticmethod
    def can_manage_team(user, team):
        """Check if user can manage team (captain, manager, or admin --
        is_manager is true for admins too)"""
        if not user or not user.is_authenticated:
            return False
        return user.is_manager or team.captain_id == user.id

    @staticmethod
    def team_allows_uploads(team):
        """Check if the team's status permits photo uploads (user-independent)"""
        return team.status not in [TeamStatus.PENDING, TeamStatus.WITHDRAWN, TeamStatus.CANCELLED]

    @staticmethod
    def can_upload_to_team(user, team):
        """Check if user can upload photos to team (admins can always fix up any team's photos)"""
        if not user or not user.is_authenticated:
            return False

        if user.is_admin:
            return True

        if not PermissionChecker.team_allows_uploads(team):
            return False

        if not PermissionChecker.can_access_team(user, team):
            return False

        # Check if user is removed from team (captains can't be removed)
        if user.id != team.captain_id:
            membership = TeamMembership.query.filter_by(user_id=user.id, team_id=team.id).first()
            if not membership or membership.status in [TeamMembershipStatus.REMOVED, TeamMembershipStatus.WITHDRAWN]:
                return False

        return True

    @staticmethod
    def can_manage_membership(user, membership):
        """Check if user can manage a specific membership"""
        if not user or not user.is_authenticated:
            return False

        if user.is_manager:  # true for admins too
            return True

        # Team captain can manage memberships
        if membership.team.captain_id == user.id:
            return True

        # User can manage their own membership
        if membership.user_id == user.id:
            return True

        return False


# Make permission checker available in templates
def register_permissions(app):
    """Register permission checker with Flask app for use in templates"""
    app.jinja_env.globals['permissions'] = PermissionChecker