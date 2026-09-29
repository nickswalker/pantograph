/**
 * Leg Assignment Badge Rendering
 *
 * DOM-side half of preference evaluation: paints `computeMetrics()` output
 * (leg-metrics.js) into the mount points leg-board.js leaves behind --
 * `[data-metrics-mount="member"]` per bench chip (a compact assigned-vs-wanted
 * summary, plus the captain's "edit preferences" button),
 * `[data-metrics-mount="leg"]` in each leg row's stats line (estimated duration), and
 * `#legs-team-summary` from team_legs.html (total vs target).
 *
 * Call `initLegBadges()` once, before the board loads. It installs itself as
 * `window.onAssignmentsChanged`, chaining any existing handler so it composes
 * rather than clobbers. `state.canEdit` is the only thing it reads to tell the
 * captain view from the read-only one; it never imports leg-board.js.
 */

import { computeMetrics } from './leg-metrics.js';
import { eventClock } from './leg-schedule.js';

const STATUS_CLASS = {
    satisfied: 'text-bg-success',
    near: 'text-bg-warning',
    violated: 'text-bg-danger',
};

function escapeHtml(value) {
    const div = document.createElement('div');
    div.textContent = value ?? '';
    return div.innerHTML;
}

function hasValue(x) {
    return x !== null && x !== undefined;
}

/**
 * `overridden` marks a badge scored against a captain-adjusted preference
 * rather than the member's own answer, with a pencil. Without it, a green
 * badge would read as "the member got what they asked for" when what it
 * actually means is "the member got what the captain decided" -- the
 * tooltip (built in leg-metrics.js) spells out the original value.
 */
function badgeHtml(status, label, tooltip, overridden = false) {
    const cls = STATUS_CLASS[status] || 'text-bg-secondary';
    const marker = overridden
        ? '<ion-icon name="create-outline" class="ms-1" aria-hidden="true"></ion-icon>'
        : '';
    return `<span class="badge ${cls}" title="${escapeHtml(tooltip)}">${escapeHtml(label)}${marker}</span>`;
}

function formatDuration(seconds) {
    const totalMinutes = Math.round(seconds / 60);
    const hours = Math.floor(totalMinutes / 60);
    const minutes = totalMinutes % 60;
    return hours > 0 ? `${hours}:${String(minutes).padStart(2, '0')}` : `${minutes} min`;
}

function formatPace(secondsPerMile) {
    const minutes = Math.floor(secondsPerMile / 60);
    const seconds = Math.round(secondsPerMile % 60);
    return `${minutes}:${String(seconds).padStart(2, '0')}/mi`;
}

// ---- Member (bench chip) summary --------------------------------------------

/**
 * Button that opens the override dialog for this member, sitting right next
 * to the "Wants" it edits. Captain/admin view only -- `leg-board.js` binds
 * the click (delegated on the bench container, since this table gets
 * repainted independently of the chip around it) via the same
 * `.bench-override-btn` convention the button used when it lived next to
 * the member's name.
 */
function editWantsButton(member, canEdit) {
    if (!canEdit) return '';
    return `<button type="button" class="btn btn-sm btn-link link-secondary p-0 ms-1 bench-override-btn"
                data-membership-id="${escapeHtml(member.membership_id)}"
                title="Adjust the preferences used for ${escapeHtml(member.name)}"
                aria-label="Adjust preferences for ${escapeHtml(member.name)}">
                <ion-icon name="create-outline"></ion-icon>
            </button>`;
}

/**
 * What the member has against what they asked for (their effective
 * preference -- overrides already applied), in at most two plain lines:
 *
 *   2 legs · 6.4 of 5.0 mi · ends Star Lake · runs solo ✎
 *   wants Angle Lake · slowed to 11:20/mi
 *
 * The second line appears only when the end station or pace differs from
 * what they want. Hovering the first line shows the full stated wants.
 */
