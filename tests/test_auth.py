"""Tests for the post-OAuth account flow: role grants and redirect targets.

The OAuth round trip itself isn't exercised; these tests seed the session
with what ``oauth_callback`` would have stored and drive the endpoints after it.
"""

import pytest

from app.config import Config


@pytest.mark.parametrize('next_url, expected', [
    ('/teams/abc/gallery', '/teams/abc/gallery'),
    ('/join-team?invite_token=x', '/join-team?invite_token=x'),
    ('//evil.com', None),
    ('//evil.com/path', None),
    ('/\\evil.com', None),
    ('https://evil.com', None),
    ('evil.com', None),
    ('', None),
    (None, None),
])
def test_safe_next_url(next_url, expected):
    from app.blueprints.auth import safe_next_url
    assert safe_next_url(next_url) == expected


def _create_account(client, email, email_verified, next_url=None):
    with client.session_transaction() as session:
        session['pending_user'] = {
            'email': email,
            'name': 'New Person',
            'avatar_url': None,
            'provider': 'microsoft',
            'provider_id': f'pid-{email}',
            'email_verified': email_verified,
        }
        if next_url is not None:
            session['next_url'] = next_url
    return client.post('/create-account', data={'agree_terms': 'on'})


def _role_of(app, email):
    from app.models import User
    with app.app_context():
        return User.query.filter_by(email=email).one().role


def test_verified_admin_email_gets_admin(app, client):
    from app.models import UserRole
    _create_account(client, Config.ADMIN_EMAIL.upper(), email_verified=True)
    assert _role_of(app, Config.ADMIN_EMAIL.upper()) == UserRole.ADMIN


def test_unverified_admin_email_gets_participant(app, client):
    from app.models import UserRole
    _create_account(client, Config.ADMIN_EMAIL, email_verified=False)
    assert _role_of(app, Config.ADMIN_EMAIL) == UserRole.PARTICIPANT


def test_manager_email_requires_verification(app, client, monkeypatch):
    from app.models import UserRole
    monkeypatch.setattr(Config, 'MANAGER_EMAILS', {'Boss@Example.com'})

    _create_account(client, 'boss@example.com', email_verified=True)
    assert _role_of(app, 'boss@example.com') == UserRole.MANAGER


def test_unverified_manager_email_gets_participant(app, client, monkeypatch):
    from app.models import UserRole
    monkeypatch.setattr(Config, 'MANAGER_EMAILS', {'boss@example.com'})

    _create_account(client, 'boss@example.com', email_verified=False)
    assert _role_of(app, 'boss@example.com') == UserRole.PARTICIPANT


def test_create_account_ignores_offsite_next(client):
    response = _create_account(client, 'someone@example.com', True, next_url='//evil.com')
    assert response.status_code == 302
    assert response.headers['Location'] == '/'


def test_create_account_follows_local_next(client):
    response = _create_account(client, 'someone@example.com', True, next_url='/join-team')
    assert response.headers['Location'] == '/join-team'
