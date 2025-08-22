from flask import Blueprint, render_template, current_app, send_from_directory, jsonify, make_response
from markupsafe import Markup
import os

main = Blueprint('main', __name__)


@main.route('/')
def index():
    return render_template('index.html')


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
    
    # Count total teams
    team_count = Team.query.count()
    
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