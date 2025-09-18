import os
import shutil
import re
from datetime import datetime
from flask import Blueprint, request, render_template, jsonify, redirect, url_for
from flask_login import login_required, current_user
from app.models import db, Team, TeamMembership, TeamStatus, TeamFormat, TeamMembershipStatus
from app.utils import load_station_names, parse_hh_mm_to_seconds, parse_mm_ss_to_seconds
from app.config import Config
from app.permissions import user_self_or_admin_required

user = Blueprint('user', __name__)


@user.route('/create-team', methods=['GET', 'POST'])
def create_team():
    # Handle GET request (show form to all users, authenticated or not)
    if request.method == 'GET':
        from app.utils import get_registration_deadline_info
        deadline_info = get_registration_deadline_info()
        return render_template('create_team.html', user=current_user, deadline_info=deadline_info)

    # Handle POST request - require authentication for actual submission
    if not current_user.is_authenticated:
        return jsonify({'error': 'Please sign in to create a team', 'redirect_to_login': True}), 401

    try:
        registration_deadline = Config.REGISTRATION_CLOSES_AT
        if datetime.now(registration_deadline.tzinfo) > registration_deadline:
            return jsonify({'error': 'Team registration has closed. The deadline has passed.'}), 403
    except Exception as e:
        import logging
        logging.warning(f"Failed to parse registration deadline: {e}")

    # Check if user is already associated with any team (as captain or member)
    existing_captained_team = Team.query.filter_by(captain_id=current_user.id).first()
    existing_membership = TeamMembership.query.filter_by(user_id=current_user.id, status=TeamMembershipStatus.ACTIVE).first()

    if existing_captained_team and existing_captained_team.status not in [TeamStatus.WITHDRAWN, TeamStatus.CANCELLED]:
        return jsonify({'error': f'You have already created team "{existing_captained_team.name}"'}), 400

    if existing_membership and existing_membership.team.status not in [TeamStatus.WITHDRAWN, TeamStatus.CANCELLED]:
        return jsonify({'error': f'You are already a member of team "{existing_membership.team.name}"'}), 400

    # Handle POST request
    try:
        team_name = request.form.get('team_name', '').strip()
        format_type_str = request.form.get('format', '').strip()
        estimated_duration_str = request.form.get('estimated_duration', '').strip()
        comments = request.form.get('comments', '').strip()
        password = request.form.get('password', '').strip()
        previous_baton_serial = request.form.get('previous_baton_serial', '').strip()
        email_opt_in = request.form.get('email_opt_in') == 'true'

        # Validation
        if not team_name:
            return jsonify({'error': 'Team name is required'}), 400

        try:
            format_type = TeamFormat(format_type_str)
        except ValueError:
            return jsonify({'error': 'Format must be Solo or Team'}), 400

        if not estimated_duration_str:
            return jsonify({'error': 'Estimated duration is required'}), 400

        # Validate duration format (HH:MM)
        if not re.match(r'^\d{1,2}:\d{2}$', estimated_duration_str):
            return jsonify({'error': 'Duration must be in HH:MM format'}), 400

        estimated_duration_seconds = parse_hh_mm_to_seconds(estimated_duration_str)

        # Check if team name already exists
        existing_name = Team.query.filter_by(name=team_name).first()
        if existing_name:
            return jsonify({'error': 'Team name already exists'}), 400

        # Create new team (status defaults to 'pending')
        new_team = Team(
            name=team_name,
            format=format_type,
            estimated_duration_seconds=estimated_duration_seconds,
            comments=comments if comments else None,
            previous_baton_serial=previous_baton_serial if previous_baton_serial else None,
            status=TeamStatus.PENDING,
            captain_id=current_user.id
        )
        new_team.set_password(password)

        db.session.add(new_team)
        db.session.flush()  # Flush to get the team ID before creating membership

        # Always create TeamMembership for captain with standard defaults
        captain_membership = None
        if format_type == TeamFormat.SOLO:
            # Calculate planned pace for solo team based on estimated duration over 36 miles
            # Ensure to handle division by zero if estimated_duration_seconds could be 0
            planned_pace_seconds = 0
            if estimated_duration_seconds > 0:
                planned_pace_seconds = round(estimated_duration_seconds / 36)

            # Solo teams get reasonable defaults that can be edited later
            captain_membership = TeamMembership(
                user_id=current_user.id,
                team_id=new_team.id,
                willing_to_lead=True,
                preferred_miles=36,    # Full distance default
                planned_pace_seconds=planned_pace_seconds,
                preferred_station=None,
                comments=None
            )
            db.session.add(captain_membership)
            redirect_url = url_for('teams.team_members', team_id=new_team.id)
            message = 'Solo entry created successfully. You can edit your preferences anytime.'
        else:
            # Team format captains still need to set their preferences
            # No membership created here - they'll create it via join form
            redirect_url = url_for('user.join_team')
            message = 'Team created successfully. Now enter your preferences.'

        # Update email opt-in status for all team types
        current_user.email_opt_in = email_opt_in

        db.session.commit()

        # Send team creation confirmation email
        from app.utils import send_email_with_logging
        from app.models import NotificationType
        from app.utils import format_hh_mm_from_seconds

        subject = f"Team '{new_team.name}' Created"
        context = {
            'team': new_team,
            'estimated_duration_display': format_hh_mm_from_seconds(new_team.estimated_duration_seconds),
            'team_url': url_for('teams.team_members', team_id=new_team.id, _external=True)
        }
        metadata = {
            'team_name': new_team.name,
            'team_format': new_team.format.value,
            'estimated_duration_seconds': new_team.estimated_duration_seconds
        }

        send_email_with_logging(
            notification_type=NotificationType.TEAM_CREATION,
            recipient_user=current_user,
            subject=subject,
            template_name='team_created',
            template_context=context,
            related_team=new_team,
            metadata=metadata
        )

        team_folder_path = os.path.join(Config.UPLOAD_FOLDER, new_team.id)
        os.makedirs(team_folder_path, exist_ok=True)

        return jsonify({
            'success': True,
            'message': message,
            'team_id': new_team.id,
            'team_name': new_team.name,
            'redirect_url': redirect_url
        }), 201

    except Exception as e:
        db.session.rollback()
        import logging
        logging.error(f"Failed to create team: {str(e)}")
        return jsonify({'error': f'Failed to create team'}), 500


