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
 *
 * While solving, models stream in and the captain watches a provisional plan
 * take shape; a Cancel keeps the best one found. See PREVIEW_THROTTLE_MS and
 * the note on provisional chips being non-interactive.
 */

import { api } from './api-client.js';
import { createSolverHandle, optimizeRemaining, generateFacts } from './leg-solver.js';

// How often the provisional preview may repaint, at most.
//
// Not politeness -- a requirement. Measured on the full course, clasp streams
// ~105 improving models, some 1-2ms apart, and every one is a *different*
// assignment set rather than a polish of the last, so painting each would be
// a strobe of chips jumping between legs. At 600ms the same solve produces
// about half a dozen calm repaints.
const PREVIEW_THROTTLE_MS = 600;

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
     * @param {HTMLElement} [opts.spinnerEl] - shown only while a solve is running
     * @param {HTMLElement} [opts.singleRunnerCheckboxEl] - "one runner per leg" solver option
     */
    constructor(opts) {
        this.board = opts.board;
        this.legsListEl = opts.legsListEl;
        this.optimizeButtonEl = opts.optimizeButtonEl || null;
        this.cancelButtonEl = opts.cancelButtonEl || null;
        this.acceptAllButtonEl = opts.acceptAllButtonEl || null;
        this.clearButtonEl = opts.clearButtonEl || null;
        this.suggestionCountEl = opts.suggestionCountEl || null;
        this.spinnerEl = opts.spinnerEl || null;
        this.singleRunnerCheckboxEl = opts.singleRunnerCheckboxEl || null;

        this.handle = createSolverHandle();
        this.suggestions = [];
        // Whether `this.suggestions` came from a solve that ran to a proven
        // optimum. False after a Stop, which is what the "best so far"
        // wording throughout this class is keyed off.
        this.suggestionsOptimal = true;
        this.solving = false;

        // Provisional plan painted during a solve (see PREVIEW_THROTTLE_MS).
        this.previewSuggestions = [];
        this.modelCount = 0;
        this._previewTimer = null;
        this._previewPending = false;

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
        this.suggestionsOptimal = true;
        this.previewSuggestions = [];
        this.modelCount = 0;
        this._repaintSuggestions();
        this._setSolving(true);

        let result;
        try {
            result = await optimizeRemaining(this.board.state, this.handle, {
                onModel: ({ index, suggestions }) => this._onStreamedModel(index, suggestions),
                singleRunnerPerLeg: !!(this.singleRunnerCheckboxEl && this.singleRunnerCheckboxEl.checked),
            });
        } finally {
            this._cancelPreviewTimer();
            this.previewSuggestions = [];
            this._setSolving(false);
            // Clear the provisional chips on every exit path, including the
            // ones (unsat / error / unavailable) where _handleResult has no
            // suggestions of its own to paint and would otherwise leave the
            // last streamed preview stranded on the board.
            this._repaintSuggestions();
        }

        this._handleResult(result);
    }

    /**
     * A model arrived mid-solve. Record it, then repaint at most once per
     * PREVIEW_THROTTLE_MS: paint the first one immediately (so the board
     * responds the moment grounding finishes, ~1.5s in on the full course),
     * then coalesce the flood behind a trailing timer that always paints the
     * newest state rather than a queued stale one.
     */
    _onStreamedModel(index, suggestions) {
        this.previewSuggestions = suggestions;
        this.modelCount = index;

        if (this._previewTimer) {
            this._previewPending = true;
            return;
        }
        this._repaintSuggestions();
        this._previewTimer = setInterval(() => {
            if (!this._previewPending) {
                this._cancelPreviewTimer();
                return;
            }
            this._previewPending = false;
            this._repaintSuggestions();
        }, PREVIEW_THROTTLE_MS);
    }

    _cancelPreviewTimer() {
        if (this._previewTimer) clearInterval(this._previewTimer);
        this._previewTimer = null;
        this._previewPending = false;
    }

    async cancelSolve() {
        if (!this.solving) return;
        // Swallow: cancel() reaches for the loader again, so it can reject
        // if the network went away mid-solve. The in-flight run() has
        // already been rejected by then, so optimizeRemaining still returns
        // a clean 'cancelled' -- there is nothing useful to do here but
        // avoid an unhandled rejection.
        await this.handle.cancel('Optimize remaining stopped').catch(() => {});
    }

    _handleResult(result) {
        switch (result.status) {
            case 'ok':
                this.suggestionsOptimal = result.optimal !== false;
                if (result.suggestions.length === 0) {
                    api.showBanner('Optimize remaining found nothing to add -- every leg already has a runner.', 'info');
                    this._repaintSuggestions();
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

            // A Stop is no longer a discarded solve: whatever clasp had
            // streamed by then is a valid, fully-covering plan -- just one
            // it never got to prove was the best available. Keep it, and be
            // explicit about that distinction, since every other path
            // through this UI hands the captain a proven optimum.
            case 'cancelled': {
                const kept = result.suggestions || [];
                if (kept.length === 0) {
                    api.showBanner('Optimize remaining was stopped before it found a plan.', 'warning');
                    break;
                }
                this.suggestions = kept;
                this.suggestionsOptimal = false;
                this._repaintSuggestions();
                api.showBanner(
                    `Stopped early -- keeping the best plan found so far (${kept.length} placement${kept.length === 1 ? '' : 's'}). `
                    + 'It covers every leg and respects your pins, but it was not checked against every alternative, '
                    + 'so a full run might place a few people differently.',
                    'warning',
                );
                break;
            }

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
        this.suggestionsOptimal = true;
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
            // The button's meaning changes once there is something to keep:
            // before the first model it genuinely abandons the solve, after
            // it it banks the best plan found. Say which.
            const canKeep = this.modelCount > 0;
            this.cancelButtonEl.innerHTML = canKeep
                ? '<ion-icon name="checkmark-done-outline" class="me-1"></ion-icon>Stop &amp; keep best'
                : '<ion-icon name="stop-circle-outline" class="me-1"></ion-icon>Cancel';
            this.cancelButtonEl.classList.toggle('btn-outline-danger', !canKeep);
            this.cancelButtonEl.classList.toggle('btn-outline-primary', canKeep);
            this.cancelButtonEl.title = canKeep
                ? 'Stop searching and keep the best plan found so far'
                : 'Stop searching (nothing found yet to keep)';
        }
        // Accept all / Clear act on real suggestions only -- never on the
        // provisional preview, which is not the captain's to accept yet.
        const hasSettled = !this.solving && this.suggestions.length > 0;
        if (this.acceptAllButtonEl) {
            this.acceptAllButtonEl.classList.toggle('d-none', !hasSettled);
        }
        if (this.clearButtonEl) {
            this.clearButtonEl.classList.toggle('d-none', !hasSettled);
        }
        if (this.spinnerEl) {
            this.spinnerEl.classList.toggle('d-none', !this.solving);
        }
        // The option only takes effect at the start of a solve (it's baked
        // into the generated facts) -- lock it while one is running rather
        // than let a mid-solve toggle imply it did something.
        if (this.singleRunnerCheckboxEl) {
            this.singleRunnerCheckboxEl.disabled = this.solving;
        }
        this._updateStatusBadge();
    }

    /**
     * The status badge doubles as the solve's progress readout. Note the
     * d-none toggle: the template ships this element hidden, and before
     * streaming nothing ever un-hid it, so the pending-count text it has
     * always set was never actually visible.
     */
    _updateStatusBadge() {
        const el = this.suggestionCountEl;
        if (!el) return;

        let text = '';
        let variant = 'text-bg-primary';

        if (this.solving) {
            variant = 'text-bg-secondary';
            // Nothing streams during grounding (~1.5s on the full course),
            // so distinguish "still setting up" from "actively improving" --
            // otherwise the first seconds look identical to a hang.
            text = this.modelCount === 0
                ? 'Preparing the course...'
                : 'Searching -- showing the best plan so far';
        } else if (this.suggestions.length > 0) {
            const n = this.suggestions.length;
            text = this.suggestionsOptimal
                ? `${n} suggestion${n === 1 ? '' : 's'} pending`
                : `${n} suggestion${n === 1 ? '' : 's'} pending -- best so far, not fully searched`;
            if (!this.suggestionsOptimal) variant = 'text-bg-warning';
        }

        el.textContent = text;
        el.classList.toggle('d-none', text === '');
        for (const cls of ['text-bg-primary', 'text-bg-secondary', 'text-bg-warning']) {
            el.classList.toggle(cls, cls === variant);
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
        // repeated repaint calls (e.g. clearSuggestions(), or a throttled
        // preview tick) between board re-renders.
        this.legsListEl.querySelectorAll('.leg-chip-suggested').forEach((el) => el.remove());

        // While solving, the board shows the provisional streamed plan
        // instead of the (empty) settled one.
        const provisional = this.solving;
        const toPaint = provisional ? this.previewSuggestions : this.suggestions;

        for (const suggestion of toPaint) {
            const zone = this.legsListEl.querySelector(`.leg-dropzone[data-leg-key="${suggestion.legKey}"]`);
            if (!zone) continue;

            const hint = zone.querySelector('.drop-hint');
            if (hint) hint.remove();

            const member = this._memberById(suggestion.membershipId);
            const name = member ? member.name : 'Unknown member';
            zone.appendChild(
                provisional
                    ? this._provisionalChip(suggestion, name)
                    : this._suggestionChip(suggestion, name),
            );
        }
    }

    _baseChip(suggestion, extraClasses) {
        const chip = document.createElement('div');
        chip.className = 'd-flex align-items-center gap-1 border border-dashed rounded-pill ps-2 pe-1 py-1 '
            + `leg-chip leg-chip-suggested ${extraClasses}`;
        chip.dataset.membershipId = suggestion.membershipId;
        chip.dataset.legKey = String(suggestion.legKey);
        return chip;
    }

    /**
     * A streamed-but-not-final placement. Deliberately carries NO accept or
     * discard buttons: the underlying plan is replaced wholesale every time
     * clasp finds a better one, so a button here would be a target that
     * moves out from under the cursor mid-click. It is something to watch,
     * not something to act on -- acting on it is what "Stop & keep best" is
     * for, which settles the plan first and then offers the real chips.
     */
    _provisionalChip(suggestion, name) {
        const chip = this._baseChip(suggestion, 'leg-chip-provisional opacity-75');
        chip.title = `Provisional: the solver is still searching and may move ${name} to a different leg`;
        chip.innerHTML = `
            <ion-icon name="ellipsis-horizontal-outline" class="text-secondary flex-shrink-0"></ion-icon>
            <span class="small fst-italic text-secondary">${escapeHtml(name)}</span>`;
        return chip;
    }

    _suggestionChip(suggestion, name) {
        const chip = this._baseChip(suggestion, '');
        chip.title = this.suggestionsOptimal
            ? 'Suggested by Optimize remaining -- not saved until accepted'
            : 'Suggested by Optimize remaining (stopped early, so not fully searched) -- not saved until accepted';
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
        return chip;
    }
}

export function initLegSolverUI(opts) {
    return new LegSolverUI(opts);
}
