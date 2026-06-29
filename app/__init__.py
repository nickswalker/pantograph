import os
from flask import Flask
from flask_login import LoginManager
from authlib.integrations.flask_client import OAuth
from sqlalchemy import text
from werkzeug.middleware.proxy_fix import ProxyFix
from pillow_heif import register_heif_opener

from app.config import Config
from app.models import db, User
from app.permissions import register_permissions
from app.security import register_security


# Register HEIC/AVIF opener for Pillow
register_heif_opener()


def create_app():
    """Application factory pattern"""
    app = Flask(__name__, static_folder='static', template_folder='templates')
    app.config.from_object(Config)

    # Configure ProxyFix for reverse proxy SSL termination (production only)
    if os.getenv('FLASK_ENV') != 'development' and os.getenv('FLASK_DEBUG') != '1':
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=1,      # Trust 1 proxy for X-Forwarded-For
            x_proto=1,    # Trust 1 proxy for X-Forwarded-Proto (HTTPS detection)
            x_host=1,     # Trust 1 proxy for X-Forwarded-Host
            x_prefix=1    # Trust 1 proxy for X-Forwarded-Prefix
        )
    else:
        # Disable secure cookies in development (HTTP)
        app.config['SESSION_COOKIE_SECURE'] = False

    # Make APPLICATION_NAME available globally in Jinja2 templates
    app.jinja_env.globals['app_name'] = app.config['APPLICATION_NAME']

    app.jinja_env.globals['is_production'] = (
        os.getenv('FLASK_ENV') != 'development' and os.getenv('FLASK_DEBUG') != '1'
    )

    # Make model enums available globally in Jinja2 templates
    from app.models import TeamMembershipStatus, TeamFormat, TeamStatus, OAuthProvider
    app.jinja_env.globals['TeamMembershipStatus'] = TeamMembershipStatus
    app.jinja_env.globals['TeamFormat'] = TeamFormat
    app.jinja_env.globals['TeamStatus'] = TeamStatus
    app.jinja_env.globals['OAuthProvider'] = OAuthProvider

    # Register custom Jinja2 filters
    from app.utils import format_hh_mm_from_seconds, format_mm_ss_from_seconds
    app.jinja_env.filters['format_hh_mm_from_seconds'] = format_hh_mm_from_seconds
    app.jinja_env.filters['format_mm_ss_from_seconds'] = format_mm_ss_from_seconds

    # Ensure upload directory exists
    os.makedirs(Config.UPLOAD_FOLDER, exist_ok=True)

    # Initialize extensions
    db.init_app(app)
    register_permissions(app)

    # Setup Flask-Login
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, user_id)

    # Setup OAuth
    oauth = OAuth(app)

    # Register blueprints
    from app.blueprints.main import main
    from app.blueprints.auth import auth, init_oauth
    from app.blueprints.teams import teams, teams_public
    from app.blueprints.admin import admin
    from app.blueprints.user import user

    app.register_blueprint(main)
    app.register_blueprint(auth)
    app.register_blueprint(teams)
    app.register_blueprint(teams_public)  # Public routes without prefix
    app.register_blueprint(admin)
    app.register_blueprint(user)

    # Initialize OAuth providers
    init_oauth(oauth)

    # Register security middleware (HTTPS enforcement, security headers)
    register_security(app)

    # Register error handlers for production security
    register_error_handlers(app)

    # Initialize database
    with app.app_context():
        db.create_all()
        if db.engine.url.drivername.startswith('sqlite'):
            try:
                db.session.execute(text('PRAGMA journal_mode=WAL'))
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                app.logger.warning("Failed to enable SQLite WAL mode: %s", e)
        print("Database initialized")

    return app


def register_error_handlers(app):
    """Register secure error handlers that don't leak sensitive information"""
    import os

    @app.errorhandler(404)
    def not_found_error(error):
        return {"error": "Page not found"}, 404

    @app.errorhandler(403)
    def forbidden_error(error):
        return {"error": "Access forbidden"}, 403

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()

        # Log the actual error for debugging (but don't show to user)
        if os.getenv('FLASK_ENV') != 'development' and os.getenv('FLASK_DEBUG') != '1':
            app.logger.error(f'Server Error: {error}', exc_info=True)
            # Return generic error message in production
            return {"error": "Internal server error"}, 500
        else:
            # Show detailed error in development
            raise error

    @app.errorhandler(413)
    def too_large_error(error):
        return {"error": "File too large"}, 413

    @app.errorhandler(429)
    def ratelimit_handler(error):
        return {"error": "Rate limit exceeded"}, 429
