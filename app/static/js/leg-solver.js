/**
 * Leg Assignment Solver
 *
 * "Optimize remaining": turns the board state into ASP facts, runs them
 * through clingo-wasm against the vendored domain (app/static/asp/), and
 * turns the best model's `assignment/2` atoms into suggested placements.
 *
 * Split like leg-metrics.js / leg-badges.js into a DOM-free half (fact
 * generation + parsing, unit-tested from plain Node in
 * tests/js/leg-solver.test.mjs) and a browser-only half (CDN loading,
 * driving the worker). Chip rendering lives in leg-solver-ui.js.
 *
 * All numbers in facts are already integer-scaled per the course model's
 * `units` block; `preferred_miles` is the exception, converted here with
 * `Math.ceil(miles * 100)` to match the legs/commute convention.
 */

import { legKey } from './leg-keys.js';

// ---- clingo-wasm CDN wiring ------------------------------------------------

// Pinned to the version smoke-tested in tests/js/leg-solver.test.mjs. Bump
// both together.
export const CLINGO_VERSION = '0.3.2';
export const CLINGO_SCRIPT_URL = `https://cdn.jsdelivr.net/npm/clingo-wasm@${CLINGO_VERSION}`;
export const CLINGO_WASM_URL = `https://cdn.jsdelivr.net/npm/clingo-wasm@${CLINGO_VERSION}/dist/clingo.wasm`;

/** Thrown (via Promise.race) when a solve is cancelled, by the captain or
 * by the timeout -- as distinct from a real clingo error or UNSAT. */
export class SolverCancelledError extends Error {
    constructor(message = 'Solve cancelled') {
        super(message);
        this.name = 'SolverCancelledError';
    }
}

let clingoLoadPromise = null;

/**
 * Lazily inject the clingo-wasm UMD bundle (jsDelivr's `jsdelivr` package.json
 * field points at `dist/clingo.web.js`, a browser bundle that runs Clingo in
 * a Worker and exposes a global `window.clingo` -- see the clingo-wasm
 * README's "In the Browser" section). Cached after the first successful
 * load; a failed load is NOT cached, so a later retry (e.g. after the
 * network recovers) can succeed.
 */
export function loadClingo(scriptUrl = CLINGO_SCRIPT_URL) {
    if (clingoLoadPromise) return clingoLoadPromise;

    clingoLoadPromise = new Promise((resolve, reject) => {
        if (typeof window === 'undefined' || typeof document === 'undefined') {
            reject(new Error('loadClingo() requires a browser environment'));
            return;
        }
        if (window.clingo) {
            resolve(window.clingo);
            return;
        }
        const script = document.createElement('script');
        script.src = scriptUrl;
        script.async = true;
        script.onload = () => {
            if (window.clingo) resolve(window.clingo);
            else reject(new Error('clingo-wasm script loaded but window.clingo was not defined'));
        };
        script.onerror = () => reject(new Error(`Failed to load clingo-wasm from ${scriptUrl}`));
        document.head.appendChild(script);
    }).catch((err) => {
        clingoLoadPromise = null; // allow retry
        throw err;
    });

    return clingoLoadPromise;
}

/**
 * Fetch the vendored ASP sources relative to this module's URL, so this
 * works under any static prefix. The Node smoke test reads them with `fs`
 * and calls `buildProgram()` itself.
 */
export async function fetchAspSources() {
    const domainUrl = new URL('../asp/scheduling-domain.lp', import.meta.url);
    const teamUrl = new URL('../asp/team-assign.lp', import.meta.url);
    const [domainSource, teamSource] = await Promise.all([
        fetch(domainUrl).then((r) => {
            if (!r.ok) throw new Error(`Failed to fetch ${domainUrl}: ${r.status}`);
            return r.text();
        }),
        fetch(teamUrl).then((r) => {
            if (!r.ok) throw new Error(`Failed to fetch ${teamUrl}: ${r.status}`);
            return r.text();
        }),
    ]);
    return { domainSource, teamSource };
}

