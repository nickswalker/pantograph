/**
 * Leg Assignment Badge Rendering
 *
 * DOM-side half of preference evaluation: takes the output of
 * `computeMetrics()` (app/static/js/leg-metrics.js, pure/DOM-free) and paints
 * Bootstrap-styled badges into the mount points WP3's board
 * (leg-board.js) leaves for us:
 *
 *   - `[data-metrics-mount="member"][data-membership-id]` -- one per bench
 *     chip. Gets the member's distance / end-exchange / leg-1 badges.
 *   - `[data-metrics-mount="leg"][data-leg-index]` -- one per leg row. Gets
 *     the leg's estimated-duration badge.
 *   - `#legs-team-summary` (in team_legs.html, not written by leg-board.js)
 *     -- team-level uncovered-legs-count and total-duration-vs-target.
 *
 * A dimension that computeMetrics() returned `null` for (no preference
 * stated / not applicable / "no data") renders NO badge at all -- silence,
 * not a neutral badge -- per the plan's rule that "no data" must never be
 * confused with "satisfied" or "violated".
 *
 * Badge color mapping (Bootstrap `text-bg-*`, matching the rest of the
 * board's badge usage):
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
 * so this module composes rather than clobbers. Works identically in the
 * read-only member view -- it only ever reads `state` and paints badges, no
 * `canEdit` check needed.
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

function badgeHtml(status, label, tooltip) {
    const cls = STATUS_CLASS[status] || 'text-bg-secondary';
    return `<span class="badge ${cls}" title="${escapeHtml(tooltip)}">${escapeHtml(label)}</span>`;
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

// ---- Member (bench chip) badges -----------------------------------------

function renderMemberBadges(mountEl, metrics) {
    if (!metrics) {
        mountEl.innerHTML = '';
        return;
    }

    const badges = [];

    if (metrics.distance) {
        const m = metrics.distance;
        badges.push(badgeHtml(m.status, `${m.assignedMiles.toFixed(1)} mi`, m.tooltip));
    }

    if (metrics.endExchange) {
        const m = metrics.endExchange;
        const label = m.status === 'satisfied' ? 'Exact stop' : `${m.distanceMiles.toFixed(1)} mi off`;
        badges.push(badgeHtml(m.status, label, m.tooltip));
    }

    if (metrics.leadLeg) {
        const m = metrics.leadLeg;
        badges.push(badgeHtml(m.status, 'Leg 1', m.tooltip));
    }

    mountEl.innerHTML = badges.join('');
}

// ---- Leg row badges -------------------------------------------------------

function renderLegBadge(mountEl, metrics) {
    if (!metrics || !metrics.covered || metrics.durationSeconds === null) {
        mountEl.innerHTML = '';
        return;
    }

    const tooltip = `Estimated ${formatDuration(metrics.durationSeconds)} using the slowest assigned pace `
        + `(${formatPace(metrics.paceSeconds)})`;
    mountEl.innerHTML = `<span class="badge text-bg-secondary" title="${escapeHtml(tooltip)}">`
        + `${escapeHtml(formatDuration(metrics.durationSeconds))} est</span>`;
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

    document.querySelectorAll('[data-metrics-mount="member"]').forEach((mountEl) => {
        renderMemberBadges(mountEl, memberMetrics[mountEl.dataset.membershipId]);
    });

    document.querySelectorAll('[data-metrics-mount="leg"]').forEach((mountEl) => {
        renderLegBadge(mountEl, legMetrics[mountEl.dataset.legIndex]);
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
