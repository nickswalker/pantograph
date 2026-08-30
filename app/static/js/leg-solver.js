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
 * Models stream in via the `onModel` option as clasp finds them, so the UI
 * can show a provisional plan and keep the best one on cancel/timeout.
 *
 * All numbers in facts are already integer-scaled per the course model's
 * `units` block; `preferred_miles` is the exception, converted here with
 * `Math.ceil(miles * 100)` to match the legs/commute convention.
 */

import { legKey } from './leg-keys.js';

// ---- clingo-wasm CDN wiring ------------------------------------------------

// Pinned to the version smoke-tested in tests/js/leg-solver.test.mjs. Bump
// both together. The wasm binary is deliberately not pinned: 0.5.0+ ships
// single- and multi-threaded builds and picks between them at load time.
export const CLINGO_VERSION = '0.6.0';
export const CLINGO_MODULE_URL = `https://cdn.jsdelivr.net/npm/clingo-wasm@${CLINGO_VERSION}/dist/index.web.js`;

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
 * Lazily import clingo-wasm from the CDN. The module spawns its own worker
 * internally. Cached after a successful load; a failed load is not, so a
 * retry can succeed.
 */
export function loadClingo(moduleUrl = CLINGO_MODULE_URL) {
    if (clingoLoadPromise) return clingoLoadPromise;

    clingoLoadPromise = import(/* webpackIgnore: true */ moduleUrl)
        .then((mod) => {
            const clingo = mod.default || mod;
            if (!clingo || typeof clingo.run !== 'function') {
                throw new Error(`clingo-wasm loaded from ${moduleUrl} but exposed no run()`);
            }
            return clingo;
        })
        .catch((err) => {
            clingoLoadPromise = null; // allow retry
            throw new Error(`Failed to load clingo-wasm from ${moduleUrl}: ${err.message || err}`);
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

/** What clingo-wasm >= 0.6.0 resolves an in-flight `run()` with when
 * `restart()` interrupts it -- a cancel, not a solver error. */
const ABORTED_BY_RESTART = 'Aborted by restart().';

/**
 * Cap on clasp's parallel search threads.
 *
 * Threads need SharedArrayBuffer, so in a browser the page must be
 * cross-origin isolated -- which this app deliberately is not: measured on
 * the full course, `--parallel-mode=4` cut a solve from ~4.3s to ~3.5s,
 * which does not justify putting COEP on the board page. We still ask for
 * parallel search where it is already available (the Node test path), since
 * there it costs nothing. 4 rather than `hardwareConcurrency` because 8
 * measured *slower* than 4 -- clasp's portfolio oversubscribes here.
 */
const MAX_SOLVER_THREADS = 4;

/** clingo CLI options for a solve, given what the runtime actually supports. */
export function solverOptions(clingo) {
    if (typeof clingo?.supportsThreads !== 'function' || !clingo.supportsThreads()) return [];
    const cores = (typeof navigator !== 'undefined' && navigator.hardwareConcurrency) || MAX_SOLVER_THREADS;
    return [`--parallel-mode=${Math.max(1, Math.min(MAX_SOLVER_THREADS, cores))}`];
}

/**
 * One clingo-wasm worker handle: wraps `run()` in a cancellation race and
 * exposes `cancel()`, which unblocks the caller immediately and restarts the
 * worker so the next `run()` starts clean. The race also turns a cancel into
 * a typed SolverCancelledError rather than something to string-match.
 *
 * IMPORTANT: pass `models = 0`, not 1. `models` is clasp's `-n`, which caps
 * models *printed*, so `-n1` stops at the first merely-feasible model and
 * reports SATISFIABLE, never proving optimality. 0 does not mean "enumerate
 * everything" -- branch-and-bound only prints strictly improving models.
 * An easy regression to reintroduce.
 *
 * Improving models are numerous (~105 on the full course) and each is a
 * different assignment set, not a polish of the last, so any UI painting
 * them must throttle. That is leg-solver-ui.js's job.
 *
 * `loader` is injectable so tests can drive this without a real CDN.
 */
export function createSolverHandle({ moduleUrl = CLINGO_MODULE_URL, loader = loadClingo } = {}) {
    let cancelReject = null;
    let initialized = false;

    async function ensureReady() {
        const clingo = await loader(moduleUrl);
        if (!initialized) {
            // No wasm url: let clingo-wasm pick its single- or multi-threaded
            // binary itself (see CLINGO_MODULE_URL's comment).
            await clingo.init();
            initialized = true;
        }
        return clingo;
    }

    async function run(program, models = 0, { onModel = undefined } = {}) {
        const clingo = await ensureReady();
        const cancelPromise = new Promise((_resolve, reject) => {
            cancelReject = reject;
        });
        try {
            const solve = clingo.run(program, models, solverOptions(clingo), onModel);
            return await Promise.race([solve, cancelPromise]);
        } finally {
            cancelReject = null;
        }
    }

    async function cancel(reason) {
        if (cancelReject) cancelReject(new SolverCancelledError(reason));
        const clingo = await loader(moduleUrl);
        await clingo.restart();
        initialized = true; // restart() already re-initializes
    }

    return { run, cancel };
}

/** True if a resolved result is really "we interrupted it". */
export function isAbortedResult(result) {
    return Boolean(result && result.Result === 'ERROR' && result.Error === ABORTED_BY_RESTART);
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
 *
 * `options.singleRunnerPerLeg` adds a hard per-leg headcount cap for
 * captains who would rather the solver not double anyone up.
 */
export function generateFacts(state, options = {}) {
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
    const pinnedCountByLegId = new Map();
    course.legs.forEach((leg, legId) => {
        const key = legKey(leg);
        const membershipIds = (assignments && assignments[key]) || [];
        for (const membershipId of membershipIds) {
            lines.push(`assignment(${quoteAtomString(membershipId)},leg(${legId},${leg.start.id},${leg.end.id})).`);
            pinnedPairs.add(pairKey(key, membershipId));
        }
        pinnedCountByLegId.set(legId, membershipIds.length);
    });

    if (options.singleRunnerPerLeg) {
        // max(1, pins already there): the solver may fill an empty leg but
        // never double it up, while a leg the captain already stacked keeps
        // exactly that headcount rather than going UNSAT.
        course.legs.forEach((leg, legId) => {
            const cap = Math.max(1, pinnedCountByLegId.get(legId) || 0);
            lines.push(`runnerCap(${legId},${cap}).`);
        });
        lines.push(':- legCoverage(T,C), C > N, runnerCap(T,N), leg(T,_,_).');
    }

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

/** One witness -> the new placements it implies. Streamed and final
 * witnesses share a shape, so both paths use this. */
export function witnessToSuggestions(witness, pinnedPairs) {
    return diffSuggestions(parseAssignments(witness && witness.Value), pinnedPairs);
}

// ---- Top-level orchestration -----------------------------------------------

/**
 * Run "Optimize remaining" against `state` using a handle from
 * `createSolverHandle`. Returns `{ status, ... }` where status is:
 *   ok          -- ran to completion; `optimal` says whether clingo proved
 *                  it (OPTIMUM FOUND) rather than just SATISFIABLE
 *   unsat       -- no model at all; rare given the at-least-1 relaxation
 *   cancelled   -- Cancel or timeout; `suggestions` is the best plan
 *                  streamed before the stop, usable but not proven optimal,
 *                  and empty if the stop landed during grounding
 *   unavailable -- couldn't load clingo-wasm or fetch the ASP sources, so
 *                  the caller should disable the button rather than let the
 *                  captain retry a hopeless load
 *   error       -- clingo ran but the program itself errored; retrying
 *                  won't help, but the solver isn't "unavailable" either
 *
 * Touches neither the DOM nor the board's real state.
 */
export async function optimizeRemaining(
    state, handle,
    { fetchSources = fetchAspSources, onModel = null, singleRunnerPerLeg = false } = {},
) {
    const { program: factsProgram, pinnedPairs } = generateFacts(state, { singleRunnerPerLeg });
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

    // Best model streamed so far. Kept outside the try so a cancellation --
    // which unwinds through the catch below -- can still hand it back rather
    // than throwing away however many seconds of solving already happened.
    let bestSoFar = null;
    let modelCount = 0;
    const collect = (witness) => {
        modelCount += 1;
        bestSoFar = witnessToSuggestions(witness, pinnedPairs);
        if (onModel) onModel({ index: modelCount, suggestions: bestSoFar });
    };

    let result;
    try {
        // 0 = search to a proven optimum, see createSolverHandle's docstring
        result = await handle.run(program, 0, { onModel: collect });
    } catch (err) {
        if (err instanceof SolverCancelledError) {
            return { status: 'cancelled', suggestions: bestSoFar || [], modelCount, optimal: false };
        }
        // handle.run() only throws (other than our own cancellation) if
        // ensureReady() couldn't import the CDN module or init the wasm
        // module -- clingo's own worker protocol otherwise always resolves
        // (even a program error comes back as a normal `{Result: 'ERROR'}`
        // payload, handled below).
        return { status: 'unavailable', message: err.message || String(err) };
    }

    // A restart() that beat our own cancellation race to the punch: still a
    // cancel, not a solver bug (clingo-wasm >= 0.6.0, see isAbortedResult).
    if (isAbortedResult(result)) {
        return { status: 'cancelled', suggestions: bestSoFar || [], modelCount, optimal: false };
    }
    if (!result || result.Result === 'ERROR') {
        return { status: 'error', message: (result && result.Error) || 'clingo reported an error.' };
    }
    if (result.Result === 'UNSATISFIABLE') {
        return {
            status: 'unsat',
            message: singleRunnerPerLeg
                ? 'No valid assignment exists with "one runner per leg" and the current pins -- there likely '
                    + 'aren\'t enough active members left to give every remaining leg its own runner. Uncheck '
                    + 'the option, or free up a member, and try again.'
                : 'No valid assignment exists for the current members and pins (unexpected given the '
                    + 'at-least-1 coverage relaxation -- check whether the team has any active members left).',
        };
    }

    // Prefer the final result's own witness (authoritative), falling back to
    // the last streamed one if a build ever stops echoing witnesses.
    const witness = bestWitness(result);
    const suggestions = witness ? witnessToSuggestions(witness, pinnedPairs) : bestSoFar;
    if (!suggestions) {
        return { status: 'error', message: `clingo returned no model (Result: ${result.Result}).` };
    }

    return {
        status: 'ok',
        suggestions,
        modelCount,
        optimal: result.Result === 'OPTIMUM FOUND',
    };
}
