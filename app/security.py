"""
Security middleware and utilities for the Pantograph application.
Handles security headers, HTTPS enforcement, and other security measures.
"""

import os
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Create limiter instance that can be imported by blueprints
limiter = Limiter(key_func=get_remote_address)


def add_security_headers(response):
    """Add security headers to all responses"""

    # Security headers
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'

    # HTTPS enforcement headers (only in production)
    if os.getenv('FLASK_ENV') != 'development' and os.getenv('FLASK_DEBUG') != '1':
        response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'

    return response


def register_security(app):
    """Register security middleware with Flask app"""

    # Initialize the global limiter with this app
    limiter.init_app(app)
    limiter.default_limits = ["1000 per day", "100 per hour"]

    @app.after_request
    def after_request(response):
        # Add security headers to all responses
        return add_security_headers(response)

    return limiter