/**
 * Leg Assignment Schedule View
 *
 * The read-first half of /team/<team_id>/legs: what the day looks like, as
 * opposed to the board (leg-board.js), which is about filling slots. Both
 * pieces are fed by the same `window.onAssignmentsChanged(state)` hook the
 * badges use.
 *
 *   - `#legs-my-assignment` -- the viewer's own legs, total mileage, and
 *     estimated start times.
 *   - `#legs-schedule` -- the course as a timetable, one row per handoff.
 *     A leg's destination is the next row's exchange, so it is named only
 *     where the chain ends: the finish, and an Interline transfer.
 *
 * Clock times come from `legScheduleMetrics()` and appear only where they are
 * derivable -- the first leg without a pace estimate ends the clock for the
 * rest of the course. A blank cell means "not knowable yet", never an
 * assumed pace.
 *
 * Deliberately absent: preference badges. Those score against captain-adjusted
 * values the rest of the team is not shown (see `_serialize_member`), so they
 * stay in edit mode.
 */

import { computeMetrics } from './leg-metrics.js';
import { legKey, legNumbering, LINE_NAMES, LINE_CODES } from './leg-keys.js';

function escapeHtml(value) {
    const div = document.createElement('div');
    div.textContent = value ?? '';
    return div.innerHTML;
}

function hasValue(x) {
    return x !== null && x !== undefined;
}

function formatMiles(hundredthsOfAMile) {
    if (!hasValue(hundredthsOfAMile)) return '?';
    return (hundredthsOfAMile / 100).toFixed(2);
}

function formatDuration(seconds) {
    if (!hasValue(seconds)) return null;
    const totalMinutes = Math.round(seconds / 60);
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;
    return hours > 0 ? `${hours}h ${minutes}m` : `${minutes} min`;
}

/**
 * Times are shown in the *event's* timezone, not the viewer's: someone
 * checking the schedule from elsewhere still wants the time they'll see on
 * the start line.
 *
 * The event's IANA zone isn't in the payload (the start arrives as an ISO
 * string carrying a fixed offset), so formatting is pinned to that offset by
 * shifting the instant and formatting in UTC. Keeps an October -07:00 start
 * reading as 8:30 AM for a viewer anywhere.
 */
function eventClock(course) {
    const iso = course && course.event_start_time;
    const match = iso && /([+-])(\d{2}):(\d{2})$/.exec(iso);
    const sign = match && match[1] === '-' ? -1 : 1;
    const offsetMs = match
        ? sign * (Number(match[2]) * 60 + Number(match[3])) * 60 * 1000
        : 0;
    const formatter = new Intl.DateTimeFormat(undefined, {
        hour: 'numeric',
        minute: '2-digit',
        timeZone: match ? 'UTC' : undefined,
    });
    return {
        format(epochMs) {
            if (!hasValue(epochMs)) return null;
            return formatter.format(new Date(epochMs + offsetMs));
        },
    };
}

function clockCell(epochMs, clock) {
    if (!hasValue(epochMs)) return '<span class="text-muted">—</span>';
    return escapeHtml(clock.format(epochMs));
}

// ---- The viewer's own legs -------------------------------------------------