/** Concatenate domain, team, and generated-facts programs into one source.
 * Pure -- shared by the browser path and the Node smoke test. */
export function buildProgram(domainSource, teamSource, factsProgram) {
    return [domainSource, teamSource, factsProgram].join('\n');
}

/**
 * One clingo-wasm worker "handle": wraps `run()` in a manual cancellation
 * race (clingo-wasm's own worker.terminate() during `restart()` just lets
 * the in-flight `run()` promise hang forever -- see src/index.web.ts in the
 * clingo-wasm package -- so we race it against our own rejecting promise)
 * and exposes `cancel()`, which both unblocks the caller immediately and
 * restarts the underlying worker (per the clingo-wasm README's guidance
 * for interrupting a long solve) so the next `run()` starts clean.
 *
 * IMPORTANT: `run()` defaults `models` to 0, not 1. clingo-wasm's `run()`
 * forwards `models` straight to clasp's `-n` (models-to-compute) argument,
 * and clasp's branch-and-bound optimization keeps searching for a *better*
 * model regardless of `-n` -- EXCEPT that `-n <k>` for a finite k>0 also
 * caps the total number of models printed, so `-n1` stops at the very
 * FIRST feasible model found and reports plain `SATISFIABLE`, never proving
 * it's actually optimal (verified empirically: a trivial 3-choice
 * `#minimize` program returns `SATISFIABLE` with `models=1` and
 * `OPTIMUM FOUND` with `models=0` -- this is also why clingo-wasm's own
 * `test/test.ts` always calls `run(program, 0)` for its optimization
 * cases). `0` does NOT mean "enumerate every tied-optimal answer set" --
 * branch-and-bound only ever prints strictly *improving* models (a handful
 * of them, not all ties) before declaring the last one optimal, so this is
 * both correct and fast in practice (~1s for a 6-leg slice, ~15s for the
 * full 22-leg/6-member course in this repo's own testing).
 */
export function createSolverHandle({ wasmUrl = CLINGO_WASM_URL, scriptUrl = CLINGO_SCRIPT_URL } = {}) {
    let cancelReject = null;
    let initialized = false;

    async function ensureReady() {
        const clingo = await loadClingo(scriptUrl);
        if (!initialized) {
            await clingo.init(wasmUrl);
            initialized = true;
        }
        return clingo;
    }

    async function run(program, models = 0) {
        const clingo = await ensureReady();
        const cancelPromise = new Promise((_resolve, reject) => {
            cancelReject = reject;
        });
        try {
            return await Promise.race([clingo.run(program, models), cancelPromise]);
        } finally {
            cancelReject = null;
        }
    }

    async function cancel(reason) {
        if (cancelReject) cancelReject(new SolverCancelledError(reason));
        const clingo = await loadClingo(scriptUrl);
        await clingo.restart(wasmUrl);
        initialized = true; // restart() already re-initializes
    }

    return { run, cancel };
}

// ---- Fact generation (pure) ------------------------------------------------

