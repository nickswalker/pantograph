import os
import shutil
from flask import Blueprint, render_template, jsonify, url_for, request
from flask_login import current_user
from app.models import db, User, Team, TeamMembership, TeamMembershipStatus, Image
from app.permissions import admin_required
from app.utils import is_allowed_image, format_mm_ss_from_seconds, get_registration_deadline_info
from app.config import Config

admin = Blueprint('admin', __name__, url_prefix='/admin')


@admin.route('/')
@admin_required
def admin_dashboard():
    # Get all teams from database
    db_teams = Team.query.all()

    teams = []
    for team in db_teams:
        # Count images from database
        image_count = Image.query.filter_by(team_id=team.id).count()

        teams.append({
            'name': team.name,
            'id': team.id,
            'url': url_for('teams.gallery', team_id=team.id),
            'image_count': image_count,
            'format': team.format,
            'estimated_duration_seconds': team.estimated_duration_seconds,
            'captain': team.captain,
            'created_at': team.created_at,
            'member_count': len([m for m in team.memberships if m.status == TeamMembershipStatus.ACTIVE]),
            'status': team.status,
            'comments': team.comments,
            'baton_serial': team.baton_serial,
            'previous_baton_serial': team.previous_baton_serial,
            'has_password': team.has_password
        })

    # Get all users from database
    db_users = User.query.all()

    users = []
    for user in db_users:
        # Get active memberships for this user
        active_memberships = [m for m in user.memberships if m.status == TeamMembershipStatus.ACTIVE]

        users.append({
            'id': user.id,
            'name': user.name,
            'email': user.email,
            'provider': user.provider,
            'avatar_url': user.avatar_url,
            'is_admin': user.is_admin,
            'created_at': user.created_at,
            'active_memberships': active_memberships
        })

    # Get captain emails for the Email Captains button
    captain_emails = []
    for team in db_teams:
        if team.captain and team.captain.email:
            captain_emails.append(team.captain.email)

    # Remove duplicates (in case someone captains multiple teams)
    captain_emails = list(set(captain_emails))

    return render_template('admin.html', teams=teams, users=users, user=current_user, captain_emails=captain_emails)


@admin.route('/team/<team_id>', methods=['DELETE'])
@admin_required
def delete_team(team_id):
    try:
        from app.utils import find_team_by_id
        # Find team in database by team_id
        team = find_team_by_id(team_id)

        if not team:
            return jsonify({'error': 'Team not found'}), 404

        # Delete team memberships first (due to foreign key constraints)
        if team:
            TeamMembership.query.filter_by(team_id=team.id).delete()
            db.session.delete(team)
            db.session.commit()

        # Remove the team folder and all its contents
        team_path = os.path.join(Config.UPLOAD_FOLDER, team.id)
        if os.path.exists(team_path):
            shutil.rmtree(team_path)

        return jsonify({
            'success': True,
            'message': f'Team "{team.name}" deleted successfully'
        }), 200

    except Exception as e:
        db.session.rollback()
        # Log the error for debugging
        import logging
        logging.error(f"Failed to delete team {team_id}: {str(e)}")
        return jsonify({'error': f'Failed to delete team'}), 500





@admin.route('/team/<team_id>/approve', methods=['POST'])
@admin_required
def approve_team(team_id):
    try:
        from app.utils import find_team_by_id
        from app.services import team_service
        from app.services.team_service import TeamStateError
        # Find team in database by team_id
        team = find_team_by_id(team_id)

        if not team:
            return jsonify({'error': 'Team not found'}), 404

        try:
            # Solo teams go to 'closed', Team goes to 'open'
            team_service.approve_team(team)
        except TeamStateError as e:
            return jsonify({'error': e.message}), 400

        # Send approval email to team captain with logging
        from app.utils import send_email_with_logging
        from app.models import NotificationType

        subject = f"Team '{team.name}' Registration Approved"
        context = {'team': team}
        metadata = {'team_name': team.name, 'team_format': team.format.value}

        send_email_with_logging(
            notification_type=NotificationType.TEAM_APPROVAL,
            recipient_user=team.captain,
            subject=subject,
            template_name='team_approved',
            template_context=context,
            related_team=team,
            metadata=metadata
        )

        return jsonify({
            'success': True,
            'message': f'Team "{team.name}" approved successfully'
        }), 200

    except Exception as e:
        db.session.rollback()
        import logging
        logging.error(f"Failed to approve team {team_id}: {str(e)}")
        return jsonify({'error': f'Failed to approve team'}), 500


@admin.route('/team/<team_id>/baton_serial', methods=['POST'])
@admin_required
def update_baton_serial(team_id):
    try:
        from app.utils import find_team_by_id
        team = find_team_by_id(team_id)
        if not team:
            return jsonify({'error': 'Team not found'}), 404

        data = request.get_json()
        new_serial = data.get('baton_serial')

        team.baton_serial = new_serial
        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'Baton serial for team "{team.name}" updated successfully'
        }), 200

    except Exception as e:
        db.session.rollback()
        import logging
        logging.error(f"Failed to update baton serial for team {team_id}: {str(e)}")
        return jsonify({'error': f'Failed to update baton serial'}), 500


