import datetime
import hashlib
import time

from flask import Blueprint, render_template, current_app, send_from_directory, jsonify, make_response, request, app
from markupsafe import Markup
import os

from app import Config
from app.models import TeamMembershipStatus, TeamFormat, TeamStatus, TeamLines

main = Blueprint('main', __name__)


@main.route('/')
def index():
    from app.utils import get_registration_deadline_info
    deadline_info = get_registration_deadline_info()
    return render_template('index.html', deadline_info=deadline_info)


@main.route('/payment')
def payment():
    return render_template('payment.html', contact_email=current_app.config['CONTACT_EMAIL'],
                           baton_price=current_app.config['BATON_PRICE_USD'])


@main.route('/privacy')
def privacy_policy():
    """Renders the privacy policy page."""
    return render_template('privacy.html')


@main.route('/terms')
def terms_of_service():
    """Renders the terms of service page."""
    return render_template('terms.html',
                         contact_email=current_app.config.get('CONTACT_EMAIL'))


@main.route('/stats.json')
def global_stats():
    """Returns global event statistics (unauthenticated endpoint)"""
    from app.models import Team, TeamMembership, TeamMembershipStatus, User

    # Count total teams that are open or closed (not pending or cancelled)
    team_count = Team.query.filter(
        Team.status.in_([TeamStatus.OPEN, TeamStatus.CLOSED]),
        Team.format == TeamFormat.TEAM
    ).count()

    solo_count = Team.query.filter(
        Team.format==TeamFormat.SOLO,
        Team.status.in_([TeamStatus.OPEN, TeamStatus.CLOSED])
    ).count()

    # Count total active team memberships
    membership_count = TeamMembership.query.filter_by(
        status=TeamMembershipStatus.ACTIVE
    ).count()

    # Count total users
    user_count = User.query.count()

    response = make_response(jsonify({
        'teams': team_count,
        'solos': solo_count,
        'memberships': membership_count,
        'users': user_count
    }))

    # Add CORS headers
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'

    return response

def _line_start_exchanges():
    """Line code ('1', '2') -> the exchange that line's branch starts from.

    The course is a Y (see course_service.legs_for), so each line's start is
    the one exchange on its course that no leg ends at. An Interline team
    starts at both.
    """
    from app.services.course_service import legs_for
    from app.utils import course_lines_for

    starts = {}
    for code, lines in (('1', TeamLines.ONE), ('2', TeamLines.TWO)):
        legs = legs_for(course_lines_for(lines))
        ends = {leg['end_exchange'] for leg in legs}
        starts[code] = next(str(leg['start_exchange']) for leg in legs if leg['start_exchange'] not in ends)
    return starts


def _merge_exchange():
    """The exchange where the lines' branches join, or None if they never do.

    It is the one exchange more than one leg ends at. An Interline team
    arrives there twice, once per branch, and carries on as one.
    """
    from app.services.course_service import legs_for

    ends = [leg['end_exchange'] for leg in legs_for()]
    return next((str(e) for e in ends if ends.count(e) > 1), None)


# Which of _line_start_exchanges() a team starts from.
_LINE_CODES = {TeamLines.ONE: ('1',), TeamLines.TWO: ('2',), TeamLines.BOTH: ('1', '2')}