function renderMemberSummary(mountEl, member, metrics, canEdit) {
    if (!member || !metrics) {
        mountEl.innerHTML = '';
        return;
    }

    const current = metrics.current;
    const wantsMiles = hasValue(member.preferred_miles) ? Number(member.preferred_miles).toFixed(1) : null;
    const wantsPace = hasValue(member.planned_pace_seconds) ? formatPace(member.planned_pace_seconds) : null;
    const wantsEnd = member.preferred_station || null;

    const main = [`${current.legCount} leg${current.legCount === 1 ? '' : 's'}`];
    const notes = [];
    if (current.legCount) {
        const miles = current.assignedMiles.toFixed(1);
        main.push(wantsMiles ? `${miles} of ${wantsMiles} mi` : `${miles} mi`);
        if (current.endExchangeName) main.push(`ends ${current.endExchangeName}`);
        if (wantsEnd && wantsEnd !== current.endExchangeName) notes.push(`wants ${wantsEnd}`);
        if (wantsPace && hasValue(current.paceSeconds)
                && current.paceSeconds > member.planned_pace_seconds
                && formatPace(current.paceSeconds) !== wantsPace) {
            notes.push(`slowed to ${formatPace(current.paceSeconds)}`);
        }
    } else {
        if (wantsMiles) main.push(`wants ${wantsMiles} mi`);
        if (wantsEnd) main.push(`ends ${wantsEnd}`);
    }
    if (member.willing_to_lead) main.push('runs solo');

    const tooltip = 'Wants: ' + [
        wantsMiles ? `${wantsMiles} mi` : 'any distance',
        wantsPace || 'no pace',
        wantsEnd ? `ends ${wantsEnd}` : 'any end station',
    ].join(', ');

    mountEl.innerHTML = `
        <div class="text-muted" title="${escapeHtml(tooltip)}">${escapeHtml(main.join(' · '))}${editWantsButton(member, canEdit)}</div>
        ${notes.length ? `<div class="text-muted">${escapeHtml(notes.join(' · '))}</div>` : ''}`;
}

// ---- Leg row duration -------------------------------------------------------

/**
 * Estimated duration, appended to the leg's distance/elevation line as plain
 * text rather than a badge -- it's a fact about the leg, not a scored
 * preference. The tooltip adds the time-of-day range when the schedule can
 * place the leg (see legScheduleMetrics for when it can't).
 */
function renderLegDuration(mountEl, metrics, scheduleEntry, clock) {
    if (!metrics || !metrics.covered || metrics.durationSeconds === null) {
        mountEl.innerHTML = '';
        return;
    }

    const duration = formatDuration(metrics.durationSeconds);
    const lines = [
        `Estimated ${duration} using the slowest assigned pace (${formatPace(metrics.paceSeconds)})`
            + (metrics.paceOverridden ? ', which the captain adjusted' : ''),
    ];
    if (scheduleEntry && hasValue(scheduleEntry.startMs) && hasValue(scheduleEntry.endMs)) {
        lines.push(`${clock.format(scheduleEntry.startMs)} – ${clock.format(scheduleEntry.endMs)}`);
    }
    const marker = metrics.paceOverridden
        ? '<ion-icon name="create-outline" class="ms-1" aria-hidden="true"></ion-icon>'
        : '';
    mountEl.innerHTML = `<span class="ms-2" title="${escapeHtml(lines.join('\n'))}">~${escapeHtml(duration)}${marker}</span>`;
}

// ---- Team summary ----------------------------------------------------------

function renderTeamSummary(mountEl, team) {
    if (!mountEl) return;
    if (!team) {
        mountEl.innerHTML = '';
        return;
    }

    const parts = [];

    if (team.status !== 'no data') {
        const totalLabel = formatDuration(team.totalEstimatedDurationSeconds);
        const targetLabel = formatDuration(team.teamEstimatedDurationSeconds);
        parts.push(badgeHtml(
            team.status,
            `${totalLabel} total (target ${targetLabel})`,
            `Estimated total ${totalLabel}, team's stated estimate is ${targetLabel}`,
        ));
    } else if (team.uncoveredLegsCount === 0 && team.legsMissingPaceCount > 0) {
        parts.push(`<span class="badge text-bg-light text-muted" title="Some assigned runners haven't stated a pace, `
            + `so the team total can't be estimated yet">Pace data incomplete</span>`);
    }

    mountEl.innerHTML = parts.join(' ');
}

// ---- Top-level render ------------------------------------------------------

/** Recompute metrics for `state` and repaint every badge mount in the DOM. */
export function renderLegBadges(state) {
    const { members: memberMetrics, legs: legMetrics, team, schedule } = computeMetrics(state);
    const clock = eventClock(state.course);
    const membersById = new Map((state.members || []).map(m => [m.membership_id, m]));

    document.querySelectorAll('[data-metrics-mount="member"]').forEach((mountEl) => {
        const membershipId = mountEl.dataset.membershipId;
        renderMemberSummary(mountEl, membersById.get(membershipId), memberMetrics[membershipId], !!state.canEdit);
    });

    document.querySelectorAll('[data-metrics-mount="leg"]').forEach((mountEl) => {
        const key = mountEl.dataset.legKey;
        renderLegDuration(mountEl, legMetrics[key], schedule.byKey[key], clock);
    });

    renderTeamSummary(document.getElementById('legs-team-summary'), team);
}

/**
 * Install `renderLegBadges` as (or chained onto) `window.onAssignmentsChanged`.
 * Call this before `initLegBoard()` so the very first `_notifyChanged()`
 * (fired at the end of the board's initial load) already paints badges.
 */
export function initLegBadges() {
    const previous = window.onAssignmentsChanged;
    window.onAssignmentsChanged = (state) => {
        if (typeof previous === 'function') previous(state);
        renderLegBadges(state);
    };
}
