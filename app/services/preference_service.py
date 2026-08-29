"""Captain overrides of stated member preferences
"""

import json

from app.models import db, MembershipPreferenceOverride
from app.services.exceptions import ServiceError
from app.utils import course_lines_for, load_end_station_names, max_preferred_miles

#: Preference fields a captain may override, in display order. Every one of
#: these is a column on TeamMembership and a key in the board's member JSON.
OVERRIDABLE_FIELDS = (
    'preferred_miles',
    'planned_pace_seconds',
    'preferred_station',
    'willing_to_lead',
)


MIN_PACE_SECONDS = 60
MAX_PACE_SECONDS = 5999


# --- Reading -------------------------------------------------------------

def stated_preferences(membership):
    """What the member themselves entered, JSON-ready."""
    return {
        'willing_to_lead': bool(membership.willing_to_lead),
        'preferred_miles': (
            float(membership.preferred_miles) if membership.preferred_miles is not None else None
        ),
        'planned_pace_seconds': membership.planned_pace_seconds,
        'preferred_station': membership.preferred_station,
    }


def applied_overrides(membership):
    """The captain's sparse override dict (see the model docstring).
    """
    record = getattr(membership, 'preference_override', None)
    if record is None:
        return {}
    try:
        stored = json.loads(record.overrides or '{}')
    except ValueError:
        return {}
    if not isinstance(stored, dict):
        return {}
    return {field: stored[field] for field in OVERRIDABLE_FIELDS if field in stored}


def effective_preferences(membership):
    """Stated preferences with any captain override applied on top.

    This is what the board, the badges, and the solver consume.
    """
    return {**stated_preferences(membership), **applied_overrides(membership)}


def stale_override_fields(membership):
    """Overridden fields the member has since changed their own answer for.

    Without this, a member updating their registration would be silently
    outvoted by an override written against an answer they no longer give.
    """
    record = getattr(membership, 'preference_override', None)
    overrides = applied_overrides(membership)
    if record is None or not overrides or not record.stated_snapshot:
        return []

    try:
        snapshot = json.loads(record.stated_snapshot)
    except ValueError:
        return []
    if not isinstance(snapshot, dict):
        return []

    stated = stated_preferences(membership)
    return [
        field for field in overrides
        if field in snapshot and snapshot[field] != stated[field]
    ]


def serialize(membership, include_note=False):
    """The override sidecar for a board member entry.

    ``note`` is the captain's private rationale, so it is included only when
    the caller says the viewer may see it.
    """
    data = {
        'stated': stated_preferences(membership),
        'overrides': applied_overrides(membership),
        'stale_override_fields': stale_override_fields(membership),
    }
    if include_note:
        record = getattr(membership, 'preference_override', None)
        data['override_note'] = record.note if record else None
    return data


# --- Validation ----------------------------------------------------------

def _validate_preferred_miles(value, team):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _reject('Preferred miles must be a number')
    max_miles = max_preferred_miles(team.lines)
    if not (0.1 <= float(value) <= max_miles):
        return _reject(f'Preferred miles must be a number between 0.1 and {max_miles:g}')
    # The column is Numeric(4, 1); round here so the override and the value it
    # would replace are comparable.
    return round(float(value), 1)


def _validate_pace(value, team):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        return _reject('Planned pace must be a whole number of seconds per mile')
    if not (MIN_PACE_SECONDS <= value <= MAX_PACE_SECONDS):
        return _reject(
            f'Planned pace must be between {MIN_PACE_SECONDS} and {MAX_PACE_SECONDS} seconds per mile'
        )
    return value


def _validate_station(value, team):
    if value is None:
        return None
    if not isinstance(value, str):
        return _reject('Preferred station must be a station name')
    station = value.strip()
    if not station:
        return None
    if station not in load_end_station_names(course_lines_for(team.lines)):
        return _reject(f'{station} is not a station on the {team.lines.value} course.')
    return station


def _validate_willing_to_lead(value, team):
    # Non-nullable on the membership, so there is no "no preference" state to
    # override it to: reverting means dropping the key, not nulling it.
    if not isinstance(value, bool):
        return _reject(
            'Willing to lead must be true or false; omit the field to go back to what the member stated'
        )
    return value