def _build_results():
    """Times for each exchange based on uploaded images, as a JSON-able dict."""
    # For each team, get the images, grouped by associated exchange
    from app.models import Team, Image
    teams = Team.query.filter(
        Team.status.in_([TeamStatus.OPEN, TeamStatus.CLOSED])).order_by(Team.name).all()
    results = []
    latest_upload_time = None

    minimum_time = Config.EVENT_START_TIME.astimezone(datetime.UTC).replace(tzinfo=None)
    maximum_time = minimum_time + datetime.timedelta(hours=16)
    line_starts = _line_start_exchanges()
    merge_exchange = _merge_exchange()

    for team in teams:
        start_exchange_ids = {line_starts[code] for code in _LINE_CODES[team.lines]}
        format = team.format.value
        team_size = len([m for m in team.memberships if m.status == TeamMembershipStatus.ACTIVE])
        if team_size == 6:
            format = 'Competitive'
        team_data = {
            'name': team.name,
            "category": format,
            "teamSize": team_size,
            # '1 Line', '2 Line' or 'Interline'; which of `starts` apply.
            "lines": team.lines.value,
            'exchangeTimes': {},
            'observations': {}
        }
        # An Interline team reaches the merge exchange once per branch.
        # exchangeTimes keeps the later arrival like anywhere else; the first
        # accepted photo there is held back to report the earlier one too.
        first_at_merge = None
        if team.lines == TeamLines.BOTH:
            team_data['earlierArrivals'] = {}
        images = Image.query.filter_by(team_id=team.id).order_by(Image.capture_time).all()
        for img in images:
            # A manual correction wins over the automatic GPS association,
            img_exchange_id = img.manual_exchange_id or img.associated_exchange_id
            if img_exchange_id is not None:
                if img_exchange_id in team_data['exchangeTimes']:
                    # Check if this image is later than the last one for this exchange. We take the latest image
                    last_img_capture_time = team_data['exchangeTimes'][img_exchange_id]
                    if img.capture_time <= last_img_capture_time:
                        continue
                if current_app.jinja_env.globals['is_production'] and (img.capture_time > maximum_time):
                    # In production, ignore images with upload times outside the event window
                    continue
                if img.capture_time < minimum_time and img_exchange_id in start_exchange_ids:
                    # A start photo taken before the gun counts as the start itself.
                    team_data['exchangeTimes'][img_exchange_id] = minimum_time
                elif img.capture_time < minimum_time:
                    # Anything else from before the start is ignored.
                    continue
                else:
                    team_data['exchangeTimes'][img_exchange_id] = img.capture_time

                # Keep original capture time: start splits may be clamped to the gun.
                team_data['observations'][str(img_exchange_id)] = {
                    'capturedAt': img.capture_time.replace(tzinfo=datetime.UTC).isoformat(),
                    'uploadedAt': img.upload_time.replace(tzinfo=datetime.UTC).isoformat(),
                }

                if (first_at_merge is None and 'earlierArrivals' in team_data
                        and img_exchange_id == merge_exchange):
                    first_at_merge = img

                # Track the latest upload time across all images used in results
                if latest_upload_time is None or img.upload_time > latest_upload_time:
                    latest_upload_time = img.upload_time

        for exchange_id in team_data['exchangeTimes']:
            team_data['exchangeTimes'][exchange_id] -= Config.EVENT_START_TIME.astimezone(datetime.UTC).replace(tzinfo=None)
            team_data['exchangeTimes'][exchange_id] = int(team_data['exchangeTimes'][exchange_id].total_seconds())
        # Only an earlier arrival if a later photo went on to take the split.
        if first_at_merge is not None and (
                first_at_merge.capture_time - minimum_time).total_seconds() < team_data['exchangeTimes'][merge_exchange]:
            team_data['earlierArrivals'][merge_exchange] = {
                'time': int((first_at_merge.capture_time - minimum_time).total_seconds()),
                'capturedAt': first_at_merge.capture_time.replace(tzinfo=datetime.UTC).isoformat(),
                'uploadedAt': first_at_merge.upload_time.replace(tzinfo=datetime.UTC).isoformat(),
            }
        results.append(team_data)

    # Every team starts at the same time; per line, `starts` also says where.
    start_time = Config.EVENT_START_TIME.isoformat()
    return {
        'starts' : {
            'main': {
                'time': start_time,
            },
            **{code: {'time': start_time, 'exchange': exchange_id}
               for code, exchange_id in line_starts.items()},
        },
        # Where the branches join; Interline rows may carry `earlierArrivals` for it.
        'merge': {'exchange': merge_exchange},
        'results': results,
        'lastUpdated': latest_upload_time.replace(tzinfo=datetime.UTC).isoformat() if latest_upload_time else None,
    }


@main.route('/results.json')
def results():
    """
    API endpoint to get times for each exchange based on uploaded images.

    Live-results pages poll this, so the rendered body is kept for
    RESULTS_CACHE_SECONDS: however many viewers there are, the database is
    read at most once per window. The cache is per process, which is all a
    few seconds of staleness needs.
    :return:
    """
    cache = current_app.extensions.setdefault('results_cache', {})
    ttl = current_app.config['RESULTS_CACHE_SECONDS']
    now = time.monotonic()
    if ttl <= 0 or 'body' not in cache or now >= cache['expires']:
        body = jsonify(_build_results()).get_data()
        cache.update(body=body, etag=hashlib.sha256(body).hexdigest()[:16], expires=now + ttl)

    response = current_app.response_class(cache['body'], mimetype='application/json')

    response.headers['Cache-Control'] = 'no-cache'
    response.set_etag(cache['etag'])

    # Add CORS headers
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'

    return response.make_conditional(request)

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