function quoteAtomString(value) {
    return `"${String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
}

function pairKey(legKey, membershipId) {
    return `${legKey}::${membershipId}`;
}

/**
 * Generate the ASP fact program for the current board state.
 *
 * Emits participant/1, leg/3 + distance/ascent/descent, commuteDistance/3
 * (symmetrized, with self-distance 0, matching relay-scheduler's
 * convention), preference facts only for members who stated them, and
 * assignment/2 pins for every chip already on the board. A preferred
 * station that resolves to no course exchange is skipped, matching the
 * metrics layer's "no data" treatment.
 *
 * Returns `{ program, pinnedPairs }`; `pinnedPairs` later filters the
 * solver's output down to genuinely new suggestions.
 *
 * `leg/3`'s first argument is just the index in `course.legs` -- the domain
 * needs a unique id, but identity comes back out of the start/end
 * exchanges, so it never has to be mapped back.
 */
export function generateFacts(state) {
    const { course, members, assignments } = state || {};
    if (!course || !members) {
        return { program: '', pinnedPairs: new Set() };
    }

    const lines = [];
    const courseExchangeIds = new Set();
    for (const leg of course.legs) {
        courseExchangeIds.add(leg.start.id);
        courseExchangeIds.add(leg.end.id);
    }

    for (const member of members) {
        lines.push(`participant(${quoteAtomString(member.membership_id)}).`);
    }

    course.legs.forEach((leg, legId) => {
        lines.push(`leg(${legId},${leg.start.id},${leg.end.id}).`);
        lines.push(`distance(${leg.start.id},${leg.end.id},${leg.distance}).`);
        lines.push(`ascent(${leg.start.id},${leg.end.id},${leg.ascent}).`);
        lines.push(`descent(${leg.start.id},${leg.end.id},${leg.descent}).`);
    });

    const commuteExchangeIds = new Set(courseExchangeIds);
    for (const [a, b] of course.commute || []) {
        commuteExchangeIds.add(a);
        commuteExchangeIds.add(b);
    }
    for (const id of commuteExchangeIds) {
        lines.push(`commuteDistance(${id},${id},0).`);
    }
    for (const [a, b, dist] of course.commute || []) {
        lines.push(`commuteDistance(${a},${b},${dist}).`);
        lines.push(`commuteDistance(${b},${a},${dist}).`);
    }

    const stationIndex = course.station_index || {};
    for (const member of members) {
        const p = quoteAtomString(member.membership_id);

        if (member.preferred_miles !== null && member.preferred_miles !== undefined) {
            const hundredths = Math.ceil(Number(member.preferred_miles) * 100);
            lines.push(`preferredDistance(${p},${hundredths}).`);
        }

        if (member.planned_pace_seconds !== null && member.planned_pace_seconds !== undefined) {
            lines.push(`preferredPace(${p},${member.planned_pace_seconds}).`);
        }

        if (member.preferred_station) {
            const exchangeId = stationIndex[member.preferred_station.trim()];
            if (exchangeId !== undefined && exchangeId !== null && courseExchangeIds.has(exchangeId)) {
                lines.push(`preferredEndExchange(${p},${exchangeId}).`);
            }
        }

        if (member.willing_to_lead) {
            lines.push(`willingToLead(${p}).`);
        }
    }

    const pinnedPairs = new Set();
    course.legs.forEach((leg, legId) => {
        const key = legKey(leg);
        const membershipIds = (assignments && assignments[key]) || [];
        for (const membershipId of membershipIds) {
            lines.push(`assignment(${quoteAtomString(membershipId)},leg(${legId},${leg.start.id},${leg.end.id})).`);
            pinnedPairs.add(pairKey(key, membershipId));
        }
    });

    return { program: `${lines.join('\n')}\n`, pinnedPairs };
}

// ---- Result parsing (pure) -------------------------------------------------

// Matches e.g. assignment("m1abc2",leg(3,143,153)) from clingo's JSON
// output. Membership ids carry no quotes or backslashes, but the regex
// tolerates escaped ones defensively.
const ASSIGNMENT_ATOM_RE = /^assignment\("((?:[^"\\]|\\.)*)",leg\((-?\d+),(-?\d+),(-?\d+)\)\)$/;

/** Parse one witness's atom strings into `{ membershipId, legKey }` pairs,
 * ignoring atoms that aren't `assignment/2` (the witness echoes input facts
 * too). The key is rebuilt from the exchanges inside the atom. */
export function parseAssignments(atomStrings) {
    const results = [];
    for (const atom of atomStrings || []) {
        const match = ASSIGNMENT_ATOM_RE.exec(atom);
        if (!match) continue;
        const membershipId = match[1].replace(/\\"/g, '"').replace(/\\\\/g, '\\');
        results.push({ membershipId, legKey: `${Number(match[3])}-${Number(match[4])}` });
    }
    return results;
}

/** Drop anything already pinned -- it was already true, not a suggestion
 * -- and collapse duplicates. */
export function diffSuggestions(solvedAssignments, pinnedPairs) {
    const suggestions = [];
    const seen = new Set();
    for (const { membershipId, legKey: key } of solvedAssignments) {
        const pair = pairKey(key, membershipId);
        if (pinnedPairs.has(pair) || seen.has(pair)) continue;
        seen.add(pair);
        suggestions.push({ legKey: key, membershipId });
    }
    return suggestions;
}

/** The best (last-improving) witness, or null on UNSAT/no model yet. clasp
 * prints improving models in order, so the last one is the best. */
export function bestWitness(clingoResult) {
    const calls = (clingoResult && clingoResult.Call) || [];
    const lastCall = calls[calls.length - 1];
    const witnesses = (lastCall && lastCall.Witnesses) || [];
    return witnesses[witnesses.length - 1] || null;
}

// ---- Top-level orchestration -----------------------------------------------

/**
 * Run "Optimize remaining" against the given board `state` using an
 * already-created solver handle (see `createSolverHandle`). Returns one of:
 *   { status: 'ok', suggestions: [{legKey, membershipId}, ...] }
 *   { status: 'unsat', message }        -- no model at all (see team-assign.lp;
 *                                           should be rare given the
 *                                           at-least-1 relaxation -- e.g. a
 *                                           team with legs but zero members)
 *   { status: 'cancelled' }             -- captain hit Cancel, or the caller's
 *                                           own timeout fired and called
 *                                           handle.cancel()
 *   { status: 'unavailable', message }  -- couldn't load/init clingo-wasm
 *                                           (CDN/network failure) or fetch
 *                                           the vendored ASP sources. This is
 *                                           the "progressive enhancement
 *                                           failed" case -- the caller should
 *                                           disable the Optimize button
 *                                           rather than let the captain keep
 *                                           retrying a hopeless load.
 *   { status: 'error', message }        -- clingo loaded and ran fine but the
 *                                           *program* itself errored (a bug
 *                                           in the vendored/generated ASP,
 *                                           not a CDN problem) -- worth
 *                                           surfacing distinctly since
 *                                           retrying won't help but the
 *                                           solver itself isn't "unavailable"
 *
 * Does not touch the DOM or the board's real state -- leg-solver-ui.js owns
 * turning `suggestions` into chips and `board.addAssignment()` calls.
 */
export async function optimizeRemaining(state, handle, { fetchSources = fetchAspSources } = {}) {
    const { program: factsProgram, pinnedPairs } = generateFacts(state);
    if (!factsProgram) {
        return { status: 'error', message: 'No course data is loaded yet.' };
    }

    let domainSource;
    let teamSource;
    try {
        ({ domainSource, teamSource } = await fetchSources());
    } catch (err) {
        return { status: 'unavailable', message: err.message || String(err) };
    }

    const program = buildProgram(domainSource, teamSource, factsProgram);

    let result;
    try {
        result = await handle.run(program, 0); // 0 = search to a proven optimum, see createSolverHandle's docstring
    } catch (err) {
        if (err instanceof SolverCancelledError) {
            return { status: 'cancelled' };
        }
        // handle.run() only throws (other than our own cancellation) if
        // ensureReady() couldn't load the CDN script or init the wasm
        // module -- clingo's own worker protocol otherwise always resolves
        // (even a program error comes back as a normal `{Result: 'ERROR'}`
        // payload, handled below).
        return { status: 'unavailable', message: err.message || String(err) };
    }

    if (!result || result.Result === 'ERROR') {
        return { status: 'error', message: (result && result.Error) || 'clingo reported an error.' };
    }
    if (result.Result === 'UNSATISFIABLE') {
        return {
            status: 'unsat',
            message: 'No valid assignment exists for the current members and pins (unexpected given the '
                + 'at-least-1 coverage relaxation -- check whether the team has any active members left).',
        };
    }

    const witness = bestWitness(result);
    if (!witness) {
        return { status: 'error', message: `clingo returned no model (Result: ${result.Result}).` };
    }

    const solved = parseAssignments(witness.Value);
    const suggestions = diffSuggestions(solved, pinnedPairs);
    return { status: 'ok', suggestions };
}
