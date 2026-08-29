/**
 * Leg Solver UI
 *
 * DOM-side half of "Optimize remaining": wires the Optimize / Cancel /
 * Accept-all / Clear buttons in team_legs.html to the pure logic in
 * leg-solver.js and paints suggestions as distinct chips in the existing
 * dropzones (captain view only), mirroring leg-badges.js over leg-metrics.js.
 *
 * Suggestions are not part of LegBoard's state: they never touch
 * `board.state.assignments` or the dirty/save flow until the captain accepts
 * them, at which point they go through `board.addAssignment()` -- the same
 * call a manual drag uses. The solver stays a pure progressive enhancement.
 *
 * LegBoard re-renders each dropzone on every state change and then calls
 * `window.onAssignmentsChanged(state)`. This module chains onto that hook, so
 * pending suggestions get repainted after every board render and there is no
 * second render path to keep in sync.
 */

import { api } from './api-client.js';
import { createSolverHandle, optimizeRemaining, generateFacts } from './leg-solver.js';

// Auto-cancel a solve that's taking unreasonably long. Optimize remaining
// searches to a *proven* optimum (see leg-solver.js's createSolverHandle
// docstring -- clasp's branch-and-bound needs `models=0`, not `1`, to
// actually finish the proof rather than stopping at the first feasible
// answer), which is real solving work: the full 22-leg/6-member course in
// this repo's own live verification took ~15s in Node. Browser wasm may be
// somewhat slower, and a bigger team/looser preferences could take longer
// still, so this is a generous ceiling, not a tuned expectation -- the
// Cancel button is always available well before it fires.
const AUTO_TIMEOUT_MS = 45000;

function escapeHtml(value) {
    const div = document.createElement('div');
    div.textContent = value ?? '';
    return div.innerHTML;
}

export class LegSolverUI {
    /**
     * @param {Object} opts
     * @param {import('./leg-board.js').LegBoard} opts.board
     * @param {HTMLElement} opts.legsListEl - same container LegBoard renders leg rows into
     * @param {HTMLElement} [opts.optimizeButtonEl]
     * @param {HTMLElement} [opts.cancelButtonEl]
     * @param {HTMLElement} [opts.acceptAllButtonEl]
     * @param {HTMLElement} [opts.clearButtonEl]
     * @param {HTMLElement} [opts.suggestionCountEl] - small text/badge showing pending count
     */
    constructor(opts) {
        this.board = opts.board;
        this.legsListEl = opts.legsListEl;
        this.optimizeButtonEl = opts.optimizeButtonEl || null;
        this.cancelButtonEl = opts.cancelButtonEl || null;
        this.acceptAllButtonEl = opts.acceptAllButtonEl || null;
        this.clearButtonEl = opts.clearButtonEl || null;
        this.suggestionCountEl = opts.suggestionCountEl || null;

        this.handle = createSolverHandle();
        this.suggestions = [];
        this.solving = false;
        this._timedOut = false;
        this._timeoutHandle = null;

        if (this.optimizeButtonEl) {
            this.optimizeButtonEl.addEventListener('click', () => this.runOptimize());
        }
        if (this.cancelButtonEl) {
            this.cancelButtonEl.addEventListener('click', () => this.cancelSolve());
        }
        if (this.acceptAllButtonEl) {
            this.acceptAllButtonEl.addEventListener('click', () => this.acceptAll());
        }
        if (this.clearButtonEl) {
            this.clearButtonEl.addEventListener('click', () => this.clearSuggestions());
        }

        // Repaint pending suggestions after every board re-render (chain
        // rather than clobber, same pattern leg-badges.js uses).
        const previous = window.onAssignmentsChanged;
        window.onAssignmentsChanged = (state) => {
            if (typeof previous === 'function') previous(state);
            this._repaintSuggestions();
        };

        this._updateButtons();
    }

    // ---- Solve lifecycle ----

    async runOptimize() {
        if (this.solving) return;

        this.suggestions = [];
        this._repaintSuggestions();
        this._setSolving(true);
        this._timedOut = false;
        this._timeoutHandle = setTimeout(() => {
            this._timedOut = true;
            this.handle.cancel('Optimize remaining timed out');
        }, AUTO_TIMEOUT_MS);

        let result;
        try {
            result = await optimizeRemaining(this.board.state, this.handle);
        } finally {
            clearTimeout(this._timeoutHandle);
            this._setSolving(false);
        }

        this._handleResult(result);
    }

    async cancelSolve() {
        if (!this.solving) return;
        await this.handle.cancel('Optimize remaining cancelled');
    }

    _handleResult(result) {
        switch (result.status) {
            case 'ok':
                if (result.suggestions.length === 0) {
                    api.showBanner('Optimize remaining found nothing to add -- every leg already has a runner.', 'info');
                } else {
                    this.suggestions = result.suggestions;
                    this._repaintSuggestions();
                    const n = result.suggestions.length;
                    api.showBanner(
                        `Optimize remaining suggests ${n} placement${n === 1 ? '' : 's'}. `
                        + 'Review the dashed chips below, then accept or clear them.',
                        'success',
                    );
                }
                break;

            case 'unsat':
                api.showBanner(`Optimize remaining: ${result.message}`, 'danger');
                break;

            case 'cancelled':
                api.showBanner(
                    this._timedOut
                        ? 'Optimize remaining took too long and was stopped automatically. You can try again, or assign the rest manually.'
                        : 'Optimize remaining was cancelled.',
                    'warning',
                );
                break;

            case 'unavailable':
                this._disableForUnavailability(result.message);
                break;

            case 'error':
            default:
                api.showBanner(`Optimize remaining hit an error: ${escapeHtml(result.message || 'unknown error')}`, 'danger');
                break;
        }
    }