@user.route('/join-team', methods=['GET', 'POST'])
def join_team():
    stations = load_station_names()[1:]

    if request.method == 'GET':
        # Handle invite token if present in URL
        invite_token = request.args.get('invite_token')
        invited_team = None
        error_message = None

        if invite_token:
            invited_team = Team.query.filter_by(invite_token=invite_token).first()
            if not invited_team:
                error_message = "The invitation link is invalid or has expired. Please select a team from the list below."
            elif invited_team.status != TeamStatus.OPEN:
                error_message = f"The team '{invited_team.name}' is not currently accepting new members."
                invited_team = None  # Don't pre-fill if team is not open

        # Get all teams with "Team" format and "open" status (open for joining)
        # Exclude closed teams as they're not accepting new members
        open_teams = Team.query.filter_by(format=TeamFormat.TEAM, status=TeamStatus.OPEN).all()

        # For authenticated users, handle existing team logic
        if current_user.is_authenticated:
            existing_captained_team = Team.query.filter_by(captain_id=current_user.id).first()
            existing_membership = TeamMembership.query.filter_by(user_id=current_user.id, status=TeamMembershipStatus.ACTIVE).first()
            pending_captain_team = None

            # Handle case where a captain just created a team and needs to join
            if not invited_team and existing_captained_team and existing_captained_team.status == TeamStatus.PENDING and not existing_membership:
                pending_captain_team = existing_captained_team

            # Determine mode and setup template data
            if pending_captain_team:
                mode = 'join'
                existing_team = None
            elif existing_membership or existing_captained_team:
                mode = 'switch'  # User can switch teams or edit current preferences
                existing_team = existing_membership.team if existing_membership else existing_captained_team
            else:
                mode = 'join'  # Fresh user joining for first time
                existing_team = None
        else:
            # For unauthenticated users, show preview mode
            mode = 'preview'
            existing_team = None
            existing_membership = None
            pending_captain_team = None

        return render_template('participant_registration.html',
                             teams=open_teams,
                             stations=stations,
                             user=current_user,
                             mode=mode,
                             existing_team=existing_team,
                             existing_membership=existing_membership if current_user.is_authenticated else None,
                             pending_captain_team=pending_captain_team if current_user.is_authenticated else None,
                             invited_team=invited_team,
                             error_message=error_message)

    # Handle POST request - require authentication for actual submission
    if not current_user.is_authenticated:
        return jsonify({'error': 'Please sign in to join a team', 'redirect_to_login': True}), 401

    # Handle POST - delegate to shared handler
    return handle_team_registration_post(stations, mode='join')


