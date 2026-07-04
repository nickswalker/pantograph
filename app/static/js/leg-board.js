/**
 * Leg Assignment Board (WP3)
 *
 * Renders the drag-and-drop board at /team/<team_id>/legs: one row per leg
 * of the course with a drop zone that can hold any number of member chips,
 * and a "bench" listing every team member (with a count of legs they hold).
 *
 * Drag-and-drop uses SortableJS (touch-friendly out of the box). Every drag
 * affordance has a keyboard/pointer-free equivalent: each bench chip has an
 * "Add to leg" dropdown (native <button data-bs-toggle="dropdown"> + menu,
 * fully keyboard operable), and every placed chip has a remove button.
 *
 * No solver here (WP5) and no preference-badge *computation* (WP4) -- this
 * module only knows how to load, render, mutate, and save the assignment
 * set. It does expose a hook, `window.onAssignmentsChanged(state)`, called
 * after every change (including initial load), so WP4 can render live
 * satisfaction badges without this module knowing anything about
 * preferences.
 *
 * The one WP4 concession here: every bench chip and leg row renders an empty
 * `[data-metrics-mount]` element (a bench chip per member, a leg row per
 * leg). WP3 never writes into them; WP4's renderer (leg-badges.js) fills
 * them in on the `onAssignmentsChanged` hook, which fires after this
 * module's own `_render()` has already replaced the relevant innerHTML, so
 * there's no race between the two.
 */

import { assignmentAPI, api } from './api-client.js';
import Sortable from 'sortablejs';

// ---- Formatting helpers -----------------------------------------------

function formatMiles(hundredthsOfAMile) {
    if (hundredthsOfAMile === null || hundredthsOfAMile === undefined) return '?';
    return (hundredthsOfAMile / 100).toFixed(2);
}

