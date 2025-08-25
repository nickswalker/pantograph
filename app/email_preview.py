"""
Email Template Preview System
Provides sample data for previewing email templates in the admin interface.
"""

from datetime import datetime, timezone
from app.models import TeamFormat, TeamStatus
from app.utils import format_hh_mm_from_seconds


class MockUser:
    def __init__(self, name, email, provider="google"):
        self.name = name
        self.email = email
        self.provider_name = provider
        self.id = "preview123"


class MockTeam:
    def __init__(self, name, format_type=TeamFormat.TEAM, status=TeamStatus.OPEN, estimated_duration_seconds=18000):
        self.name = name
        self.format = format_type
        self.status = status
        self.estimated_duration_seconds = estimated_duration_seconds
        self.id = "teampreview"
        self.captain = MockUser("Sarah Johnson", "sarah@example.com")


class MockMembership:
    def __init__(self, user, team, preferred_miles=3.5, planned_pace_seconds=480, willing_to_lead=True):
        self.user = user
        self.team = team
        self.preferred_miles = preferred_miles
        self.planned_pace_seconds = planned_pace_seconds
        self.willing_to_lead = willing_to_lead
        self.preferred_station = "University District Station"
        self.comments = "Looking forward to running with the team!"
        self.joined_at = datetime.now(timezone.utc)


def get_sample_data_for_template(template_name):
    """Generate sample data for email template previews"""

    # Common sample data
    sample_team = MockTeam("Lightning Runners", TeamFormat.TEAM, TeamStatus.OPEN, 18000)
    sample_user = MockUser("Alex Chen", "alex@example.com")
    sample_captain = MockUser("Sarah Johnson", "sarah@example.com")

    base_context = {
        'contact_email': 'support@example.com',
        'event_name': 'Light Rail Relay 2025',
        'event_url': '#event-preview-link',
        'payment_url': '#payment-preview-link',
        'my_preferences_url': '#prefs-preview-link',
        'team_url': '#team-preview-link'
    }

    if template_name == 'team_approval':
        team = MockTeam("Lightning Runners", TeamFormat.TEAM, TeamStatus.OPEN, 18000)
        return {
            **base_context,
            'team': team,
            'approval_message': 'Your team has been approved for the 2024 Light Rail Relay! We\'re excited to see you race.',
            'next_steps': [
                'Check your team roster and invite additional members if needed',
                'Review race day logistics and station assignments',
                'Start training and coordinate with your team members'
            ]
        }

    elif template_name == 'team_creation':
        team = MockTeam("Lightning Runners", TeamFormat.TEAM, TeamStatus.PENDING, 18000)
        return {
            **base_context,
            'team': team,
            'estimated_duration_display': format_hh_mm_from_seconds(team.estimated_duration_seconds),
        }

    elif template_name == 'member_joined':
        membership = MockMembership(sample_user, sample_team)
        return {
            **base_context,
            'team': sample_team,
            'membership': membership,
        }

    elif template_name == 'captain_transfer':
        team = sample_team
        previous_captain = MockUser("Mike Rodriguez", "mike@example.com")
        new_captain = MockUser("Alex Chen", "alex@example.com")
        return {
            **base_context,
            'team': team,
            'previous_captain': previous_captain,
            'new_captain': new_captain,
            'member_count': 8,
        }

    elif template_name == 'new_members_digest':
        team = sample_team
        new_members = [
            MockMembership(MockUser("Emma Thompson", "emma@example.com"), team, 2.8, 420, True),
            MockMembership(MockUser("David Park", "david@example.com"), team, 4.2, 510, False),
            MockMembership(MockUser("Lisa Wang", "lisa@example.com"), team, 3.0, 450, True)
        ]
        return {
            **base_context,
            'team': team,
            'captain_name': team.captain.name,
            'new_members': new_members,
            'total_members': 11,
        }

    else:
        # Default fallback
        return {
            **base_context,
            'team': sample_team,
            'user': sample_user
        }


def get_available_templates():
    """Return list of available email templates for preview"""
    return [
        {'name': 'team_approval', 'display': 'Team Approval'},
        {'name': 'team_creation', 'display': 'Team Creation Confirmation'},
        {'name': 'member_joined', 'display': 'Member Joined Welcome'},
        {'name': 'captain_transfer', 'display': 'Captain Transfer Notification'},
        {'name': 'new_members_digest', 'display': 'New Members Digest'}
    ]


def get_sample_subject_for_template(template_name):
    """Generate sample email subjects for previews"""
    subjects = {
        'team_approval': "Your team 'Lightning Runners' has been approved",
        'team_creation': "Team 'Lightning Runners' Created",
        'member_joined': "Welcome to Team 'Lightning Runners'",
        'captain_transfer': "You're Now Captain of Team 'Lightning Runners'",
        'new_members_digest': "New Team Members - Lightning Runners"
    }
    return subjects.get(template_name, f"Email Preview: {template_name.replace('_', ' ').title()}")