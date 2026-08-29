/**
 * Leg Assignment Badge Rendering
 *
 * DOM-side half of preference evaluation: paints `computeMetrics()` output
 * (leg-metrics.js) into the mount points leg-board.js leaves behind --
 * `[data-metrics-mount="member"]` per bench chip (an uncolored wants-vs-current
 * table, plus the captain's "edit preferences" button),
 * `[data-metrics-mount="leg"]` per leg row (estimated duration), and
 * `#legs-team-summary` from team_legs.html (total vs target).
 *
 *   - `[data-metrics-mount="member"][data-membership-id]` -- one per bench
 *     chip. Gets a plain wants-vs-current table (miles / pace / end) --
 *     no color coding, just the two numbers side by side -- plus, in the
 *     captain/admin view, the "edit preferences" button next to "Wants"
 *     (leg-board.js binds its click; see `state.canEdit` below).
 *   - `[data-metrics-mount="leg"][data-leg-key]` -- one per leg row. Gets
 *     the leg's estimated-duration badge.
 *   - `#legs-team-summary` (in team_legs.html, not written by leg-board.js)
 *     -- team-level uncovered-legs-count and total-duration-vs-target.
 *
 * Badge color mapping (Bootstrap `text-bg-*`, matching the rest of the
 * board's badge usage -- leg and team-summary badges only; the member table
 * is deliberately uncolored):
 *   satisfied -> text-bg-success   near -> text-bg-warning
 *   violated  -> text-bg-danger
 *
 * Every badge carries a plain-language `title` tooltip (the board's existing
 * convention -- see leg-board.js's status/leader badges -- native browser
 * tooltip, no Bootstrap JS tooltip component needed).
 *
 * Wiring: call `initLegBadges()` once (from team_legs.html, alongside
 * `initLegBoard`) *before* the board starts loading. It installs itself as
 * `window.onAssignmentsChanged`, chaining any previously-installed handler
 * so this module composes rather than clobbers. `state.canEdit` (set by
 * leg-board.js's `_notifyChanged()`) is the one thing this module reads to
 * tell the captain/admin view from the read-only member view -- it doesn't
 * import leg-board.js or know anything else about it.
 */

import { computeMetrics } from './leg-metrics.js';

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

// ---- Member (bench chip) wants/current table -------------------------------

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
 * Two-row wants-vs-current table: what the member asked for (their
 * effective preference -- overrides already applied) against what their
 * assignments actually add up to right now. Plain values, no satisfied/
 * near/violated coloring.
 */
function renderMemberTable(mountEl, member, metrics, canEdit) {
    if (!member || !metrics) {
        mountEl.innerHTML = '';
        return;
    }

    const current = metrics.current;

    const milesWant = hasValue(member.preferred_miles)
        ? `${Number(member.preferred_miles).toFixed(1)} mi` : 'no preference';
    const paceWant = hasValue(member.planned_pace_seconds)
        ? formatPace(member.planned_pace_seconds) : 'no preference';
    const endWant = member.preferred_station || 'no preference';

    const milesCurrent = current.legCount ? `${current.assignedMiles.toFixed(1)} mi` : '—';
    const paceCurrent = hasValue(current.paceSeconds) ? formatPace(current.paceSeconds) : '—';
    const endCurrent = current.endExchangeName || '—';

    mountEl.innerHTML = `
        <table class="table table-sm table-borderless mb-0 metrics-table">
            <tbody>
                <tr>
                    <th scope="row" class="text-muted fw-normal">Wants${editWantsButton(member, canEdit)}</th>
                    <td>${escapeHtml(milesWant)}</td>
                    <td>${escapeHtml(paceWant)}</td>
                    <td>${escapeHtml(endWant)}</td>
                </tr>
                <tr>
                    <th scope="row" class="text-muted fw-normal">Current</th>
                    <td>${escapeHtml(milesCurrent)}</td>
                    <td>${escapeHtml(paceCurrent)}</td>
                    <td>${escapeHtml(endCurrent)}</td>
                </tr>
            </tbody>
        </table>`;
}

// ---- Leg row badges -------------------------------------------------------

function renderLegBadge(mountEl, metrics) {
    if (!metrics || !metrics.covered || metrics.durationSeconds === null) {
        mountEl.innerHTML = '';
        return;
    }

    const tooltip = `Estimated ${formatDuration(metrics.durationSeconds)} using the slowest assigned pace `
        + `(${formatPace(metrics.paceSeconds)})`
        + (metrics.paceOverridden ? ', which the captain adjusted' : '');
    // No status: an estimated duration isn't a satisfied/violated preference,
    // so this badge stays neutral (badgeHtml's text-bg-secondary fallback).
    mountEl.innerHTML = badgeHtml(
        null,
        `${formatDuration(metrics.durationSeconds)} est`,
        tooltip,
        metrics.paceOverridden,
    );
}

// ---- Team summary ----------------------------------------------------------

function renderTeamSummary(mountEl, team) {
    if (!mountEl) return;
    if (!team) {
        mountEl.innerHTML = '';
        return;
    }

    const parts = [];

    const uncoveredStatus = team.uncoveredLegsCount === 0 ? 'satisfied' : 'violated';
    const uncoveredLabel = team.uncoveredLegsCount === 0
        ? 'All legs covered'
        : `${team.uncoveredLegsCount} leg${team.uncoveredLegsCount === 1 ? '' : 's'} uncovered`;
    parts.push(badgeHtml(
        uncoveredStatus,
        uncoveredLabel,
        team.uncoveredLegsCount === 0
            ? 'Every leg has at least one runner assigned'
            : `${team.uncoveredLegsCount} leg(s) have nobody assigned yet`,
    ));

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
    const { members: memberMetrics, legs: legMetrics, team } = computeMetrics(state);
    const membersById = new Map((state.members || []).map(m => [m.membership_id, m]));

    document.querySelectorAll('[data-metrics-mount="member"]').forEach((mountEl) => {
        const membershipId = mountEl.dataset.membershipId;
        renderMemberTable(mountEl, membersById.get(membershipId), memberMetrics[membershipId], !!state.canEdit);
    });

    document.querySelectorAll('[data-metrics-mount="leg"]').forEach((mountEl) => {
        renderLegBadge(mountEl, legMetrics[mountEl.dataset.legKey]);
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
