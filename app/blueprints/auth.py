import datetime
import logging
import hashlib
from flask import Blueprint, request, redirect, render_template, abort, session, url_for
from flask_login import login_user, logout_user, login_required, current_user
from app.models import db, User, OAuthProvider, UserRole
from app.config import Config, OAUTH_PROVIDERS
from app.security import limiter

auth = Blueprint('auth', __name__)


def get_gravatar_url(email, size=200, default='identicon'):
    """Generate Gravatar URL from email address"""
    if not email:
        return None

    # Normalize email: strip whitespace, convert to lowercase
    email = email.strip().lower()

    # Create MD5 hash of email
    email_hash = hashlib.md5(email.encode('utf-8')).hexdigest()

    # Build Gravatar URL
    return f"https://www.gravatar.com/avatar/{email_hash}?s={size}&d={default}"



def init_oauth(oauth_instance):
    """Initialize OAuth providers with the OAuth instance from the main app"""
    # Configure OAuth providers
    google = oauth_instance.register(
        name='google',
        client_id=Config.GOOGLE_CLIENT_ID,
        client_secret=Config.GOOGLE_CLIENT_SECRET,
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'}
    )

    github = oauth_instance.register(
        name='github',
        client_id=Config.GITHUB_CLIENT_ID,
        client_secret=Config.GITHUB_CLIENT_SECRET,
        access_token_url='https://github.com/login/oauth/access_token',
        authorize_url='https://github.com/login/oauth/authorize',
        api_base_url='https://api.github.com/',
        client_kwargs={'scope': 'user:email'},
    )

    microsoft = oauth_instance.register(
        name='microsoft',
        client_id=Config.MICROSOFT_CLIENT_ID,
        client_secret=Config.MICROSOFT_CLIENT_SECRET,
        access_token_url='https://login.microsoftonline.com/common/oauth2/v2.0/token',
        authorize_url='https://login.microsoftonline.com/common/oauth2/v2.0/authorize',
        api_base_url='https://graph.microsoft.com/v1.0/',
        jwks_uri='https://login.microsoftonline.com/common/discovery/v2.0/keys',
        client_kwargs={'scope': 'openid email profile User.Read'}
    )

    return google, github, microsoft


@auth.route('/login/<provider>')
@limiter.limit("5 per minute")
def oauth_login(provider):
    if provider not in [p['name'] for p in OAUTH_PROVIDERS]:
        return abort(404)

    from flask import current_app
    import secrets
    oauth_instance = current_app.extensions.get('authlib.integrations.flask_client')
    client = oauth_instance.create_client(provider)

    # Generate CSRF state token for OAuth security
    state_token = secrets.token_urlsafe(32)
    session['oauth_state'] = state_token

    # Store the 'next' parameter in the session
    next_url = request.args.get('next')
    if next_url:
        session['next_url'] = next_url

    redirect_uri = url_for('auth.oauth_callback', provider=provider, _external=True)
    logging.warning(f"Redirecting to {redirect_uri} for provider {provider}")

    # Add state parameter for CSRF protection
    return client.authorize_redirect(redirect_uri, state=state_token)


