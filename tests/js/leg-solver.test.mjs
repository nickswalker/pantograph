// Smoke + unit tests for app/static/js/leg-solver.js (clingo-wasm solver
// integration).
//
// Two halves, matching leg-solver.js's own split:
//   1. Pure function tests (fact generation, atom parsing, suggestion
//      diffing) -- no clingo involved, fast, deterministic.
//   2. A real smoke test that feeds the vendored app/static/asp/*.lp files
//      + a realistic generated fact set (a slice of the real
//      data/legs_2026.json course, ~4-6 members with varied preferences,
//      several pins including a two-runner leg) through the actual npm
//      `clingo-wasm` package running in Node, and asserts on the solved
//      model. This is what the plan calls "smoke-test the vendored domain
//      + a realistic generated fact set in Node ... before wiring the UI".
//
// Requires `clingo-wasm` to be installed in tests/js/node_modules --run
// `npm install` in this directory once (see tests/js/package.json). The
// app itself never depends on this; it loads clingo-wasm from jsDelivr at
// runtime as a progressive enhancement.
//
// Run with plain Node (v18+):
//   node tests/js/leg-solver.test.mjs

import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

import {
    generateFacts,
    parseAssignments,
    diffSuggestions,
    buildProgram,
    bestWitness,
    createSolverHandle,
    SolverCancelledError,
} from '../../app/static/js/leg-solver.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '..', '..');

const DOMAIN_SOURCE = readFileSync(path.join(REPO_ROOT, 'app/static/asp/scheduling-domain.lp'), 'utf8');
const TEAM_SOURCE = readFileSync(path.join(REPO_ROOT, 'app/static/asp/team-assign.lp'), 'utf8');
const COURSE_JSON = JSON.parse(readFileSync(path.join(REPO_ROOT, 'data/legs_2026.json'), 'utf8'));

// ---------------------------------------------------------------------------
// Build a WP3/WP4-shaped `course` object from the raw data/legs_2026.json
// build artifact, the same way app/services/assignment_service.py's
// `_serialize_course` does server-side (id/name endpoints, commute matrix,
// station_index combining real names + aliases + non-course stations).
// ---------------------------------------------------------------------------
function buildCourse(rawCourse, legSlice) {
    const exchangesById = new Map(rawCourse.exchanges.map((e) => [e.id, e]));
    const endpoint = (id) => ({ id, name: exchangesById.get(id) ? exchangesById.get(id).name : null });

    const stationIndex = {};
    for (const exchange of rawCourse.exchanges) stationIndex[exchange.name] = exchange.id;
    Object.assign(stationIndex, rawCourse.station_aliases || {});
    Object.assign(stationIndex, rawCourse.non_course_stations || {});

    const legs = (legSlice || rawCourse.legs).map((leg) => ({
        index: leg.index,
        start: endpoint(leg.start),
        end: endpoint(leg.end),
        distance: leg.distance,
        ascent: leg.ascent,
        descent: leg.descent,
    }));

    return {
        event: rawCourse.event,
        units: rawCourse.units,
        legs,
        commute: rawCourse.commute,
        station_index: stationIndex,
        estimated_duration_seconds: null,
    };
}

// A realistic ~6-leg slice (legs 0-5 in running order, so consecutive-leg
// "exchange count" logic is exercised) rather than the full 22, to keep the
// smoke test's solve fast while still real course data end to end.
const SLICE_LEGS = COURSE_JSON.legs.slice(0, 6);
const SMALL_COURSE = buildCourse(COURSE_JSON, SLICE_LEGS);
const FULL_COURSE = buildCourse(COURSE_JSON, COURSE_JSON.legs);

function member(overrides) {
    return {
        membership_id: overrides.membership_id,
        name: overrides.name || overrides.membership_id,
        status: 'active',
        willing_to_lead: false,
        preferred_miles: null,
        planned_pace_seconds: null,
        preferred_station: null,
        ...overrides,
    };
}

// 4 members with varied preferences.
const MEMBERS = [
    member({ membership_id: 'm1', willing_to_lead: true, preferred_miles: 3.0, planned_pace_seconds: 600 }),
    member({ membership_id: 'm2', preferred_miles: 5.0, planned_pace_seconds: 700, preferred_station: 'Boeing Access Road' }), // non-course -- should be skipped
    member({ membership_id: 'm3', planned_pace_seconds: 550 }), // no distance/station preference at all
    member({ membership_id: 'm4', willing_to_lead: true, preferred_station: 'U-District' }), // alias, resolves if on this slice
];

function buildState(course, members, assignments) {
    return { course, members, assignments };
}

// ---------------------------------------------------------------------------
// 1. Pure function tests
// ---------------------------------------------------------------------------