@user.route('/my-registration', methods=['GET', 'POST'])
@login_required
def my_registration():
    """Handle editing existing team registration/preferences"""
    stations = load_station_names()[1:]

    # Check if user has existing membership or captained team
    existing_captained_team = Team.query.filter_by(captain_id=current_user.id).first()
    existing_membership = TeamMembership.query.filter_by(user_id=current_user.id).first()

    if not existing_membership and not existing_captained_team:
        # No existing registration, redirect to join team
        return redirect(url_for('user.join_team'))

    if request.method == 'GET':
        if existing_membership:
            # User has membership - show edit form
            team = existing_membership.team
            return render_template('participant_registration.html',
                                 teams=[],
                                 stations=stations,
                                 user=current_user,
                                 mode='edit',
                                 existing_team=team,
                                 existing_membership=existing_membership,
                                 pending_captain_team=None)
        elif existing_captained_team:
            # If it's a solo team, they are redirected to team_members page
            if existing_captained_team.format == TeamFormat.SOLO:
                return redirect(url_for('teams.team_members', team_id=existing_captained_team.id))
            else:
                # This is a 'Team' format captain who needs to fill out their preferences
                # The mode should be 'join' as they are effectively joining their own team
                # And pending_captain_team should be set to display the banner
                return render_template('participant_registration.html',
                                     teams=[existing_captained_team],
                                     stations=stations,
                                     user=current_user,
                                     mode='join',
                                     existing_team=None,
                                     existing_membership=None,
                                     pending_captain_team=existing_captained_team)

    # Handle POST - delegate to shared handler
    return handle_team_registration_post(stations, mode='edit')