def _reject(message):
    raise ServiceError(message)


_VALIDATORS = {
    'preferred_miles': _validate_preferred_miles,
    'planned_pace_seconds': _validate_pace,
    'preferred_station': _validate_station,
    'willing_to_lead': _validate_willing_to_lead,
}


def validate_overrides(team, overrides):
    """Normalize a raw override payload, or raise :class:`ServiceError`.

    Only known fields survive, and an override that merely restates what the
    member already said is dropped -- it is not an adjustment, and keeping it
    would produce a permanently "adjusted" chip that says nothing.
    """
    if not isinstance(overrides, dict):
        raise ServiceError('Overrides must be an object keyed by preference field')

    unknown = set(overrides) - set(OVERRIDABLE_FIELDS)
    if unknown:
        raise ServiceError(f"Unknown preference field(s): {', '.join(sorted(unknown))}")

    return {
        field: _VALIDATORS[field](overrides[field], team)
        for field in OVERRIDABLE_FIELDS
        if field in overrides
    }


def _drop_redundant(normalized, membership):
    stated = stated_preferences(membership)
    return {field: value for field, value in normalized.items() if value != stated[field]}


# --- Writing --------------------------------------------------------------

def set_overrides(team, membership, overrides, note, actor):
    """Replace ``membership``'s override set wholesale.

    Empty (or entirely redundant) overrides clear the record outright,
    including its note -- there is no such thing as a rationale for an
    adjustment that isn't being made.

    Returns the membership, refreshed.
    """
    if membership.team_id != team.id:
        raise ServiceError('Membership is not part of this team', status=404)

    normalized = _drop_redundant(validate_overrides(team, overrides), membership)
    record = membership.preference_override

    if not normalized:
        if record is not None:
            db.session.delete(record)
            db.session.commit()
        return membership

    note = (note or '').strip() or None
    payload = json.dumps(normalized)
    # Snapshot only the fields being overridden: an unrelated later edit by
    # the member should not make every override look stale.
    stated = stated_preferences(membership)
    snapshot = json.dumps({field: stated[field] for field in normalized})

    if record is None:
        record = MembershipPreferenceOverride(
            membership_id=membership.id,
            overrides=payload,
            stated_snapshot=snapshot,
            note=note,
            created_by=actor.id,
        )
        db.session.add(record)
    else:
        record.overrides = payload
        record.stated_snapshot = snapshot
        record.note = note
        record.created_by = actor.id

    db.session.commit()
    return membership


def clear_overrides(team, membership):
    """Drop every override for ``membership``, reverting to stated values."""
    if membership.team_id != team.id:
        raise ServiceError('Membership is not part of this team', status=404)

    record = membership.preference_override
    if record is not None:
        db.session.delete(record)
        db.session.commit()
    return membership


# --- Display helpers ------------------------------------------------------

def _format_value(field, value):
    from app.utils import format_mm_ss_from_seconds

    if value is None:
        return 'no preference'
    if field == 'preferred_miles':
        return f'{value:g} mi'
    if field == 'planned_pace_seconds':
        return f'{format_mm_ss_from_seconds(value)}/mi'
    if field == 'willing_to_lead':
        return 'yes' if value else 'no'
    return str(value)


_FIELD_LABELS = {
    'preferred_miles': 'miles',
    'planned_pace_seconds': 'pace',
    'preferred_station': 'end station',
    'willing_to_lead': 'willing to lead',
}


def describe_overrides(membership):
    """One-line human summary, e.g. ``miles 8 mi -> 5 mi; pace 8:00/mi -> 9:30/mi``.

    Used by the members page and the CSV/TSV export, which show stated values
    and need to say plainly where a captain has adjusted them.
    """
    overrides = applied_overrides(membership)
    if not overrides:
        return ''
    stated = stated_preferences(membership)
    return '; '.join(
        f'{_FIELD_LABELS[field]} {_format_value(field, stated[field])} '
        f'-> {_format_value(field, overrides[field])}'
        for field in OVERRIDABLE_FIELDS if field in overrides
    )