test('generateFacts: emits participant/1 for every member, quoted', () => {
    const { program } = generateFacts(buildState(SMALL_COURSE, MEMBERS, {}));
    for (const m of MEMBERS) {
        assert.match(program, new RegExp(`participant\\("${m.membership_id}"\\)\\.`));
    }
});

test('generateFacts: leg/distance/ascent/descent come straight from course.legs', () => {
    const { program } = generateFacts(buildState(SMALL_COURSE, MEMBERS, {}));
    const leg0 = SMALL_COURSE.legs[0];
    assert.match(program, new RegExp(`leg\\(0,${leg0.start.id},${leg0.end.id}\\)\\.`));
    assert.match(program, new RegExp(`distance\\(${leg0.start.id},${leg0.end.id},${leg0.distance}\\)\\.`));
    assert.match(program, new RegExp(`ascent\\(${leg0.start.id},${leg0.end.id},${leg0.ascent}\\)\\.`));
    assert.match(program, new RegExp(`descent\\(${leg0.start.id},${leg0.end.id},${leg0.descent}\\)\\.`));
});

test('generateFacts: commuteDistance is symmetrized plus self-distance-0', () => {
    const { program } = generateFacts(buildState(SMALL_COURSE, MEMBERS, {}));
    const [a, b, dist] = SMALL_COURSE.commute[0];
    assert.match(program, new RegExp(`commuteDistance\\(${a},${b},${dist}\\)\\.`));
    assert.match(program, new RegExp(`commuteDistance\\(${b},${a},${dist}\\)\\.`));
    assert.match(program, new RegExp(`commuteDistance\\(${a},${a},0\\)\\.`));
});