@auth.route('/account/auth/<provider>')
def oauth_callback(provider):
    if provider not in [p['name'] for p in OAUTH_PROVIDERS]:
        return abort(404)

    from flask import current_app
    oauth_instance = current_app.extensions.get('authlib.integrations.flask_client')
    client = oauth_instance.create_client(provider)

    try:
        # Validate CSRF state token
        received_state = request.args.get('state')
        expected_state = session.pop('oauth_state', None)

        if not received_state or received_state != expected_state:
            logging.error(f"OAuth state mismatch for provider {provider}. Expected: {expected_state}, Received: {received_state}")
            logging.warning(f"Request scheme: {request.scheme}, headers: {dict(request.headers)}")
            return redirect(url_for('auth.login', error='auth_failed'))

        token = client.authorize_access_token()

        if provider == OAuthProvider.GOOGLE.value:
            user_info = token.get('userinfo')
            if user_info:
                email = user_info.get('email')
                name = user_info.get('name')
                avatar_url = user_info.get('picture')
                provider_id = str(user_info.get('sub'))
        elif provider == OAuthProvider.GITHUB.value:
            # Get user info from GitHub API
            resp = client.get('user', token=token)
            user_info = resp.json()

            # Get primary email from GitHub API
            email_resp = client.get('user/emails', token=token)
            emails = email_resp.json()
            primary_email = next((e['email'] for e in emails if e['primary']), None)

            email = primary_email or user_info.get('email')
            name = user_info.get('name') or user_info.get('login')
            avatar_url = user_info.get('avatar_url')
            provider_id = str(user_info.get('id'))
        elif provider == OAuthProvider.MICROSOFT.value:
            # Get user info from Microsoft Graph API
            resp = client.get('me', token=token)
            user_info = resp.json()

            email = user_info.get('mail') or user_info.get('userPrincipalName')
            name = user_info.get('displayName')
            provider_id = str(user_info.get('id'))

            # Get profile photo from Microsoft Graph API
            avatar_url = None
            try:
                photo_resp = client.get('me/photo/$value', token=token)
                if photo_resp.status_code == 200:
                    # Convert photo data to base64 data URL
                    import base64
                    photo_data = photo_resp.content
                    photo_b64 = base64.b64encode(photo_data).decode('utf-8')
                    avatar_url = f"data:image/jpeg;base64,{photo_b64}"
                    logging.warning(f"DEBUG: Successfully fetched Microsoft profile photo")
                else:
                    logging.warning(f"DEBUG: No profile photo available (status: {photo_resp.status_code})")
            except Exception as photo_error:
                logging.warning(f"DEBUG: Failed to fetch Microsoft profile photo: {photo_error}")

            logging.warning(f"DEBUG: Extracted - email: {email}, name: {name}, provider_id: {provider_id}, has_avatar: {avatar_url is not None}")
        else:
            return redirect(url_for('auth.login', error='auth_failed'))

        if not email or not provider_id:
            return redirect(url_for('auth.login', error='missing_data'))

        # Apply Gravatar fallback if no avatar from provider
        final_avatar_url = avatar_url or get_gravatar_url(email)

        # Find or create user
        user = User.query.filter_by(provider=OAuthProvider(provider), provider_id=provider_id).first()

        if user:
            # Update existing user
            user.email = email
            user.name = name
            user.avatar_url = final_avatar_url
            user.last_login = datetime.datetime.now(datetime.UTC)
            db.session.commit()
            login_user(user)
        else:
            # Check if email exists with different provider
            existing_user = User.query.filter_by(email=email).first()
            if existing_user:
                return redirect(url_for('auth.login', error='email_exists'))

            # Store user info in session for account creation confirmation
            session['pending_user'] = {
                'email': email,
                'name': name,
                'avatar_url': final_avatar_url,
                'provider': provider,
                'provider_id': provider_id
            }

            # Redirect to account creation confirmation page
            return redirect(url_for('auth.confirm_account_creation'))

        # Redirect to the original page from session or to index
        next_page = session.pop('next_url', None)

        if not next_page or not next_page.startswith('/'):
            next_page = url_for('main.index')
        return redirect(next_page)

    except Exception as e:
        logging.error(f"OAuth error for provider {provider}: {e}")
        logging.error(f"OAuth error type: {type(e)}")
        import traceback
        logging.error(f"OAuth error traceback: {traceback.format_exc()}")
        return redirect(url_for('auth.login', error='auth_failed'))


@auth.route('/login')
def login():
    # If user is already logged in, redirect to next page or index
    if current_user.is_authenticated:
        next_page = request.args.get('next')
        if next_page and next_page.startswith('/'):
            return redirect(next_page)
        return redirect(url_for('main.index'))

    error = request.args.get('error')
    error_messages = {
        'missing_data': 'Unable to retrieve user information from OAuth provider.',
        'email_exists': 'An account with this email already exists with a different provider.',
        'auth_failed': 'Authentication failed. Please try again.'
    }
    error_message = error_messages.get(error) if error else None

    # Preserve the next URL for the OAuth links
    next_url = request.args.get('next')

    return render_template('login.html', providers=OAUTH_PROVIDERS, error=error_message, next_url=next_url)


@auth.route('/confirm-account-creation')
def confirm_account_creation():
    # Check if there's pending user data in session
    pending_user = session.get('pending_user')
    if not pending_user:
        return redirect(url_for('auth.login'))

    # Handle error messages
    error = request.args.get('error')
    error_message = None
    if error == 'terms_required':
        error_message = 'You must agree to the Terms of Service to create an account.'

    return render_template('confirm_account_creation.html',
                         email=pending_user['email'],
                         name=pending_user['name'],
                         provider=pending_user['provider'],
                         avatar_url=pending_user['avatar_url'],
                         error=error_message)


@auth.route('/create-account', methods=['POST'])
def create_account():
    # Check if there's pending user data in session
    pending_user = session.pop('pending_user', None)
    if not pending_user:
        return redirect(url_for('auth.login'))

    # Validate terms agreement
    if not request.form.get('agree_terms'):
        # Put user data back in session and redirect with error
        session['pending_user'] = pending_user
        return redirect(url_for('auth.confirm_account_creation') + '?error=terms_required')

    # Create new user. Role is decided once, at creation -- ADMIN_EMAIL takes
    # precedence over MANAGER_EMAILS (moot in practice, but an address
    # listed in both should end up ADMIN, the higher tier).
    if pending_user['email'] == Config.ADMIN_EMAIL:
        role = UserRole.ADMIN
    elif pending_user['email'] in Config.MANAGER_EMAILS:
        role = UserRole.MANAGER
    else:
        role = UserRole.PARTICIPANT

    user = User(
        email=pending_user['email'],
        name=pending_user['name'],
        avatar_url=pending_user['avatar_url'],
        provider=OAuthProvider(pending_user['provider']),
        provider_id=pending_user['provider_id'],
        role=role
    )
    db.session.add(user)
    db.session.commit()
    login_user(user)

    # Redirect to the original page from session or to index
    next_page = session.pop('next_url', None)
    if not next_page or not next_page.startswith('/'):
        next_page = url_for('main.index')
    return redirect(next_page)


@auth.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('main.index'))