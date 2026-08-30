/**
 * Leg Assignment Board
 *
 * Renders the drag-and-drop board at /team/<team_id>/legs: one row per leg
 * of the course with a drop zone that can hold any number of member chips,
 * and a roster listing every team member.
 */

import { assignmentAPI, api } from './api-client.js';
import Sortable from 'sortablejs';
import { Modal } from 'bootstrap';
import { legKey, assignmentKey, parseLegKey, legLabel, legNumbering } from './leg-keys.js';

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

/** MM:SS -> seconds per mile, or null if it isn't a well-formed pace. */
function parsePace(text) {
    const match = /^(\d{1,2}):(\d{2})$/.exec((text || '').trim());
    if (!match) return null;
    const seconds = Number(match[1]) * 60 + Number(match[2]);
    return Number(match[2]) < 60 ? seconds : null;
}

function escapeHtml(value) {
    const div = document.createElement('div');
    div.textContent = value ?? '';
    return div.innerHTML;
}

/**
 * Display names for the preference fields a captain may override,
 * used by the stale-override warning below. Mirrors `OVERRIDABLE_FIELDS` in
 * app/services/preference_service.py.
 */
const OVERRIDE_FIELD_LABELS = {
    preferred_miles: 'preferred miles',
    planned_pace_seconds: 'pace',
    preferred_station: 'end station',
    willing_to_lead: 'willingness to run alone',
};

// ---- Board -------------------------------------------------------------

