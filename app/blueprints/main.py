import datetime

from flask import Blueprint, render_template, current_app, send_from_directory, jsonify, make_response
from markupsafe import Markup
import os

from app import Config
from app.models import TeamMembershipStatus

main = Blueprint('main', __name__)


@main.route('/')
def index():
    from app.utils import get_registration_deadline_info
    deadline_info = get_registration_deadline_info()
    return render_template('index.html', deadline_info=deadline_info)


@main.route('/payment')
def payment():
    return render_template('payment.html', contact_email=current_app.config['CONTACT_EMAIL'])


@main.route('/privacy')
def privacy_policy():
    """Renders the privacy policy page."""
    return render_template('privacy.html')


@main.route('/terms')
def terms_of_service():
    """Renders the terms of service page."""
    return render_template('terms.html',
                         contact_email=current_app.config.get('CONTACT_EMAIL'))


@main.route('/stats')
def global_stats():
    """Returns global event statistics (unauthenticated endpoint)"""
    from app.models import Team, TeamMembership, TeamMembershipStatus, User

    # Count total teams that are open full or closed
    team_count = Team.query.filter(Team.status.in_([
        'OPEN', 'FULL', 'CLOSED'
    ])).count()

    # Count total active team memberships
    membership_count = TeamMembership.query.filter_by(
        status=TeamMembershipStatus.ACTIVE
    ).count()

    # Count total users
    user_count = User.query.count()

    response = make_response(jsonify({
        'teams': team_count,
        'memberships': membership_count,
        'users': user_count
    }))

    # Add CORS headers
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'

    return response

@main.route('/results')
def results():
    """
    API endpoint to get times for each exchange based on uploaded images.
    :return:
    """
    # For each team, get the images, grouped by associated exchange
    from app.models import Team, Image
    from collections import defaultdict
    teams = Team.query.all()
    results = []
    for team in teams:
        format = team.format.value
        team_size = len([m for m in team.memberships if m.status == TeamMembershipStatus.ACTIVE])
        if team_size == 6:
            format = 'Competitive'
        team_data = {
            'teamName': team.name,
            "category": format,
            "teamSize": team_size,
            'exchangeTimes': {}
        }
        images = Image.query.filter_by(team_id=team.id).order_by(Image.capture_time).all()
        for img in images:
            if img.associated_exchange_id is not None:
                img_exchange_id = img.associated_exchange_id if img.associated_exchange_id else img.manual_exchange_id
                if img_exchange_id in team_data['exchangeTimes']:
                    # Check if this image is later than the last one for this exchange. We take the latest image
                    last_img = team_data['exchanges'][img_exchange_id]
                    if img.capture_time <= last_img['capture_time']:
                        continue
                team_data['exchangeTimes'][img_exchange_id] = img.capture_time
        for exchange_id in team_data['exchangeTimes']:
            team_data['exchangeTimes'][exchange_id] -= Config.EVENT_START_TIME.astimezone(datetime.UTC).replace(tzinfo=None)
            team_data['exchangeTimes'][exchange_id] = int(team_data['exchangeTimes'][exchange_id].total_seconds())
        results.append(team_data)
    return jsonify(results)

@main.route('/.well-known/microsoft-identity-association.json')
def microsoft_identity_association():
    """Serves Microsoft identity association file for OAuth verification"""
    data_dir = os.path.join(current_app.root_path, '..', 'data')
    return send_from_directory(data_dir, 'microsoft-identity-assocation.json',
                             mimetype='application/json')


@main.app_context_processor
def inject_svg():
    """Inject SVG helper function for email templates"""
    def get_svg(filename):
        try:
            svg_path = os.path.join(current_app.static_folder, filename)
            with open(svg_path, 'r') as f:
                content = f.read()
                # Strip the XML declaration and add email-friendly styling
                content = content.replace('<?xml version="1.0" encoding="UTF-8"?>', '')
                content = content.replace('<svg', '<svg width="16" height="16" style="vertical-align: middle; margin-right: 4px;" aria-hidden="true"')
                return Markup(content)
        except FileNotFoundError:
            return ''
    return dict(get_svg=get_svg)