function renderMyAssignment(mountEl, state, metrics, clock) {
    if (!mountEl) return;

    const membershipId = mountEl.dataset.membershipId || '';
    const member = (state.members || []).find(m => m.membership_id === membershipId);

    // Site admins and captains viewing a team they don't run on have no legs
    // of their own here; the schedule below is the whole page for them.
    if (!membershipId || !member) {
        mountEl.innerHTML = '';
        return;
    }

    const legs = (state.course ? state.course.legs : [])
        .filter(leg => ((state.assignments || {})[legKey(leg)] || []).includes(membershipId));

    if (legs.length === 0) {
        mountEl.innerHTML = `
            <div class="card border-secondary-subtle mb-4">
                <div class="card-body">
                    <h2 class="h5 card-title mb-1">You aren't on a leg yet</h2>
                    <p class="card-text text-muted mb-0">
                        Your captain hasn't assigned you a leg. The full schedule so far is below.
                    </p>
                </div>
            </div>`;
        return;
    }

    const totalHundredths = legs.reduce((sum, leg) => sum + leg.distance, 0);
    const scheduleByKey = metrics.schedule.byKey;

    const rows = legs.map(leg => {
        const key = legKey(leg);
        const entry = scheduleByKey[key] || {};
        const start = hasValue(entry.startMs)
            ? `<span class="fw-semibold">${escapeHtml(clock.format(entry.startMs))}</span>`
            : '<span class="text-muted">start time not known yet</span>';
        const others = ((state.assignments || {})[key] || [])
            .filter(id => id !== membershipId)
            .map(id => (state.members.find(m => m.membership_id === id) || {}).name)
            .filter(Boolean);
        const withWhom = others.length
            ? `<div class="text-muted small">with ${escapeHtml(others.join(', '))}</div>`
            : '';
        return `
            <li class="list-group-item px-0">
                <div class="d-flex flex-wrap justify-content-between gap-2">
                    <div>
                        <div class="fw-semibold">${escapeHtml(leg.start.name)} &rarr; ${escapeHtml(leg.end.name)}</div>
                        ${withWhom}
                    </div>
                    <div class="text-end small">
                        <div>${start}</div>
                        <div class="text-muted">${formatMiles(leg.distance)} mi</div>
                    </div>
                </div>
            </li>`;
    }).join('');

    mountEl.innerHTML = `
        <div class="card border-primary-subtle mb-4">
            <div class="card-body">
                <h2 class="h5 card-title">
                    Your ${legs.length === 1 ? 'leg' : `${legs.length} legs`}
                    <span class="text-muted fw-normal">&middot; ${formatMiles(totalHundredths)} mi total</span>
                </h2>
                <ul class="list-group list-group-flush">${rows}</ul>
            </div>
        </div>`;
}

// ---- The full schedule table ----------------------------------------------

/**
 * Split the course into the stretches an Interline team actually runs as
 * separate things: the 1 Line branch, the 2 Line branch, and the shared trunk
 * they both feed into (see `legs_for()` in course_service.py, which orders the
 * legs branches-first for exactly this reason).
 *
 * Grouping is by which of the *team's* lines each leg belongs to, so a
 * single-line team -- whose trunk legs are on one selected line like every
 * other leg -- comes back as one segment and renders as a plain table.
 */
function courseSegments(legs, courseLines) {
    const selected = new Set(courseLines || []);
    const signatureOf = (leg) => (leg.lines || [])
        .filter(line => selected.size === 0 || selected.has(line))
        .join('+');

    const segments = [];
    let current = null;
    for (const leg of legs) {
        const signature = signatureOf(leg);
        if (!current || current.signature !== signature) {
            current = { signature, lines: signature ? signature.split('+') : [], legs: [] };
            segments.push(current);
        }
        current.legs.push(leg);
    }

    for (const segment of segments) {
        const names = segment.lines.map(line => LINE_NAMES[line] || line);
        const shared = segment.lines.length > 1;
        segment.label = shared ? 'Shared trunk' : (names[0] || 'Course');
        segment.pill = linePill(segment.lines);
    }
    return segments;
}

/**
 * The Link-style line pill the team page uses: a coloured circle per line
 * plus the word. The trunk gets one pill carrying both circles -- "(1)(2)
 * Lines" -- since it is one stretch of course, not two.
 */
function linePill(lines) {
    const circles = lines
        .map((line) => {
            const code = LINE_CODES[line];
            if (!code) return '';
            return `<span class="line-name line-name-${escapeHtml(code)}">${escapeHtml(code)}</span>`;
        })
        .join('');
    if (!circles) return '';
    return `<span class="line-pill">${circles}${lines.length > 1 ? 'Lines' : 'Line'}</span>`;
}

/** The first leg of the segment after `segment`, if any. */
function nextLegAfter(segments, segment, indexInSegment) {
    if (indexInSegment < segment.legs.length - 1) return segment.legs[indexInSegment + 1];
    const next = segments[segments.indexOf(segment) + 1];
    return next ? next.legs[0] : null;
}

/**
 * The leg's running position, in the same numbering the board uses. A trunk
 * leg carries one number per line -- "14/13" is the 1 Line's leg 14 and the
 * 2 Line's leg 13 -- since each branch counts up to it separately.
 */
function legNumberHtml(leg, courseLines) {
    const { text, title } = legNumbering(leg, courseLines);
    if (!text) return '<span class="leg-number"></span>';
    return `<span class="leg-number" title="${escapeHtml(title)}">${escapeHtml(text)}</span>`;
}

function runnersCell(state, key, highlightMembershipId) {
    const ids = (state.assignments || {})[key] || [];
    if (ids.length === 0) {
        return '<span class="text-muted">Unassigned</span>';
    }
    return ids.map(id => {
        const member = (state.members || []).find(m => m.membership_id === id);
        const name = escapeHtml(member ? member.name : 'Unknown member');
        // The viewer's own name is underlined so they can find themselves
        // down a long schedule without the row having to shout.
        return id === highlightMembershipId ? `<u>${name}</u>` : name;
    }).join(', ');
}