function formatPace(secondsPerMile) {
    if (!secondsPerMile && secondsPerMile !== 0) return null;
    const minutes = Math.floor(secondsPerMile / 60);
    const seconds = secondsPerMile % 60;
    return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

function escapeHtml(value) {
    const div = document.createElement('div');
    div.textContent = value ?? '';
    return div.innerHTML;
}

// ---- Board -------------------------------------------------------------

export class LegBoard {
    /**
     * @param {Object} opts
     * @param {string} opts.teamId
     * @param {boolean} opts.canEdit
     * @param {HTMLElement} opts.legsListEl - container the leg rows render into
     * @param {HTMLElement} opts.benchEl - container the bench chips render into
     * @param {HTMLElement} [opts.saveButtonEl]
     * @param {HTMLElement} [opts.dirtyIndicatorEl]
     * @param {HTMLElement} [opts.savedIndicatorEl]
     * @param {HTMLElement} [opts.loadingEl]
     */
    constructor(opts) {
        this.teamId = opts.teamId;
        this.canEdit = !!opts.canEdit;
        this.legsListEl = opts.legsListEl;
        this.benchEl = opts.benchEl;
        this.saveButtonEl = opts.saveButtonEl || null;
        this.dirtyIndicatorEl = opts.dirtyIndicatorEl || null;
        this.savedIndicatorEl = opts.savedIndicatorEl || null;
        this.loadingEl = opts.loadingEl || null;

        this.state = { course: null, members: [], assignments: {} };
        this.dirty = false;
        this._sortables = [];

        if (this.saveButtonEl) {
            this.saveButtonEl.addEventListener('click', () => this.save());
        }

        window.addEventListener('beforeunload', (e) => {
            if (this.dirty) {
                e.preventDefault();
                e.returnValue = '';
            }
        });
    }

    async load() {
        this._setLoading(true);
        const result = await assignmentAPI.getBoard(this.teamId);
        this._setLoading(false);

        if (!result.success) {
            api.showBanner(result.error || 'Failed to load the leg assignment board.', 'danger');
            return;
        }

        const data = result.data;
        this.state.course = data.course;
        this.state.members = data.members;
        this.state.assignments = this._groupAssignments(data.course, data.assignments);
        this._setDirty(false);
        this._render();
        this._notifyChanged();
    }

    async save() {
        if (!this.canEdit) return;

        const payload = [];
        for (const [legIndexStr, membershipIds] of Object.entries(this.state.assignments)) {
            const legIndex = Number(legIndexStr);
            for (const membershipId of membershipIds) {
                payload.push({ leg_index: legIndex, membership_id: membershipId });
            }
        }

        if (this.saveButtonEl) api.setButtonLoading(this.saveButtonEl, 'Saving...');
        const result = await assignmentAPI.saveAssignments(this.teamId, payload);
        if (this.saveButtonEl) api.resetButton(this.saveButtonEl);

        if (!result.success) {
            // Surface the failure and keep local state intact -- never
            // silently drop unsaved changes.
            api.showBanner(result.error || 'Failed to save leg assignments.', 'danger');
            return;
        }

        this._setDirty(false);
        api.showBanner('Leg assignments saved.', 'success');
    }

    // ---- State mutation ----

    _groupAssignments(course, assignmentsList) {
        const grouped = {};
        if (course) {
            for (const leg of course.legs) {
                grouped[leg.index] = [];
            }
        }
        for (const a of assignmentsList) {
            if (!grouped[a.leg_index]) grouped[a.leg_index] = [];
            grouped[a.leg_index].push(a.membership_id);
        }
        return grouped;
    }

    addAssignment(legIndex, membershipId) {
        const key = String(legIndex);
        const current = this.state.assignments[key] || [];
        if (current.includes(membershipId)) {
            api.showBanner('That member is already assigned to this leg.', 'warning');
            return;
        }
        this.state.assignments[key] = [...current, membershipId];
        this._setDirty(true);
        this._render();
        this._notifyChanged();
    }

    removeAssignment(legIndex, membershipId) {
        const key = String(legIndex);
        const current = this.state.assignments[key] || [];
        this.state.assignments[key] = current.filter(id => id !== membershipId);
        this._setDirty(true);
        this._render();
        this._notifyChanged();
    }

    _legsHeldBy(membershipId) {
        return Object.values(this.state.assignments)
            .filter(ids => ids.includes(membershipId)).length;
    }

    // ---- Rendering ----

    _setLoading(isLoading) {
        if (this.loadingEl) this.loadingEl.classList.toggle('d-none', !isLoading);
    }

    _setDirty(isDirty) {
        this.dirty = isDirty;
        if (this.saveButtonEl) this.saveButtonEl.disabled = !isDirty;
        if (this.dirtyIndicatorEl) this.dirtyIndicatorEl.classList.toggle('d-none', !isDirty);
        if (this.savedIndicatorEl) this.savedIndicatorEl.classList.toggle('d-none', isDirty);
    }

    _notifyChanged() {
        if (typeof window.onAssignmentsChanged === 'function') {
            window.onAssignmentsChanged({
                course: this.state.course,
                members: this.state.members,
                assignments: this.state.assignments,
            });
        }
    }

    _render() {
        this._destroySortables();
        this._renderBench();
        this._renderLegs();
        if (this.canEdit) this._wireSortable();
    }

    _memberById(membershipId) {
        return this.state.members.find(m => m.membership_id === membershipId);
    }

    _chipAvatar(member) {
        if (member.avatar_url) {
            return `<img src="${escapeHtml(member.avatar_url)}" alt="" class="rounded-circle flex-shrink-0" width="32" height="32">`;
        }
        return '<ion-icon name="person-circle-outline" class="flex-shrink-0" style="font-size: 32px;"></ion-icon>';
    }

    _statusBadge(member) {
        if (member.status === 'active') return '';
        const label = member.status === 'withdrawn' ? 'Withdrawn' : 'Removed';
        return `<span class="badge text-bg-warning ms-1" title="This member is no longer active; resolve before saving.">${label}</span>`;
    }

    _renderBench() {
        if (!this.benchEl) return;

        if (this.state.members.length === 0) {
            this.benchEl.innerHTML = '<p class="text-muted small mb-0">No team members yet.</p>';
            return;
        }

        const legs = this.state.course ? this.state.course.legs : [];

        this.benchEl.innerHTML = this.state.members.map(member => {
            const legsHeld = this._legsHeldBy(member.membership_id);
            const pace = formatPace(member.planned_pace_seconds);
            const details = [];
            if (member.preferred_miles !== null) details.push(`${member.preferred_miles} mi wanted`);
            if (pace) details.push(`${pace}/mi`);
            if (member.preferred_station) details.push(`&rarr; ${escapeHtml(member.preferred_station)}`);

            const leaderMarker = member.willing_to_lead
                ? '<ion-icon name="flag-outline" class="text-info ms-1" title="Willing to lead (leg 1)"></ion-icon>'
                : '';

            let addControl = '';
            if (this.canEdit && member.status === 'active' && legs.length) {
                const menuItems = legs.map(leg => {
                    const held = (this.state.assignments[leg.index] || []).includes(member.membership_id);
                    return `<li><button type="button" class="dropdown-item bench-add-item d-flex align-items-center justify-content-between"
                                data-membership-id="${escapeHtml(member.membership_id)}" data-leg-index="${leg.index}">
                                <span>Leg ${leg.index + 1}: ${escapeHtml(leg.start.name)} &rarr; ${escapeHtml(leg.end.name)}</span>
                                ${held ? '<ion-icon name="checkmark-outline" class="ms-2"></ion-icon>' : ''}
                            </button></li>`;
                }).join('');
                addControl = `
                    <div class="dropdown flex-shrink-0">
                        <button class="btn btn-sm btn-outline-primary" type="button" data-bs-toggle="dropdown" aria-expanded="false"
                                aria-label="Add ${escapeHtml(member.name)} to a leg">
                            <ion-icon name="add-outline"></ion-icon>
                        </button>
                        <ul class="dropdown-menu dropdown-menu-end" style="max-height: 320px; overflow-y: auto;">${menuItems}</ul>
                    </div>`;
            }

            return `
                <div class="d-flex align-items-center gap-2 border rounded p-2 bg-body-tertiary bench-chip${this.canEdit && member.status === 'active' ? ' bench-chip-draggable' : ''}"
                     data-membership-id="${escapeHtml(member.membership_id)}">
                    ${this._chipAvatar(member)}
                    <div class="flex-grow-1 small">
                        <div class="fw-semibold">${escapeHtml(member.name)}${leaderMarker}${this._statusBadge(member)}</div>
                        <div class="text-muted">${details.join(' &middot; ') || 'No preferences stated'}</div>
                        <div class="text-muted">${legsHeld} leg${legsHeld === 1 ? '' : 's'} assigned</div>
                        <div class="d-flex flex-wrap gap-1 mt-1" data-metrics-mount="member" data-membership-id="${escapeHtml(member.membership_id)}"></div>
                    </div>
                    ${addControl}
                </div>`;
        }).join('');

        this.benchEl.querySelectorAll('.bench-add-item').forEach(btn => {
            btn.addEventListener('click', () => {
                const membershipId = btn.dataset.membershipId;
                const legIndex = Number(btn.dataset.legIndex);
                const held = (this.state.assignments[legIndex] || []).includes(membershipId);
                if (held) {
                    this.removeAssignment(legIndex, membershipId);
                } else {
                    this.addAssignment(legIndex, membershipId);
                }
            });
        });
    }

    _renderLegChip(membershipId, legIndex) {
        const member = this._memberById(membershipId);
        if (!member) {
            // Assignment references a member no longer visible to us (shouldn't
            // normally happen -- get_board() includes any assigned non-active
            // member -- but don't blow up the render if it does).
            return `<span class="badge text-bg-secondary">Unknown member</span>`;
        }
        const removeBtn = this.canEdit
            ? `<button type="button" class="btn-close ms-1" aria-label="Remove ${escapeHtml(member.name)} from leg ${legIndex + 1}"
                   data-membership-id="${escapeHtml(membershipId)}" data-leg-index="${legIndex}"></button>`
            : '';
        return `
            <div class="d-flex align-items-center gap-1 border rounded-pill ps-1 pe-2 py-1 bg-body-tertiary leg-chip"
                 data-membership-id="${escapeHtml(membershipId)}">
                ${this._chipAvatar(member)}
                <span class="small">${escapeHtml(member.name)}${this._statusBadge(member)}</span>
                ${removeBtn}
            </div>`;
    }

    _renderLegs() {
        if (!this.legsListEl) return;

        if (!this.state.course) {
            this.legsListEl.innerHTML = '<p class="text-muted">Course data is unavailable.</p>';
            return;
        }

        this.legsListEl.innerHTML = this.state.course.legs.map(leg => {
            const assigned = this.state.assignments[leg.index] || [];
            const chips = assigned.map(id => this._renderLegChip(id, leg.index)).join('');
            const emptyHint = assigned.length === 0
                ? `<span class="text-muted small drop-hint">${this.canEdit ? 'Drop runners here, or use their "Add to leg" menu' : 'Unassigned'}</span>`
                : '';

            return `
                <div class="list-group-item">
                    <div class="d-flex flex-wrap align-items-baseline justify-content-between gap-2 mb-2">
                        <div>
                            <span class="badge text-bg-secondary me-2">Leg ${leg.index + 1}</span>
                            <strong>${escapeHtml(leg.start.name)}</strong> &rarr; <strong>${escapeHtml(leg.end.name)}</strong>
                        </div>
                        <div class="small text-muted">
                            ${formatMiles(leg.distance)} mi &middot; +${leg.ascent} / -${leg.descent} ft
                        </div>
                    </div>
                    <div class="d-flex flex-wrap gap-1 mb-2" data-metrics-mount="leg" data-leg-index="${leg.index}"></div>
                    <div class="leg-dropzone d-flex flex-wrap align-items-center gap-2 p-2 border border-dashed rounded"
                         data-leg-index="${leg.index}">
                        ${chips}${emptyHint}
                    </div>
                </div>`;
        }).join('');

        this.legsListEl.querySelectorAll('.btn-close[data-membership-id]').forEach(btn => {
            btn.addEventListener('click', () => {
                this.removeAssignment(Number(btn.dataset.legIndex), btn.dataset.membershipId);
            });
        });
    }

    // ---- Drag and drop ----

    _destroySortables() {
        this._sortables.forEach(s => s.destroy());
        this._sortables = [];
    }

    _wireSortable() {
        if (this.benchEl) {
            this._sortables.push(new Sortable(this.benchEl, {
                group: { name: 'leg-assignments', pull: 'clone', put: false },
                sort: false,
                animation: 150,
                draggable: '.bench-chip-draggable',
                filter: '.dropdown, .dropdown *',
                preventOnFilter: false,
            }));
        }

        this.legsListEl.querySelectorAll('.leg-dropzone').forEach(zoneEl => {
            this._sortables.push(new Sortable(zoneEl, {
                group: { name: 'leg-assignments', pull: false, put: true },
                animation: 150,
                onAdd: (evt) => {
                    const membershipId = evt.item.dataset.membershipId;
                    const legIndex = Number(evt.to.dataset.legIndex);
                    // Sortable already inserted a raw clone of the bench chip;
                    // discard it -- state + _render() is the single source of
                    // truth for what a leg row looks like.
                    evt.item.remove();
                    if (membershipId) this.addAssignment(legIndex, membershipId);
                },
            }));
        });
    }
}

export function initLegBoard(opts) {
    const board = new LegBoard(opts);
    board.load();
    return board;
}
