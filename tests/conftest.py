"""Pytest fixtures: a Flask app wired to a temporary SQLite database.

``app.config.Config`` resolves secrets at import time, so dummy values must be
in the environment before anything under ``app`` is imported.
"""

import os

_DUMMY_SECRETS = {
    'SECRET_KEY': 'test-secret-key',
    'GOOGLE_CLIENT_ID': 'test',
    'GOOGLE_CLIENT_SECRET': 'test',
    'GITHUB_CLIENT_ID': 'test',
    'GITHUB_CLIENT_SECRET': 'test',
    'MICROSOFT_CLIENT_ID': 'test',
    'MICROSOFT_CLIENT_SECRET': 'test',
    'ADMIN_EMAIL': 'admin@example.com',
    'CONTACT_EMAIL': 'contact@example.com',
    'NOTIFICATION_EMAIL': 'notify@example.com',
    'CANONICAL_URL': 'http://localhost:5001',
    'AWS_ACCESS_KEY_ID': 'test',
    'AWS_SECRET_ACCESS_KEY': 'test',
    'AWS_REGION': 'us-west-2',
}
for _key, _value in _DUMMY_SECRETS.items():
    os.environ.setdefault(_key, _value)
os.environ.setdefault('FLASK_ENV', 'development')

import pytest  # noqa: E402

from app.config import Config  # noqa: E402


@pytest.fixture()
def app(tmp_path, monkeypatch):
    """A fresh application bound to a temporary SQLite database."""
    monkeypatch.setattr(
        Config, 'SQLALCHEMY_DATABASE_URI', f"sqlite:///{tmp_path / 'test.db'}"
    )
    monkeypatch.setattr(Config, 'UPLOAD_FOLDER', str(tmp_path / 'uploads'))

    from app import create_app
    from app.models import db

    flask_app = create_app()
    flask_app.config.update(TESTING=True)

    yield flask_app

    with flask_app.app_context():
        db.session.remove()
        db.engine.dispose()


@pytest.fixture()
def client(app):
    return app.test_client()


def login(client, user_id):
    """Log a user in by seeding the Flask-Login session cookie."""
    with client.session_transaction() as session:
        session['_user_id'] = user_id
        session['_fresh'] = True