export class LegBoard {
    /**
     * @param {Object} opts
     * @param {string} opts.teamId
     * @param {boolean} opts.canEdit
     * @param {HTMLElement} opts.legsListEl - container the leg rows render into
     * @param {HTMLElement} opts.benchEl - container the bench chips render into
     * @param {HTMLElement} [opts.saveButtonEl]
     * @param {HTMLElement} [opts.loadingEl]
     * @param {HTMLElement} [opts.overrideModalEl] - captain-only dialog for
     *   adjusting the preferences a member is scored/solved against;
     *   omitted in the read-only view, which hides the affordance entirely
     */
    constructor(opts) {
        this.teamId = opts.teamId;
        this.canEdit = !!opts.canEdit;
        this.legsListEl = opts.legsListEl;
        this.benchEl = opts.benchEl;
        this.saveButtonEl = opts.saveButtonEl || null;
        this.loadingEl = opts.loadingEl || null;
        this.overrideModalEl = (this.canEdit && opts.overrideModalEl) || null;

        this.state = { course: null, members: [], assignments: {} };
        this.dirty = false;
        this._sortables = [];

        if (this.saveButtonEl) {
            this.saveButtonEl.addEventListener('click', () => this.save());
        }

        if (this.overrideModalEl) {
            const submitBtn = this.overrideModalEl.querySelector('[data-override-submit]');
            const resetBtn = this.overrideModalEl.querySelector('[data-override-reset]');
            if (submitBtn) submitBtn.addEventListener('click', () => this._submitOverrides());
            if (resetBtn) resetBtn.addEventListener('click', () => this._clearOverrides());
        }

        // Delegated (bound once, on the container) rather than bound per
        // button on every render: the "edit preferences" button itself now
        // lives in the wants/current table, which leg-badges.js repaints on
        // its own schedule (after every _notifyChanged(), not just after
        // _renderBench()) -- a direct binding would go stale the first time
        // that repaint replaced the button out from under it.
        if (this.benchEl) {
            this.benchEl.addEventListener('click', (e) => {
                const btn = e.target.closest('.bench-override-btn');
                if (btn) this._openOverrideModal(btn.dataset.membershipId);
            });
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
        for (const [key, membershipIds] of Object.entries(this.state.assignments)) {
            const leg = parseLegKey(key);
            for (const membershipId of membershipIds) {
                payload.push({ ...leg, membership_id: membershipId });
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
                grouped[legKey(leg)] = [];
            }
        }
        for (const a of assignmentsList) {
            const key = assignmentKey(a);
            if (!grouped[key]) grouped[key] = [];
            grouped[key].push(a.membership_id);
        }
        return grouped;
    }

    addAssignment(key, membershipId) {
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

    removeAssignment(key, membershipId) {
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
    }

    _notifyChanged() {
        if (typeof window.onAssignmentsChanged === 'function') {
            window.onAssignmentsChanged({
                course: this.state.course,
                members: this.state.members,
                assignments: this.state.assignments,
                // So leg-badges.js knows whether to draw the "edit
                // preferences" button in the wants/current table -- it has
                // no other way to tell the captain/admin view from the
                // read-only member view.
                canEdit: this.canEdit && !!this.overrideModalEl,
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

    // ---- Captain overrides of stated preferences ----
    //
    // The pencil marker next to an adjusted "wants" value lives in the
    // wants/current table now (leg-badges.js), alongside the metrics it's
    // scored against. This section keeps only what's still leg-board.js's:
    // the stale-override warning, which doesn't depend on any metric.

    _staleOverrideWarning(member) {
        const stale = member.stale_override_fields || [];
        if (!stale.length) return '';
        const names = stale.map(field => OVERRIDE_FIELD_LABELS[field]).join(', ');
        return `<div class="text-warning-emphasis" title="The captain's adjustment still applies, but it was made against an answer the member has since changed.">
                    <ion-icon name="alert-circle-outline"></ion-icon>
                    Member changed their ${escapeHtml(names)} since it was adjusted
                </div>`;
    }

    /** Prefill and open the override dialog for one member. */
    _openOverrideModal(membershipId) {
        const member = this._memberById(membershipId);
        if (!member || !this.overrideModalEl) return;

        const stated = member.stated || {};
        const overrides = member.overrides || {};
        const el = this.overrideModalEl;
        const field = name => el.querySelector(`[data-override-field="${name}"]`);
        // "Use stated" checkboxes and value inputs are separate controls, so
        // that "no preference" (an explicit null override) stays expressible
        // -- an empty input alone can't tell the two apart.
        const set = (name, value, statedValue, formatStated) => {
            const input = field(name);
            const clear = el.querySelector(`[data-override-clear="${name}"]`);
            const help = el.querySelector(`[data-override-stated="${name}"]`);
            const overridden = Object.prototype.hasOwnProperty.call(overrides, name);
            input.value = value === null || value === undefined ? '' : value;
            if (clear) clear.checked = overridden && overrides[name] === null;
            if (help) {
                help.textContent = statedValue === null || statedValue === undefined
                    ? 'Member stated no preference'
                    : `Member stated ${formatStated(statedValue)}`;
            }
        };

        el.dataset.membershipId = membershipId;
        const nameEl = el.querySelector('[data-override-member-name]');
        if (nameEl) nameEl.textContent = member.name;

        set('preferred_miles',
            overrides.preferred_miles ?? stated.preferred_miles,
            stated.preferred_miles, v => `${v} mi`);
        set('planned_pace_seconds',
            formatPace(overrides.planned_pace_seconds ?? stated.planned_pace_seconds),
            stated.planned_pace_seconds, v => `${formatPace(v)}/mi`);
        set('preferred_station',
            overrides.preferred_station ?? stated.preferred_station ?? '',
            stated.preferred_station, v => v);

        const aloneInput = field('willing_to_lead');
        aloneInput.checked = !!(overrides.willing_to_lead ?? stated.willing_to_lead);
        const aloneHelp = el.querySelector('[data-override-stated="willing_to_lead"]');
        if (aloneHelp) {
            aloneHelp.textContent = stated.willing_to_lead
                ? "Member said they're okay being on a leg alone"
                : "Member said they'd rather not be on a leg alone";
        }

        const noteInput = el.querySelector('[data-override-note]');
        if (noteInput) noteInput.value = member.override_note || '';

        api.clearModalAlert(el);
        Modal.getOrCreateInstance(el).show();
    }

    /**
     * Read the dialog into the sparse override shape the API expects: a field
     * only appears if it actually differs from what the member stated (or is
     * explicitly cleared), so "adjusted" never means "the captain opened the
     * dialog and pressed save".
     */
    _readOverrideForm(member) {
        const el = this.overrideModalEl;
        const stated = member.stated || {};
        const value = name => el.querySelector(`[data-override-field="${name}"]`).value.trim();
        const cleared = name => {
            const box = el.querySelector(`[data-override-clear="${name}"]`);
            return !!(box && box.checked);
        };
        const overrides = {};

        if (cleared('preferred_miles')) {
            overrides.preferred_miles = null;
        } else if (value('preferred_miles') !== '') {
            const miles = Number(value('preferred_miles'));
            if (!Number.isFinite(miles)) throw new Error('Preferred miles must be a number.');
            overrides.preferred_miles = miles;
        }

        if (cleared('planned_pace_seconds')) {
            overrides.planned_pace_seconds = null;
        } else if (value('planned_pace_seconds') !== '') {
            const pace = parsePace(value('planned_pace_seconds'));
            if (pace === null) throw new Error('Planned pace must be in MM:SS format.');
            overrides.planned_pace_seconds = pace;
        }

        if (cleared('preferred_station')) {
            overrides.preferred_station = null;
        } else if (value('preferred_station') !== '') {
            overrides.preferred_station = value('preferred_station');
        }

        const willingToLead = el.querySelector('[data-override-field="willing_to_lead"]').checked;
        if (willingToLead !== !!stated.willing_to_lead) overrides.willing_to_lead = willingToLead;

        const noteInput = el.querySelector('[data-override-note]');
        return { overrides, note: noteInput ? noteInput.value : '' };
    }

    async _submitOverrides() {
        const el = this.overrideModalEl;
        const membershipId = el.dataset.membershipId;
        const member = this._memberById(membershipId);
        if (!member) return;

        let payload;
        try {
            payload = this._readOverrideForm(member);
        } catch (err) {
            api.showModalAlert(el, 'danger', err.message);
            return;
        }

        const submitBtn = el.querySelector('[data-override-submit]');
        if (submitBtn) api.setButtonLoading(submitBtn, 'Saving...');
        const result = await assignmentAPI.saveOverrides(
            this.teamId, membershipId, payload.overrides, payload.note,
        );
        if (submitBtn) api.resetButton(submitBtn);

        if (!result.success) {
            api.showModalAlert(el, 'danger', result.error || 'Failed to save the adjustment.');
            return;
        }

        this._applyMemberUpdate(result.data.member);
        Modal.getOrCreateInstance(el).hide();
        api.showBanner(`Preferences used for ${member.name} updated.`, 'success');
    }

    async _clearOverrides() {
        const el = this.overrideModalEl;
        const membershipId = el.dataset.membershipId;
        const member = this._memberById(membershipId);
        if (!member) return;

        const clearBtn = el.querySelector('[data-override-reset]');
        if (clearBtn) api.setButtonLoading(clearBtn, 'Reverting...');
        const result = await assignmentAPI.clearOverrides(this.teamId, membershipId);
        if (clearBtn) api.resetButton(clearBtn);

        if (!result.success) {
            api.showModalAlert(el, 'danger', result.error || 'Failed to revert the adjustment.');
            return;
        }

        this._applyMemberUpdate(result.data.member);
        Modal.getOrCreateInstance(el).hide();
        api.showBanner(`${member.name} reverted to their own stated preferences.`, 'success');
    }

    /**
     * Swap in a re-serialized member and repaint. Overrides are saved on their
     * own endpoint the moment the dialog is submitted, so this deliberately
     * does NOT touch the dirty flag: the unsaved-assignments warning must keep
     * meaning "assignments", not "something on this page changed".
     */
    _applyMemberUpdate(updated) {
        const index = this.state.members.findIndex(m => m.membership_id === updated.membership_id);
        if (index === -1) return;
        this.state.members[index] = updated;
        this._render();
        this._notifyChanged();
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

            const aloneMarker = member.willing_to_lead
                ? '<ion-icon name="flag-outline" class="text-info ms-1" title="Okay being on a leg alone"></ion-icon>'
                : '';

            let addControl = '';
            if (this.canEdit && member.status === 'active' && legs.length) {
                const menuItems = legs.map(leg => {
                    const key = legKey(leg);
                    const held = (this.state.assignments[key] || []).includes(member.membership_id);
                    return `<li><button type="button" class="dropdown-item bench-add-item d-flex align-items-center justify-content-between"
                                data-membership-id="${escapeHtml(member.membership_id)}" data-leg-key="${key}">
                                <span>${escapeHtml(legLabel(leg))}</span>
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
                        <div class="fw-semibold">${escapeHtml(member.name)}${aloneMarker}${this._statusBadge(member)}</div>
                        <div class="text-muted">${legsHeld} leg${legsHeld === 1 ? '' : 's'} assigned</div>
                        ${this._staleOverrideWarning(member)}
                        <div class="mt-1" data-metrics-mount="member" data-membership-id="${escapeHtml(member.membership_id)}"></div>
                    </div>
                    ${addControl}
                </div>`;
        }).join('');

        this.benchEl.querySelectorAll('.bench-add-item').forEach(btn => {
            btn.addEventListener('click', () => {
                const membershipId = btn.dataset.membershipId;
                const key = btn.dataset.legKey;
                const held = (this.state.assignments[key] || []).includes(membershipId);
                if (held) {
                    this.removeAssignment(key, membershipId);
                } else {
                    this.addAssignment(key, membershipId);
                }
            });
        });
    }

    /**
     * A leg's position, numbered from 1 within each line it belongs to.
     * Shared with the schedule view via leg-keys.js so the two can't drift.
     */
    _legNumber(leg) {
        const teamLines = (this.state.course && this.state.course.lines) || leg.lines || [];
        const { text, title } = legNumbering(leg, teamLines);
        if (!text) return '';
        return `<span class="leg-number" title="${escapeHtml(title)}">${escapeHtml(text)}</span>`;
    }

    _renderLegChip(membershipId, key, label) {
        const member = this._memberById(membershipId);
        if (!member) {
            // Assignment references a member no longer visible to us (shouldn't
            // normally happen -- get_board() includes any assigned non-active
            // member -- but don't blow up the render if it does).
            return `<span class="badge text-bg-secondary">Unknown member</span>`;
        }
        const removeBtn = this.canEdit
            ? `<button type="button" class="btn-close ms-1" aria-label="Remove ${escapeHtml(member.name)} from ${escapeHtml(label)}"
                   data-membership-id="${escapeHtml(membershipId)}" data-leg-key="${escapeHtml(key)}"></button>`
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
            const key = legKey(leg);
            const label = legLabel(leg);
            const assigned = this.state.assignments[key] || [];
            const chips = assigned.map(id => this._renderLegChip(id, key, label)).join('');
            // An empty leg shows a plus rather than a sentence. The instruction
            // it replaces moves to the zone's label, so it stays available to
            // screen readers and on hover without repeating on every row.
            const emptyHint = assigned.length === 0
                ? (this.canEdit
                    ? '<ion-icon name="add-outline" class="drop-hint" aria-hidden="true"></ion-icon>'
                    : '<span class="text-muted small">Unassigned</span>')
                : '';
            const zoneLabel = this.canEdit
                ? `Runners for ${label}. Drag a runner here`
                : `Runners for ${label}`;

            return `
                <div class="list-group-item">
                    <div class="d-flex flex-wrap align-items-baseline justify-content-between gap-2 mb-2">
                        <div class="d-flex align-items-center flex-wrap gap-2">
                            ${this._legNumber(leg)}
                            <strong>${escapeHtml(leg.start.name)}</strong>
                            <span class="text-muted">&rarr;</span>
                            <strong>${escapeHtml(leg.end.name)}</strong>
                        </div>
                        <div class="small text-muted">
                            ${formatMiles(leg.distance)} mi
                            <span class="ms-2">&uarr;${leg.ascent}</span>
                            <span class="ms-1">&darr;${leg.descent} ft</span>
                        </div>
                    </div>
                    <div class="d-flex flex-wrap gap-1 mb-2" data-metrics-mount="leg" data-leg-key="${key}"></div>
                    <div class="leg-dropzone d-flex flex-wrap align-items-center gap-2 p-2 border border-dashed rounded"
                         data-leg-key="${key}" role="group" aria-label="${escapeHtml(zoneLabel)}"
                         title="${escapeHtml(zoneLabel)}">
                        ${chips}${emptyHint}
                    </div>
                </div>`;
        }).join('');

        this.legsListEl.querySelectorAll('.btn-close[data-membership-id]').forEach(btn => {
            btn.addEventListener('click', () => {
                this.removeAssignment(btn.dataset.legKey, btn.dataset.membershipId);
            });
        });
    }

    // ---- Drag and drop ----

    _destroySortables() {
        this._sortables.forEach(s => s.destroy());
        this._sortables = [];
    }

    /**
     * Mark which legs can accept the runner being dragged.
     *
     * A member may only appear on a leg once, so the legs they already hold
     * are dead ends. Saying so mid-drag beats letting the drop land and then
     * explaining it away with a warning banner.
     */
    _markDropTargets(membershipId) {
        this.legsListEl.querySelectorAll('.leg-dropzone').forEach(zoneEl => {
            const held = (this.state.assignments[zoneEl.dataset.legKey] || []).includes(membershipId);
            zoneEl.classList.toggle('leg-dropzone-blocked', held);
            zoneEl.classList.toggle('leg-dropzone-open', !held);
            // Point at the chip that makes this leg a dead end, rather than
            // dimming everyone standing on it.
            if (held) {
                const chip = zoneEl.querySelector(`.leg-chip[data-membership-id="${CSS.escape(membershipId)}"]`);
                if (chip) chip.classList.add('leg-chip-duplicate');
            }
        });
    }

    _clearDropTargets() {
        this.legsListEl.querySelectorAll('.leg-dropzone').forEach(zoneEl => {
            zoneEl.classList.remove('leg-dropzone-blocked', 'leg-dropzone-open');
        });
        this.legsListEl.querySelectorAll('.leg-chip-duplicate').forEach(chipEl => {
            chipEl.classList.remove('leg-chip-duplicate');
        });
    }

    _wireSortable() {
        if (this.benchEl) {
            this._sortables.push(new Sortable(this.benchEl, {
                group: { name: 'leg-assignments', pull: 'clone', put: false },
                sort: false,
                animation: 150,
                draggable: '.bench-chip-draggable',
                filter: '.dropdown, .dropdown *, .bench-override-btn, .bench-override-btn *',
                preventOnFilter: false,
                onStart: (evt) => this._markDropTargets(evt.item.dataset.membershipId),
                onEnd: () => this._clearDropTargets(),
            }));
        }

        this.legsListEl.querySelectorAll('.leg-dropzone').forEach(zoneEl => {
            this._sortables.push(new Sortable(zoneEl, {
                group: {
                    name: 'leg-assignments',
                    pull: false,
                    // Refuse the drop outright on a leg the member already
                    // holds, so no placeholder opens up there either.
                    put: (to, _from, dragEl) => !(
                        this.state.assignments[to.el.dataset.legKey] || []
                    ).includes(dragEl.dataset.membershipId),
                },
                animation: 150,
                onAdd: (evt) => {
                    const membershipId = evt.item.dataset.membershipId;
                    const key = evt.to.dataset.legKey;
                    // Sortable already inserted a raw clone of the bench chip;
                    // discard it -- state + _render() is the single source of
                    // truth for what a leg row looks like.
                    evt.item.remove();
                    if (membershipId) this.addAssignment(key, membershipId);
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