def handle_team_registration_post(stations, mode='join'):
    """Shared handler for team registration POST requests"""
    try:
        team_id = request.form.get('team_id')
        willing_to_lead = request.form.get('willing_to_lead') == 'yes'
        preferred_miles = request.form.get('preferred_miles')
        planned_pace_str = request.form.get('planned_pace')
        preferred_station = request.form.get('preferred_station')
        comments = request.form.get('comments', '').strip()
        waiver_agreed = request.form.get('waiver_agreed') == 'on'
        team_password = request.form.get('team_password', '').strip()
        invite_token = request.form.get('invite_token')
        email_opt_in = request.form.get('email_opt_in') == 'true'

        # Check if user is already associated with any team (as captain or member)
        existing_captained_team = Team.query.filter_by(captain_id=current_user.id).first()
        existing_membership = TeamMembership.query.filter_by(user_id=current_user.id, status=TeamMembershipStatus.ACTIVE).first()

        # Validation
        team = None
        # Authorize joining the team
        if invite_token:
            team = Team.query.filter_by(invite_token=invite_token).first()
            if not team:
                return jsonify({'error': 'The invitation link is invalid or has expired.'}), 400
            if team_id and team_id != team.id:
                return jsonify({'error': 'Invitation and selected team do not match.'}), 400
            # Otherwise, they've got a valid token, so we'll let them join the team.
        else:
            if not team_id:
                 return jsonify({'error': 'Please select a team'}), 400
            team = db.session.get(Team, team_id)

        # Authorization logic: Check if user can join this specific team
        # Skip password check for edit mode when user is already a member
        is_editing_existing_membership = mode == 'edit' and existing_membership and existing_membership.team_id == team.id

        if not is_editing_existing_membership:
            if team.status == TeamStatus.OPEN:
                # For open teams, check password if required (unless user is the team captain)
                if team.password_hash and not invite_token and team.captain_id != current_user.id: # Invite token or team captain bypasses password
                    if not team_password:
                        return jsonify({'error': 'This team requires a password'}), 400
                    if not team.check_password(team_password):
                        return jsonify({'error': 'Incorrect team password'}), 400
            elif team.status == TeamStatus.PENDING:
                # For pending teams, only the captain can register
                if team.captain_id != current_user.id:
                    return jsonify({'error': 'This team is pending approval and not yet open for joining.'}), 403
            else: # 'withdrawn' or other statuses
                return jsonify({'error': f"Team '{team.name}' is not currently accepting new members."}), 403

        if team.format == TeamFormat.SOLO:
            return jsonify({'error': 'Solo teams cannot be joined.'}), 400

        # Handle team switching (user joining different team)
        is_switching_teams = existing_membership and existing_membership.team_id != team.id

        # Validation for preferred_miles (now Numeric) and planned_pace (now seconds)
        try:
            preferred_miles_numeric = float(preferred_miles) # Convert to float for validation, will be stored as Numeric
            if not (0.1 <= preferred_miles_numeric <= 36):
                return jsonify({'error': 'Preferred miles must be a number between 0.1 and 36'}), 400
        except ValueError:
            return jsonify({'error': 'Preferred miles must be a valid number'}), 400

        if not planned_pace_str or not re.match(r'^\d{1,2}:\d{2}$', planned_pace_str):
            return jsonify({'error': 'Planned pace must be in MM:SS format'}), 400

        planned_pace_seconds = parse_mm_ss_to_seconds(planned_pace_str)

        if not waiver_agreed:
            return jsonify({'error': 'You must agree to the waiver terms'}), 400

        # Update email opt-in status
        current_user.email_opt_in = email_opt_in

        # Handle different scenarios
        membership_to_log = None
        if is_switching_teams:
            # User is switching to a different team - remove old membership and create new one
            db.session.delete(existing_membership)
            membership = TeamMembership(
                user_id=current_user.id,
                team_id=team.id,
                willing_to_lead=willing_to_lead,
                preferred_miles=preferred_miles_numeric,
                planned_pace_seconds=planned_pace_seconds,
                preferred_station=preferred_station if preferred_station else None,
                comments=comments if comments else None
            )
            db.session.add(membership)
            membership_to_log = membership  # Log this as a new member
            message = f'Successfully switched to {team.name}'
        elif existing_membership:
            # Update existing membership
            existing_membership.willing_to_lead = willing_to_lead
            existing_membership.preferred_miles = preferred_miles_numeric
            existing_membership.planned_pace_seconds = planned_pace_seconds
            existing_membership.preferred_station = preferred_station if preferred_station else None
            existing_membership.comments = comments if comments else None
            message = f'Successfully updated preferences for {team.name}'
        elif existing_captained_team:
            # Create membership for captain (captains don't automatically have memberships)
            membership = TeamMembership(
                user_id=current_user.id,
                team_id=team.id,
                willing_to_lead=willing_to_lead,
                preferred_miles=preferred_miles_numeric,
                planned_pace_seconds=planned_pace_seconds,
                preferred_station=preferred_station if preferred_station else None,
                comments=comments if comments else None
            )
            db.session.add(membership)
            # Don't log captain joining their own team for digest
            message = f'Successfully added preferences for {team.name}'
        else:
            # Create new team membership
            membership = TeamMembership(
                user_id=current_user.id,
                team_id=team.id,
                willing_to_lead=willing_to_lead,
                preferred_miles=preferred_miles_numeric,
                planned_pace_seconds=planned_pace_seconds,
                preferred_station=preferred_station if preferred_station else None,
                comments=comments if comments else None
            )
            db.session.add(membership)
            # Log member join event for digest processing (only for new members, not captains)
            if team.captain_id != current_user.id:
                membership_to_log = membership
            message = f'Successfully joined {team.name}'

        # Commit all changes first
        db.session.commit()

        # Send member joined notification email (for new members only, not updates)
        if membership_to_log and not is_switching_teams:
            from app.utils import send_email_with_logging
            from app.models import NotificationType

            subject = f"Welcome to Team '{team.name}'!"
            context = {
                'team': team,
                'membership': membership_to_log,
                'team_url': url_for('teams.team_members', team_id=team.id, _external=True)
            }
            metadata = {
                'team_name': team.name,
                'member_name': current_user.name,
                'preferred_miles': float(membership_to_log.preferred_miles) if membership_to_log.preferred_miles else None,
                'willing_to_lead': membership_to_log.willing_to_lead
            }

            send_email_with_logging(
                notification_type=NotificationType.MEMBER_JOINED,
                recipient_user=current_user,
                subject=subject,
                template_name='member_joined',
                template_context=context,
                related_team=team,
                metadata=metadata
            )

        # Log member join event for digest processing (after commit so we have IDs)
        if membership_to_log:
            from app.utils import log_member_join_event
            log_member_join_event(team, membership_to_log)

        return jsonify({
            'success': True,
            'message': message,
            'team_name': team.name,
            'redirect_url': url_for('teams.team_members', team_id=team.id)
        }), 201

    except Exception as e:
        db.session.rollback()
        import logging
        logging.error(f"Failed to process team registration: {str(e)}")
        return jsonify({'error': f'Failed to process registration'}), 500

