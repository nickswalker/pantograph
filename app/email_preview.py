"""
Email Template Preview System
Provides sample data for previewing email templates in the admin interface.
"""

from datetime import datetime, timezone
from app.models import TeamFormat, TeamStatus, NotificationType
from app.utils import format_hh_mm_from_seconds, get_registration_deadline_info


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

    match template_name:
        case NotificationType.TEAM_APPROVAL.value:
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

        case NotificationType.TEAM_CREATION.value:
            team = MockTeam("Lightning Runners", TeamFormat.TEAM, TeamStatus.PENDING, 18000)
            return {
                **base_context,
                'team': team,
                'estimated_duration_display': format_hh_mm_from_seconds(team.estimated_duration_seconds),
            }

        case NotificationType.MEMBER_JOINED.value:
            membership = MockMembership(sample_user, sample_team)
            return {
                **base_context,
                'team': sample_team,
                'membership': membership,
            }

        case NotificationType.CAPTAIN_TRANSFER.value:
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

        case NotificationType.NEW_MEMBERS_DIGEST.value:
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

        case NotificationType.REGISTRATION_REMINDER.value:
            return {
                **base_context,
                'user': sample_user,
                'registration_close_date': get_registration_deadline_info()['deadline_str'],
                'registration_url': '#registration-link'
            }

        case NotificationType.PAYMENT_REMINDER.value:
            team = sample_team
            return {
                **base_context,
                'user': sample_user,
                'team': team,
                'estimated_duration_display': format_hh_mm_from_seconds(team.estimated_duration_seconds),
            }

        case _:
            # Default fallback
            return {
                **base_context,
                'team': sample_team,
                'user': sample_user
            }


def get_available_templates():
    """Return list of available email templates for preview"""
    return [
        {'name': NotificationType.TEAM_APPROVAL.value, 'display': 'Team Approval'},
        {'name': NotificationType.TEAM_CREATION.value, 'display': 'Team Creation Confirmation'},
        {'name': NotificationType.MEMBER_JOINED.value, 'display': 'Member Joined Welcome'},
        {'name': NotificationType.CAPTAIN_TRANSFER.value, 'display': 'Captain Transfer Notification'},
        {'name': NotificationType.NEW_MEMBERS_DIGEST.value, 'display': 'New Members Digest'},
        {'name': NotificationType.REGISTRATION_REMINDER.value, 'display': 'Registration Reminder'},
        {'name': NotificationType.PAYMENT_REMINDER.value, 'display': 'Payment Reminder'},
    ]


def get_sample_subject_for_template(template_name):
    """Generate sample email subjects for previews"""
    subjects = {
        NotificationType.TEAM_APPROVAL.value: "Your team 'Lightning Runners' has been approved",
        NotificationType.TEAM_CREATION.value: "Team 'Lightning Runners' Created",
        NotificationType.MEMBER_JOINED.value: "Welcome to Team 'Lightning Runners'",
        NotificationType.CAPTAIN_TRANSFER.value: "You're Now Captain of Team 'Lightning Runners'",
        NotificationType.NEW_MEMBERS_DIGEST.value: "New Team Members - Lightning Runners",
        NotificationType.REGISTRATION_REMINDER.value: "Reminder: Register for Light Rail Relay 2025",
        NotificationType.PAYMENT_REMINDER.value: "Reminder: Complete Registration for Light Rail Relay 2025"
    }
    return subjects.get(template_name, f"Email Preview: {template_name.replace('_', ' ').title()}")