    _disableForUnavailability(message) {
        if (this.optimizeButtonEl) {
            this.optimizeButtonEl.disabled = true;
            this.optimizeButtonEl.title = `Solver unavailable: ${message}. The board still works without it -- assign legs manually.`;
        }
        api.showBanner(
            'Optimize remaining is unavailable right now (couldn\'t load the solver). The rest of the board is unaffected -- assign legs manually.',
            'warning',
        );
    }

    // ---- Suggestion mutation ----

    acceptOne(legKey, membershipId) {
        this.suggestions = this.suggestions.filter(
            (s) => !(s.legKey === legKey && s.membershipId === membershipId),
        );
        this.board.addAssignment(legKey, membershipId); // triggers board re-render + our repaint via the chained hook
    }

    discardOne(legKey, membershipId) {
        this.suggestions = this.suggestions.filter(
            (s) => !(s.legKey === legKey && s.membershipId === membershipId),
        );
        this._repaintSuggestions();
    }

    acceptAll() {
        const toAccept = [...this.suggestions];
        this.suggestions = [];
        for (const s of toAccept) {
            this.board.addAssignment(s.legKey, s.membershipId);
        }
    }

    clearSuggestions() {
        if (this.suggestions.length === 0) return;
        this.suggestions = [];
        this._repaintSuggestions();
        api.showBanner('Suggestions cleared.', 'secondary');
    }

    // ---- Rendering ----

    _setSolving(isSolving) {
        this.solving = isSolving;
        this._updateButtons();
    }

    _updateButtons() {
        // Note: we only ever toggle visibility (d-none) here, never
        // `disabled`, so an 'unavailable' result's permanent
        // `optimizeButtonEl.disabled = true` (set in
        // _disableForUnavailability) is never un-set by a later solve.
        if (this.optimizeButtonEl) {
            this.optimizeButtonEl.classList.toggle('d-none', this.solving);
        }
        if (this.cancelButtonEl) {
            this.cancelButtonEl.classList.toggle('d-none', !this.solving);
        }
        if (this.acceptAllButtonEl) {
            this.acceptAllButtonEl.classList.toggle('d-none', this.suggestions.length === 0);
        }
        if (this.clearButtonEl) {
            this.clearButtonEl.classList.toggle('d-none', this.suggestions.length === 0);
        }
        if (this.suggestionCountEl) {
            this.suggestionCountEl.textContent = this.suggestions.length
                ? `${this.suggestions.length} suggestion${this.suggestions.length === 1 ? '' : 's'} pending`
                : '';
        }
    }

    _memberById(membershipId) {
        return (this.board.state.members || []).find((m) => m.membership_id === membershipId);
    }

    _repaintSuggestions() {
        this._updateButtons();
        if (!this.legsListEl) return;

        // LegBoard's own _render() replaces the entire leg list's innerHTML
        // on every change, so any suggestion chips from a previous repaint
        // are already gone; this removal is just defensive idempotency for
        // repeated repaint calls (e.g. clearSuggestions()) between board
        // re-renders.
        this.legsListEl.querySelectorAll('.leg-chip-suggested').forEach((el) => el.remove());

        for (const suggestion of this.suggestions) {
            const zone = this.legsListEl.querySelector(`.leg-dropzone[data-leg-key="${suggestion.legKey}"]`);
            if (!zone) continue;

            const hint = zone.querySelector('.drop-hint');
            if (hint) hint.remove();

            const member = this._memberById(suggestion.membershipId);
            const name = member ? member.name : 'Unknown member';

            const chip = document.createElement('div');
            chip.className = 'd-flex align-items-center gap-1 border border-dashed rounded-pill ps-2 pe-1 py-1 leg-chip leg-chip-suggested';
            chip.dataset.membershipId = suggestion.membershipId;
            chip.dataset.legKey = String(suggestion.legKey);
            chip.title = 'Suggested by Optimize remaining -- not saved until accepted';
            chip.innerHTML = `
                <ion-icon name="sparkles-outline" class="text-primary flex-shrink-0"></ion-icon>
                <span class="small fst-italic">${escapeHtml(name)}</span>
                <button type="button" class="btn btn-sm btn-outline-success py-0 px-1 ms-1"
                        title="Accept: assign ${escapeHtml(name)} to this leg" data-action="accept">
                    <ion-icon name="checkmark-outline"></ion-icon>
                </button>
                <button type="button" class="btn btn-sm btn-outline-secondary py-0 px-1"
                        title="Discard this suggestion" data-action="discard">
                    <ion-icon name="close-outline"></ion-icon>
                </button>`;
            chip.querySelector('[data-action="accept"]').addEventListener(
                'click', () => this.acceptOne(suggestion.legKey, suggestion.membershipId),
            );
            chip.querySelector('[data-action="discard"]').addEventListener(
                'click', () => this.discardOne(suggestion.legKey, suggestion.membershipId),
            );
            zone.appendChild(chip);
        }
    }
}

export function initLegSolverUI(opts) {
    return new LegSolverUI(opts);
}