def _perform_user_deletion(user_to_delete):
    """Helper function to encapsulate user deletion logic."""
    try:
        # Check if user is a captain of any teams
        captained_teams = Team.query.filter_by(captain_id=user_to_delete.id).all()

        for team in captained_teams:
            # Count active team members (excluding captain)
            active_members = TeamMembership.query.filter_by(
                team_id=team.id,
                status=TeamMembershipStatus.ACTIVE
            ).filter(TeamMembership.user_id != user_to_delete.id).count()

            # If team has other active team members, cannot delete captain
            if active_members > 0:
                return False, f'Cannot delete user. They are the captain of team "{team.name}" which has other active members. Please transfer captaincy first.'

        # Delete user's team memberships
        TeamMembership.query.filter_by(user_id=user_to_delete.id).delete()

        # Delete any notification logs associated with this user
        from app.models import NotificationLog
        NotificationLog.query.filter_by(recipient_user_id=user_to_delete.id).delete()

        # Delete any teams where user was the only captain
        for team in captained_teams:
            # Delete all images associated with this team first
            from app.models import Image
            Image.query.filter_by(team_id=team.id).delete()

            # Delete any notification logs associated with this team
            NotificationLog.query.filter_by(related_team_id=team.id).delete()

            # Delete team folder if it exists
            team_folder = os.path.join(Config.UPLOAD_FOLDER, team.id)
            if os.path.exists(team_folder):
                try:
                    shutil.rmtree(team_folder)
                except OSError as e:
                    # Log error but don't fail the deletion
                    import logging
                    logging.warning(f"Failed to delete team folder {team_folder}: {e}")

            # Delete team from database
            db.session.delete(team)

        # Store user email for response message
        user_email = user_to_delete.email
        user_name = user_to_delete.name

        # Delete the user account
        db.session.delete(user_to_delete)
        db.session.commit()

        return True, f'User "{user_name}" ({user_email}) deleted successfully'

    except Exception as e:
        db.session.rollback()
        import logging
        logging.error(f"User deletion failed for {user_to_delete.email}: {str(e)}")

        # Provide user-friendly error messages
        error_msg = str(e).lower()
        if 'foreign key' in error_msg or 'constraint' in error_msg:
            return False, 'Unable to delete account due to data relationships. Please contact support for assistance.'
        elif 'permission' in error_msg or 'access' in error_msg:
            return False, 'Permission denied. You may not have sufficient privileges to delete this account.'
        elif 'disk' in error_msg or 'space' in error_msg:
            return False, 'Storage error occurred while deleting account. Please try again later.'
        else:
            return False, 'An unexpected error occurred while deleting the account. Please contact support if this persists.'


@user.route('/delete-account', methods=['POST'])
@login_required
def delete_account():
    """Endpoint to delete the current user's account"""
    from flask_login import logout_user
    success, message = _perform_user_deletion(current_user)
    if success:
        logout_user()
        return jsonify({'success': True, 'message': message}), 200
    else:
        return jsonify({'error': message}), 400


@user.route('/user/<user_id>', methods=['DELETE'])
@user_self_or_admin_required()
def delete_user(user_id, user):
    success, message = _perform_user_deletion(user)
    if success:
        return jsonify({'success': True, 'message': message}), 200
    else:
        return jsonify({'error': message}), 400


@user.route('/notification-preferences', methods=['PATCH'])
@login_required
def update_notification_preferences():
    """Update user's notification preferences"""
    try:
        data = request.get_json()

        if not data:
            return jsonify({'error': 'No data provided'}), 400

        # Update captain notifications preference
        if 'captain_notifications_enabled' in data:
            current_user.captain_notifications_enabled = bool(data['captain_notifications_enabled'])

        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Notification preferences updated successfully'
        }), 200

    except Exception as e:
        db.session.rollback()
        import logging
        logging.error(f"Failed to update notification preferences for user {current_user.id}: {str(e)}")
        return jsonify({'error': f'Failed to update preferences'}), 500