test('generateFacts: preference facts only emitted for members who stated them', () => {
    const { program } = generateFacts(buildState(SMALL_COURSE, MEMBERS, {}));

    // m1: distance (3.0mi -> 300 hundredths) + pace (600) + willingToLead
    assert.match(program, /preferredDistance\("m1",300\)\./);
    assert.match(program, /preferredPace\("m1",600\)\./);
    assert.match(program, /willingToLead\("m1"\)\./);

    // m2: distance (5.0mi -> 500) + pace (700), but preferred_station is a
    // non-course station (Boeing Access Road, id 162, no leg on this slice
    // or any slice) -- must be skipped entirely, not emitted as a bogus fact.
    assert.match(program, /preferredDistance\("m2",500\)\./);
    assert.match(program, /preferredPace\("m2",700\)\./);
    assert.doesNotMatch(program, /preferredEndExchange\("m2",/);

    // m3: no distance/station preference at all -> only pace, no
    // preferredDistance/preferredEndExchange/willingToLead fact for m3.
    assert.doesNotMatch(program, /preferredDistance\("m3",/);
    assert.doesNotMatch(program, /preferredEndExchange\("m3",/);
    assert.doesNotMatch(program, /willingToLead\("m3"\)\./);

    // m4: no distance/pace, willing to lead, and preferred_station is an
    // alias ("U-District" -> id 147) -- only emitted if 147 is a course
    // exchange on this slice (it isn't, in legs 0-5, since those are in the
    // 160s); confirm it's correctly omitted here, then re-check against the
    // full course below where 147 *is* a leg endpoint.
    assert.doesNotMatch(program, /preferredEndExchange\("m4",147\)\./);

    const { program: fullProgram } = generateFacts(buildState(FULL_COURSE, MEMBERS, {}));
    assert.match(fullProgram, /preferredEndExchange\("m4",147\)\./);
    assert.doesNotMatch(fullProgram, /preferredEndExchange\("m2",/); // still non-course
});

test('generateFacts: pins are emitted verbatim as assignment/2 facts and tracked in pinnedPairs', () => {
    const leg0 = SMALL_COURSE.legs[0];
    const leg1 = SMALL_COURSE.legs[1];
    const assignments = { [leg0.index]: ['m1', 'm3'], [leg1.index]: ['m2'] };
    const { program, pinnedPairs } = generateFacts(buildState(SMALL_COURSE, MEMBERS, assignments));

    assert.match(program, new RegExp(`assignment\\("m1",leg\\(${leg0.index},${leg0.start.id},${leg0.end.id}\\)\\)\\.`));
    assert.match(program, new RegExp(`assignment\\("m3",leg\\(${leg0.index},${leg0.start.id},${leg0.end.id}\\)\\)\\.`));
    assert.match(program, new RegExp(`assignment\\("m2",leg\\(${leg1.index},${leg1.start.id},${leg1.end.id}\\)\\)\\.`));

    assert.ok(pinnedPairs.has(`${leg0.index}::m1`));
    assert.ok(pinnedPairs.has(`${leg0.index}::m3`));
    assert.ok(pinnedPairs.has(`${leg1.index}::m2`));
    assert.equal(pinnedPairs.size, 3);
});

test('generateFacts: no course/members -> empty program, not a crash', () => {
    assert.deepEqual(generateFacts({}), { program: '', pinnedPairs: new Set() });
    assert.deepEqual(generateFacts(null), { program: '', pinnedPairs: new Set() });
});

test('parseAssignments: extracts membershipId + legIndex, ignores non-assignment atoms', () => {
    const values = [
        'participant("m1")',
        'leg(0,140,141)',
        'assignment("m1",leg(0,140,141))',
        'assignment("m2",leg(1,141,142))',
        'legCoverage(0,1)',
    ];
    assert.deepEqual(parseAssignments(values), [
        { membershipId: 'm1', legIndex: 0 },
        { membershipId: 'm2', legIndex: 1 },
    ]);
});

test('diffSuggestions: drops pinned pairs and de-duplicates', () => {
    const solved = [
        { membershipId: 'm1', legIndex: 0 }, // pinned, should be dropped
        { membershipId: 'm2', legIndex: 1 }, // new suggestion
        { membershipId: 'm2', legIndex: 1 }, // duplicate of the above
        { membershipId: 'm3', legIndex: 2 }, // new suggestion
    ];
    const pinnedPairs = new Set(['0::m1']);
    assert.deepEqual(diffSuggestions(solved, pinnedPairs), [
        { legIndex: 1, membershipId: 'm2' },
        { legIndex: 2, membershipId: 'm3' },
    ]);
});

test('buildProgram: concatenates domain + team + facts with separators', () => {
    const program = buildProgram('domain.', 'team.', 'facts.');
    assert.equal(program, 'domain.\nteam.\nfacts.');
});

test('bestWitness: returns the last witness of the last call, or null', () => {
    assert.equal(bestWitness(null), null);
    assert.equal(bestWitness({ Call: [] }), null);
    const result = {
        Call: [
            { Witnesses: [{ Value: ['a'] }, { Value: ['a', 'b'] }] },
        ],
    };
    assert.deepEqual(bestWitness(result), { Value: ['a', 'b'] });
});

// ---------------------------------------------------------------------------
// 2. Cancellation plumbing (stubbed clingo -- no real wasm needed) --
//    verifies createSolverHandle's Promise.race actually rejects promptly
//    with SolverCancelledError, since clingo-wasm's own worker.terminate()
//    would otherwise just leave the caller hanging forever (see the
//    comment in leg-solver.js above createSolverHandle).
// ---------------------------------------------------------------------------

test('createSolverHandle: cancel() rejects an in-flight run with SolverCancelledError', async () => {
    let restarted = false;
    const fakeClingo = {
        async init() {},
        async restart() { restarted = true; },
        run() {
            // Never resolves on its own -- simulates a long/stuck solve.
            return new Promise(() => {});
        },
    };

    global.window = { clingo: fakeClingo };
    global.document = {
        createElement: () => ({}),
        head: { appendChild() {} },
    };
    try {
        const handle = createSolverHandle();
        const runPromise = handle.run('a.', 1);
        // Give run() a tick to reach clingo.run() before cancelling.
        await new Promise((resolve) => setTimeout(resolve, 10));
        await handle.cancel('test cancel');
        await assert.rejects(runPromise, SolverCancelledError);
        assert.equal(restarted, true);
    } finally {
        delete global.window;
        delete global.document;
    }
});

// ---------------------------------------------------------------------------
// 3. Real clingo-wasm smoke test
// ---------------------------------------------------------------------------

test('smoke: vendored domain + generated facts solve SAT, cover all legs, preserve pins', async (t) => {
    let clingoRun;
    try {
        ({ run: clingoRun } = (await import('clingo-wasm')).default);
    } catch (err) {
        t.skip(`clingo-wasm not installed in tests/js/node_modules (run "npm install" in tests/js): ${err.message}`);
        return;
    }

    const leg0 = SMALL_COURSE.legs[0]; // pin: m1 solo
    const leg1 = SMALL_COURSE.legs[1]; // pin: m2 + m3 together (two-runner leg)
    const assignments = {
        [leg0.index]: ['m1'],
        [leg1.index]: ['m2', 'm3'],
    };
    const state = buildState(SMALL_COURSE, MEMBERS, assignments);
    const { program: factsProgram, pinnedPairs } = generateFacts(state);
    const program = buildProgram(DOMAIN_SOURCE, TEAM_SOURCE, factsProgram);

    // models=0, not 1: clasp's branch-and-bound optimizer needs an
    // unbounded model count to actually search to a *proven* optimum --
    // `-n1` stops at the first feasible model and reports plain
    // SATISFIABLE without ever comparing it to a better one (see
    // createSolverHandle's docstring in leg-solver.js for how this was
    // discovered). Assert the strict OPTIMUM FOUND here specifically so a
    // future regression back to `models=1` fails loudly.
    const result = await clingoRun(program, 0);

    assert.equal(
        result.Result, 'OPTIMUM FOUND',
        `expected a proven-optimal result, got ${result.Result}: ${JSON.stringify(result.Warnings)}`,
    );

    const witness = bestWitness(result);
    assert.ok(witness, 'expected at least one witness/model');

    const solved = parseAssignments(witness.Value);

    // All legs covered (at least one runner each, including the pins).
    const runnersByLeg = new Map();
    for (const { membershipId, legIndex } of solved) {
        if (!runnersByLeg.has(legIndex)) runnersByLeg.set(legIndex, new Set());
        runnersByLeg.get(legIndex).add(membershipId);
    }
    for (const leg of SMALL_COURSE.legs) {
        const runners = runnersByLeg.get(leg.index);
        assert.ok(runners && runners.size >= 1, `leg ${leg.index} has no runner assigned`);
    }

    // No duplicate member-on-same-leg (the choice rule is 0/1 per atom, but
    // assert on the actual parsed output to catch any parsing regressions).
    for (const [legIndex, runners] of runnersByLeg) {
        const count = solved.filter((s) => s.legIndex === legIndex && s.membershipId === [...runners][0]).length;
        assert.ok(count <= runners.size, `leg ${legIndex} lists a member more than once`);
    }
    const seenPairs = new Set();
    for (const { membershipId, legIndex } of solved) {
        const key = `${legIndex}::${membershipId}`;
        assert.ok(!seenPairs.has(key), `duplicate (leg, member) pair in solved model: ${key}`);
        seenPairs.add(key);
    }

    // Pins preserved verbatim.
    for (const key of pinnedPairs) {
        const [legIndexStr, membershipId] = key.split('::');
        assert.ok(
            solved.some((s) => s.legIndex === Number(legIndexStr) && s.membershipId === membershipId),
            `pin ${key} missing from solved model`,
        );
    }

    // The pinned two-runner leg (leg1) stays intact: both m2 and m3, still there.
    const leg1Runners = runnersByLeg.get(leg1.index);
    assert.ok(leg1Runners.has('m2') && leg1Runners.has('m3'), 'pinned two-runner leg lost a runner');

    // Suggestions (non-pinned atoms) only ever place someone on a leg that
    // had no pin, or add a runner alongside pins -- never remove a pin.
    const suggestions = diffSuggestions(solved, pinnedPairs);
    for (const s of suggestions) {
        assert.ok(!pinnedPairs.has(`${s.legIndex}::${s.membershipId}`));
    }
});

test('smoke: full 22-leg course also solves SAT with a realistic 4-member team', async (t) => {
    let clingoRun;
    try {
        ({ run: clingoRun } = (await import('clingo-wasm')).default);
    } catch (err) {
        t.skip(`clingo-wasm not installed: ${err.message}`);
        return;
    }

    const state = buildState(FULL_COURSE, MEMBERS, {});
    const { program: factsProgram } = generateFacts(state);
    const program = buildProgram(DOMAIN_SOURCE, TEAM_SOURCE, factsProgram);

    const result = await clingoRun(program, 0); // see the models=0 note above
    assert.equal(
        result.Result, 'OPTIMUM FOUND',
        `expected a proven-optimal result on the full course, got ${result.Result}`,
    );

    const witness = bestWitness(result);
    const solved = parseAssignments(witness.Value);
    const coveredLegs = new Set(solved.map((s) => s.legIndex));
    for (const leg of FULL_COURSE.legs) {
        assert.ok(coveredLegs.has(leg.index), `leg ${leg.index} uncovered on the full course`);
    }
});

test('smoke: zero active members reports UNSAT cleanly (contrived, given the at-least-1 relaxation)', async (t) => {
    let clingoRun;
    try {
        ({ run: clingoRun } = (await import('clingo-wasm')).default);
    } catch (err) {
        t.skip(`clingo-wasm not installed: ${err.message}`);
        return;
    }

    // A team with legs but no members at all: at-least-1 coverage can never
    // be satisfied, so this should be a clean, distinguishable UNSAT rather
    // than a hang/timeout.
    const state = buildState(SMALL_COURSE, [], {});
    const { program: factsProgram } = generateFacts(state);
    const program = buildProgram(DOMAIN_SOURCE, TEAM_SOURCE, factsProgram);

    const result = await clingoRun(program, 0);
    assert.equal(result.Result, 'UNSATISFIABLE');
});