@admin.route('/email-templates')
@admin_required
def email_templates():
    """Admin interface to browse and preview email templates"""
    from app.email_preview import get_available_templates
    templates = get_available_templates()
    return render_template('admin_email_templates.html', templates=templates)


@admin.route('/email-templates/<template_name>')
@admin_required
def raw_email_template(template_name):
    """Return raw HTML of email template with sample data (for iframe)"""
    from app.email_preview import get_sample_data_for_template

    try:
        # Get sample data for this template
        sample_data = get_sample_data_for_template(template_name)

        # Render just the email template HTML
        return render_template(f'emails/{template_name}.html', **sample_data)

    except Exception as e:
        import logging
        logging.error(f"Error rendering template '{template_name}': {str(e)}")
        return f"Error rendering template '{template_name}'", 500


@admin.route('/email-templates/<template_name>/send-test', methods=['POST'])
@admin_required
def send_test_email(template_name):
    """Send a test email with sample data to the current admin user"""
    try:
        from app.email_preview import get_sample_data_for_template, get_sample_subject_for_template
        from app.utils import send_email_with_logging
        from app.models import NotificationType

        # Get sample data for this template
        sample_data = get_sample_data_for_template(template_name)
        subject = f"[TEST] {get_sample_subject_for_template(template_name)}"

        # Send test email to current admin user
        send_email_with_logging(
            notification_type=NotificationType.EVENT_UPDATE,  # Use a generic type for test emails
            recipient_user=current_user,
            subject=subject,
            template_name=template_name,
            template_context=sample_data,
            related_team=None,
            metadata={'test_email': True, 'template_name': template_name}
        )

        return jsonify({
            'success': True,
            'message': f'Test email sent to {current_user.email}'
        }), 200

    except Exception as e:
        import logging
        logging.error(f"Failed to send test email for template '{template_name}': {str(e)}")
        return jsonify({'error': f'Failed to send test email: {str(e)}'}), 500


@admin.route('/delete-all-images', methods=['POST'])
@admin_required
def delete_all_images():
    try:
        # Get all images from all teams
        all_images = Image.query.all()
        deleted_count = 0

        for image in all_images:
            # Delete the physical file(s)
            file_path = os.path.join(Config.UPLOAD_FOLDER, image.file_path)
            if os.path.exists(file_path):
                os.remove(file_path)

            # Delete any other files with the same base filename (e.g., HEIC originals)
            base_filename = os.path.splitext(file_path)[0]
            import glob
            matching_files = glob.glob(f"{base_filename}.*")
            for matching_file in matching_files:
                if matching_file != file_path and os.path.exists(matching_file):
                    os.remove(matching_file)

            # Delete the database record
            db.session.delete(image)
            deleted_count += 1

        # Commit all deletions
        db.session.commit()

        return jsonify({
            "success": True,
            "message": f"Successfully deleted {deleted_count} images from all teams"
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({
            "success": False,
            "error": f"Failed to delete images: {str(e)}"
        }), 500

@admin.route('/send-registration-reminder', methods=['POST'])
@admin_required
def send_registration_reminder():
    """Send registration reminder emails to all users without active team memberships"""
    try:
        # Find users without active team memberships
        # This includes users who have never joined a team or whose teams are withdrawn/cancelled
        users_without_teams = User.query.outerjoin(TeamMembership).filter(
            (TeamMembership.user_id.is_(None)) |  # No memberships at all
            (~TeamMembership.status.in_([TeamMembershipStatus.ACTIVE]))  # No active memberships
        ).all()

        # Filter to ensure we only get users who truly have no active memberships
        users_to_remind = []
        for user in users_without_teams:
            active_memberships = [m for m in user.memberships if m.status == TeamMembershipStatus.ACTIVE]
            if not active_memberships:
                users_to_remind.append(user)

        if not users_to_remind:
            return jsonify({
                'success': True,
                'message': 'No users found without active team memberships'
            }), 200

        # Send registration reminder emails
        from app.utils import send_email_with_logging
        from app.models import NotificationType
        from app.config import Config

        sent_count = 0
        for user in users_to_remind:
            try:
                subject = "Registration is Closing Soon"
                context = {
                    'event_name': Config.EVENT_NAME,
                    'registration_close_date': get_registration_deadline_info()['deadline_str']
                }
                metadata = {
                    'user_name': user.name,
                    'user_email': user.email
                }

                send_email_with_logging(
                    notification_type=NotificationType.REGISTRATION_REMINDER,
                    recipient_user=user,
                    subject=subject,
                    template_name='registration_reminder',
                    template_context=context,
                    related_team=None,
                    metadata=metadata
                )
                sent_count += 1

            except Exception as e:
                import logging
                logging.error(f"Failed to send registration reminder to {user.email}: {str(e)}")
                # Continue with other users even if one fails

        return jsonify({
            'success': True,
            'message': f'Registration reminder emails sent to {sent_count} users'
        }), 200

    except Exception as e:
        import logging
        logging.error(f"Failed to send registration reminders: {str(e)}")
        return jsonify({'error': f'Failed to send registration reminders'}), 500