function renderSchedule(mountEl, state, metrics, clock) {
    if (!mountEl) return;

    if (!state.course) {
        mountEl.innerHTML = '<p class="text-muted">Course data is unavailable.</p>';
        return;
    }

    const highlightMembershipId = mountEl.dataset.membershipId || '';
    const schedule = metrics.schedule;
    const legMetrics = metrics.legs;

    // A row is a *handoff*, not a leg: the exchange you arrive at, the time
    // you get there, and the leg that leaves from it. A leg's destination is
    // the next row's exchange, so naming it twice would just be noise -- the
    // chain is only closed out explicitly where it actually ends (below).
    const legs = state.course.legs;
    const segments = courseSegments(legs, state.course.lines);
    const rows = [];

    segments.forEach((segment) => {
        // Only an Interline course has more than one segment; a single-line
        // team gets the plain uninterrupted table.
        if (segments.length > 1) {
            rows.push(`
                <tr class="schedule-row-section">
                    <th colspan="5" scope="colgroup">${segment.pill}</th>
                </tr>`);
        }

        segment.legs.forEach((leg, index) => {
            const key = legKey(leg);
            const entry = schedule.byKey[key] || {};
            const duration = formatDuration((legMetrics[key] || {}).durationSeconds);
            const mine = ((state.assignments || {})[key] || []).includes(highlightMembershipId);

            rows.push(`
                <tr${mine ? ' class="schedule-row-mine"' : ''}>
                    <td class="text-nowrap">${clockCell(entry.startMs, clock)}</td>
                    <td>
                        ${legNumberHtml(leg, state.course.lines)}
                        <span class="fw-semibold">${escapeHtml(leg.start.name)}</span>
                    </td>
                    <td class="text-nowrap small">
                        ${formatMiles(leg.distance)} mi
                        <div class="text-muted">&uarr;${leg.ascent} &darr;${leg.descent} ft</div>
                    </td>
                    <td class="text-nowrap small">${duration ? escapeHtml(duration) : '<span class="text-muted">—</span>'}</td>
                    <td>${runnersCell(state, key, highlightMembershipId)}</td>
                </tr>`);

            // Close the chain where the next row won't be this leg's
            // destination: the finish, and the end of a branch that hands off
            // to a trunk starting elsewhere in the table.
            const next = segment.legs[index + 1] || nextLegAfter(segments, segment, index);
            if (!next || next.start.id !== leg.end.id) {
                rows.push(`
                    <tr class="schedule-row-terminus">
                        <td class="text-nowrap">${clockCell(entry.endMs, clock)}</td>
                        <td>
                            <span class="leg-number"></span>
                            <span class="fw-semibold">${escapeHtml(leg.end.name)}</span>
                        </td>
                        <td colspan="3" class="small text-muted">
                            ${next ? escapeHtml(`${segment.label} arrives`) : 'Finish'}
                        </td>
                    </tr>`);
            }
        });
    });

    mountEl.innerHTML = `
        <div class="table-responsive">
            <table class="table table-sm align-middle mb-0">
                <thead>
                    <tr>
                        <th scope="col">Time</th>
                        <th scope="col">Leg</th>
                        <th scope="col">Distance</th>
                        <th scope="col">Est.</th>
                        <th scope="col">Runners</th>
                    </tr>
                </thead>
                <tbody>${rows.join('')}</tbody>
            </table>
        </div>`;
}

// ---- Top-level render ------------------------------------------------------

export function renderLegSchedule(state) {
    const myMount = document.getElementById('legs-my-assignment');
    const scheduleMount = document.getElementById('legs-schedule');
    if (!myMount && !scheduleMount) return;

    const metrics = computeMetrics(state);
    const clock = eventClock(state.course);

    renderMyAssignment(myMount, state, metrics, clock);
    renderSchedule(scheduleMount, state, metrics, clock);
}

/**
 * Install `renderLegSchedule` as (or chained onto)
 * `window.onAssignmentsChanged`, the same convention leg-badges.js uses, so
 * the schedule repaints from edit-mode changes without either module knowing
 * about the other.
 */
export function initLegSchedule() {
    const previous = window.onAssignmentsChanged;
    window.onAssignmentsChanged = (state) => {
        if (typeof previous === 'function') previous(state);
        renderLegSchedule(state);
    };
}
