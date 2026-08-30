// Smoke + unit tests for app/static/js/leg-solver.js (clingo-wasm solver
// integration).
//
// Two halves, matching leg-solver.js's own split:
//   1. Pure function tests (fact generation, atom parsing, suggestion
//      diffing) -- no clingo involved, fast, deterministic.
//   2. A real smoke test that feeds the vendored app/static/asp/*.lp files
//      + a realistic generated fact set (a slice of the real
//      data/lrr2026.geojson course, ~4-6 members with varied preferences,
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
    witnessToSuggestions,
    createSolverHandle,
    optimizeRemaining,
    solverOptions,
    isAbortedResult,
    SolverCancelledError,
} from '../../app/static/js/leg-solver.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(__dirname, '..', '..');

const DOMAIN_SOURCE = readFileSync(path.join(REPO_ROOT, 'app/static/asp/scheduling-domain.lp'), 'utf8');
const TEAM_SOURCE = readFileSync(path.join(REPO_ROOT, 'app/static/asp/team-assign.lp'), 'utf8');
const COURSE_GEOJSON = JSON.parse(readFileSync(path.join(REPO_ROOT, 'data/lrr2026.geojson'), 'utf8'));

// ---------------------------------------------------------------------------
// Build a board-shaped `course` object from data/lrr2026.geojson, mirroring
// app/services/course_service.py: stations are the Points carrying
// stationInfo (the point-of-interest Points are not exchanges), legs are the
// LineStrings, distances are scaled to integer hundredths of a mile, and the
// commute matrix is straight-line between exchanges since the file has none.
// ---------------------------------------------------------------------------
const LINE_1 = 'lrr_1line';

function scaleMiles(miles) {
    return Math.ceil(Number(miles) * 100);
}

function haversineMiles([lonA, latA], [lonB, latB]) {
    const toRad = (d) => (d * Math.PI) / 180;
    const phiA = toRad(latA);
    const phiB = toRad(latB);
    const dPhi = phiB - phiA;
    const dLambda = toRad(lonB - lonA);
    const h = Math.sin(dPhi / 2) ** 2 + Math.cos(phiA) * Math.cos(phiB) * Math.sin(dLambda / 2) ** 2;
    return 2 * 3958.7613 * Math.asin(Math.sqrt(h));
}

function courseFeatures() {
    const stations = new Map();
    const legs = [];
    for (const feature of COURSE_GEOJSON.features) {
        const props = feature.properties || {};
        const geometry = feature.geometry || {};
        if (geometry.type === 'Point' && 'stationInfo' in props) {
            stations.set(props.id, { id: props.id, name: props.name, coordinates: geometry.coordinates });
        } else if (geometry.type === 'LineString') {
            legs.push(props);
        }
    }
    return { stations, legs };
}

/** `line` defaults to the 1 Line; `legSlice` trims to the first N legs. */
function buildCourse(line = LINE_1, legCount = null) {
    const { stations, legs } = courseFeatures();
    const endpoint = (id) => ({ id, name: stations.has(id) ? stations.get(id).name : null });

    const onLine = legs
        .filter((leg) => (leg.lines || []).includes(line))
        .sort((a, b) => a.sequence[a.lines.indexOf(line)] - b.sequence[b.lines.indexOf(line)]);
    const selected = legCount === null ? onLine : onLine.slice(0, legCount);

    const serializedLegs = selected.map((leg) => ({
        start: endpoint(leg.start_exchange),
        end: endpoint(leg.end_exchange),
        distance: scaleMiles(leg.distance_mi),
        ascent: Math.trunc(leg.ascent_ft),
        descent: Math.trunc(leg.descent_ft),
        lines: leg.lines,
        sequence: Object.fromEntries(leg.lines.map((l, i) => [l, leg.sequence[i]])),
    }));

    const ids = [...new Set(serializedLegs.flatMap((leg) => [leg.start.id, leg.end.id]))].sort((a, b) => a - b);
    const commute = [];
    for (let i = 0; i < ids.length; i += 1) {
        for (let j = i + 1; j < ids.length; j += 1) {
            commute.push([ids[i], ids[j],
                scaleMiles(haversineMiles(stations.get(ids[i]).coordinates, stations.get(ids[j]).coordinates))]);
        }
    }

    const stationIndex = {};
    for (const station of stations.values()) stationIndex[station.name] = station.id;

    return {
        event: 'lrr2026',
        units: {},
        legs: serializedLegs,
        commute,
        station_index: stationIndex,
        estimated_duration_seconds: null,
    };
}

// A realistic ~6-leg slice (the first six in running order, so consecutive-leg
// "exchange count" logic is exercised) rather than the full 1 Line, to keep
// the smoke test's solve fast while still real course data end to end.
const SMALL_COURSE = buildCourse(LINE_1, 6);
const FULL_COURSE = buildCourse(LINE_1);

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
    member({ membership_id: 'm2', preferred_miles: 5.0, planned_pace_seconds: 700, preferred_station: 'Bellevue Downtown' }), // 2 Line only -- off this course, should be skipped
    member({ membership_id: 'm3', planned_pace_seconds: 550 }), // no distance/station preference at all
    member({ membership_id: 'm4', willing_to_lead: true, preferred_station: 'U District' }), // 1 Line trunk, resolves only on the full course
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

    // m2: distance (5.0mi -> 500) + pace (700), but preferred_station is on
    // the 2 Line only (Bellevue Downtown), so it is not an exchange of this
    // 1 Line course -- must be skipped entirely, not emitted as a bogus fact.
    assert.match(program, /preferredDistance\("m2",500\)\./);
    assert.match(program, /preferredPace\("m2",700\)\./);
    assert.doesNotMatch(program, /preferredEndExchange\("m2",/);

    // m3: no distance/station preference at all -> only pace, no
    // preferredDistance/preferredEndExchange/willingToLead fact for m3.
    assert.doesNotMatch(program, /preferredDistance\("m3",/);
    assert.doesNotMatch(program, /preferredEndExchange\("m3",/);
    assert.doesNotMatch(program, /willingToLead\("m3"\)\./);

    // m4: no distance/pace, willing to lead, and preferred_station is
    // U District (1247), on the shared trunk. The 6-leg slice stops in the
    // 160s, so it is not an exchange here; confirm it's omitted, then
    // re-check against the full 1 Line where 1247 *is* a leg endpoint.
    assert.doesNotMatch(program, /preferredEndExchange\("m4",1247\)\./);

    const { program: fullProgram } = generateFacts(buildState(FULL_COURSE, MEMBERS, {}));
    assert.match(fullProgram, /preferredEndExchange\("m4",1247\)\./);
    assert.doesNotMatch(fullProgram, /preferredEndExchange\("m2",/); // still off this line
});

test('generateFacts: pins are emitted verbatim as assignment/2 facts and tracked in pinnedPairs', () => {
    const leg0 = SMALL_COURSE.legs[0];
    const leg1 = SMALL_COURSE.legs[1];
    const key0 = `${leg0.start.id}-${leg0.end.id}`;
    const key1 = `${leg1.start.id}-${leg1.end.id}`;
    const assignments = { [key0]: ['m1', 'm3'], [key1]: ['m2'] };
    const { program, pinnedPairs } = generateFacts(buildState(SMALL_COURSE, MEMBERS, assignments));

    // The leg/3 id is the leg's position in course.legs.
    assert.match(program, new RegExp(`assignment\\("m1",leg\\(0,${leg0.start.id},${leg0.end.id}\\)\\)\\.`));
    assert.match(program, new RegExp(`assignment\\("m3",leg\\(0,${leg0.start.id},${leg0.end.id}\\)\\)\\.`));
    assert.match(program, new RegExp(`assignment\\("m2",leg\\(1,${leg1.start.id},${leg1.end.id}\\)\\)\\.`));

    assert.ok(pinnedPairs.has(`${key0}::m1`));
    assert.ok(pinnedPairs.has(`${key0}::m3`));
    assert.ok(pinnedPairs.has(`${key1}::m2`));
    assert.equal(pinnedPairs.size, 3);
});

test('generateFacts: no course/members -> empty program, not a crash', () => {
    assert.deepEqual(generateFacts({}), { program: '', pinnedPairs: new Set() });
    assert.deepEqual(generateFacts(null), { program: '', pinnedPairs: new Set() });
});

test('generateFacts: singleRunnerPerLeg appends a per-leg headcount cap, off by default', () => {
    const state = buildState(SMALL_COURSE, MEMBERS, {});
    const { program: defaultProgram } = generateFacts(state);
    assert.ok(!defaultProgram.includes('runnerCap('));
    assert.ok(!defaultProgram.includes('legCoverage(T,C), C > N'));

    const { program: cappedProgram } = generateFacts(state, { singleRunnerPerLeg: true });
    assert.ok(cappedProgram.includes(':- legCoverage(T,C), C > N, runnerCap(T,N), leg(T,_,_).'));
    // No pins at all -- every leg's cap should be the floor of 1.
    for (let legId = 0; legId < SMALL_COURSE.legs.length; legId += 1) {
        assert.ok(cappedProgram.includes(`runnerCap(${legId},1).`), `leg ${legId} should default to cap 1`);
    }
});

test('generateFacts: singleRunnerPerLeg raises the cap to match a leg already pinned with more than one runner', () => {
    const leg1 = SMALL_COURSE.legs[1];
    const leg1Key = `${leg1.start.id}-${leg1.end.id}`;
    const state = buildState(SMALL_COURSE, MEMBERS, { [leg1Key]: ['m2', 'm3'] });

    const { program } = generateFacts(state, { singleRunnerPerLeg: true });
    assert.ok(program.includes('runnerCap(1,2).'), 'leg1 (index 1) should be capped at its existing 2 pins');
    assert.ok(program.includes('runnerCap(0,1).'), 'an unrelated leg should still default to cap 1');
});

test('parseAssignments: extracts membershipId + legKey, ignores non-assignment atoms', () => {
    const values = [
        'participant("m1")',
        'leg(0,140,141)',
        'assignment("m1",leg(0,140,141))',
        'assignment("m2",leg(1,141,142))',
        'legCoverage(0,1)',
    ];
    // Identity comes from the exchanges in the atom, not the leg id.
    assert.deepEqual(parseAssignments(values), [
        { membershipId: 'm1', legKey: '140-141' },
        { membershipId: 'm2', legKey: '141-142' },
    ]);
});

test('diffSuggestions: drops pinned pairs and de-duplicates', () => {
    const solved = [
        { membershipId: 'm1', legKey: '140-141' }, // pinned, should be dropped
        { membershipId: 'm2', legKey: '141-142' }, // new suggestion
        { membershipId: 'm2', legKey: '141-142' }, // duplicate of the above
        { membershipId: 'm3', legKey: '142-143' }, // new suggestion
    ];
    const pinnedPairs = new Set(['140-141::m1']);
    assert.deepEqual(diffSuggestions(solved, pinnedPairs), [
        { legKey: '141-142', membershipId: 'm2' },
        { legKey: '142-143', membershipId: 'm3' },
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
// 2. Cancellation + streaming plumbing (fake clingo -- no real wasm needed).
//
//    createSolverHandle takes an injectable `loader`, so these drive the
//    handle with a stub instead of the old approach of faking `window` and
//    `document` to satisfy a <script>-injection loader -- clingo-wasm 0.6.0
//    is an ES module with no `window.clingo` global, so that stub no longer
//    corresponds to anything real.
// ---------------------------------------------------------------------------

/** A fake clingo whose run() never settles on its own, so a test can cancel it. */
function stuckClingo() {
    const calls = { restarted: 0, options: null, onModel: null };
    const clingo = {
        async init() {},
        async restart() { calls.restarted += 1; },
        run(_program, _models, options, onModel) {
            calls.options = options;
            calls.onModel = onModel;
            return new Promise(() => {});
        },
    };
    return { clingo, calls, loader: async () => clingo };
}

test('createSolverHandle: cancel() rejects an in-flight run with SolverCancelledError', async () => {
    const { calls, loader } = stuckClingo();
    const handle = createSolverHandle({ loader });

    const runPromise = handle.run('a.', 1);
    // Give run() a tick to reach clingo.run() before cancelling.
    await new Promise((resolve) => setTimeout(resolve, 10));
    await handle.cancel('test cancel');

    await assert.rejects(runPromise, SolverCancelledError);
    assert.equal(calls.restarted, 1);
});

test('createSolverHandle: forwards onModel through to clingo.run', async () => {
    const { calls, loader } = stuckClingo();
    const handle = createSolverHandle({ loader });
    const onModel = () => {};

    handle.run('a.', 0, { onModel });
    await new Promise((resolve) => setTimeout(resolve, 10));

    assert.equal(calls.onModel, onModel);
});

test('solverOptions: asks for parallel search only when threads are available', () => {
    assert.deepEqual(solverOptions({ supportsThreads: () => false }), []);
    assert.deepEqual(solverOptions({}), []);
    // Capped at 4 regardless of core count -- 8 measured slower than 4.
    assert.deepEqual(solverOptions({ supportsThreads: () => true }), ['--parallel-mode=4']);
});

test('isAbortedResult: tells a restart-interrupted run apart from a real error', () => {
    assert.equal(isAbortedResult({ Result: 'ERROR', Error: 'Aborted by restart().' }), true);
    assert.equal(isAbortedResult({ Result: 'ERROR', Error: 'syntax error' }), false);
    assert.equal(isAbortedResult({ Result: 'SATISFIABLE' }), false);
    assert.equal(isAbortedResult(null), false);
});

test('witnessToSuggestions: parses a streamed witness and drops pins', () => {
    const witness = { Value: ['leg(0,1,2)', 'assignment("m1",leg(0,1,2))', 'assignment("m2",leg(1,2,3))'] };
    assert.deepEqual(
        witnessToSuggestions(witness, new Set(['1-2::m1'])),
        [{ legKey: '2-3', membershipId: 'm2' }],
    );
});

// ---------------------------------------------------------------------------
// 2b. Streaming through optimizeRemaining: the UI depends on getting parsed
//     suggestions per model, and -- the whole point of "Stop & keep best" --
//     on a cancelled solve still handing back the last model it saw.
// ---------------------------------------------------------------------------

/** Minimal state whose facts mention two legs, enough to parse against. */
function tinyState() {
    return buildState(buildCourse(LINE_1, 2), MEMBERS.slice(0, 2), {});
}

function fakeSources() {
    return { domainSource: '', teamSource: '' };
}

test('optimizeRemaining: streams each model to onModel as parsed suggestions', async () => {
    const state = tinyState();
    const legs = state.course.legs;
    const witnessFor = (memberId, leg) => ({
        Value: [`assignment("${memberId}",leg(0,${leg.start.id},${leg.end.id}))`],
    });

    const seen = [];
    const handle = {
        async run(_program, _models, { onModel }) {
            onModel(witnessFor('m1', legs[0]));
            onModel(witnessFor('m2', legs[1]));
            return { Result: 'OPTIMUM FOUND', Call: [{ Witnesses: [witnessFor('m2', legs[1])] }] };
        },
    };

    const result = await optimizeRemaining(state, handle, {
        fetchSources: fakeSources,
        onModel: (m) => seen.push(m),
    });

    assert.equal(seen.length, 2);
    assert.equal(seen[0].index, 1);
    assert.deepEqual(seen[0].suggestions, [{ legKey: `${legs[0].start.id}-${legs[0].end.id}`, membershipId: 'm1' }]);
    assert.deepEqual(seen[1].suggestions, [{ legKey: `${legs[1].start.id}-${legs[1].end.id}`, membershipId: 'm2' }]);
    assert.equal(result.status, 'ok');
    assert.equal(result.optimal, true);
    assert.equal(result.modelCount, 2);
});

test('optimizeRemaining: reports optimal:false when clingo only got to SATISFIABLE', async () => {
    const state = tinyState();
    const leg = state.course.legs[0];
    const witness = { Value: [`assignment("m1",leg(0,${leg.start.id},${leg.end.id}))`] };
    const handle = {
        async run() { return { Result: 'SATISFIABLE', Call: [{ Witnesses: [witness] }] }; },
    };

    const result = await optimizeRemaining(state, handle, { fetchSources: fakeSources });
    assert.equal(result.status, 'ok');
    assert.equal(result.optimal, false);
});

test('optimizeRemaining: a cancelled solve keeps the best model streamed so far', async () => {
    const state = tinyState();
    const leg = state.course.legs[0];
    const handle = {
        async run(_program, _models, { onModel }) {
            onModel({ Value: [`assignment("m1",leg(0,${leg.start.id},${leg.end.id}))`] });
            onModel({ Value: [`assignment("m2",leg(0,${leg.start.id},${leg.end.id}))`] });
            throw new SolverCancelledError('stopped');
        },
    };

    const result = await optimizeRemaining(state, handle, { fetchSources: fakeSources });
    assert.equal(result.status, 'cancelled');
    assert.equal(result.optimal, false);
    assert.equal(result.modelCount, 2);
    // The LAST streamed model, not the first -- clasp only emits improvements.
    assert.deepEqual(result.suggestions, [
        { legKey: `${leg.start.id}-${leg.end.id}`, membershipId: 'm2' },
    ]);
});

test('optimizeRemaining: cancelling during grounding yields no suggestions, not a crash', async () => {
    const handle = {
        async run() { throw new SolverCancelledError('stopped'); },
    };
    const result = await optimizeRemaining(tinyState(), handle, { fetchSources: fakeSources });
    assert.equal(result.status, 'cancelled');
    assert.deepEqual(result.suggestions, []);
    assert.equal(result.modelCount, 0);
});

test('optimizeRemaining: a restart-interrupted result is a cancel, not an error', async () => {
    const state = tinyState();
    const leg = state.course.legs[0];
    const handle = {
        async run(_program, _models, { onModel }) {
            onModel({ Value: [`assignment("m1",leg(0,${leg.start.id},${leg.end.id}))`] });
            return { Result: 'ERROR', Error: 'Aborted by restart().' };
        },
    };

    const result = await optimizeRemaining(state, handle, { fetchSources: fakeSources });
    assert.equal(result.status, 'cancelled');
    assert.equal(result.suggestions.length, 1);
});

test('optimizeRemaining: a genuine program error is still an error', async () => {
    const handle = {
        async run() { return { Result: 'ERROR', Error: 'syntax error in line 3' }; },
    };
    const result = await optimizeRemaining(tinyState(), handle, { fetchSources: fakeSources });
    assert.equal(result.status, 'error');
    assert.match(result.message, /syntax error/);
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
    const leg0Key = `${leg0.start.id}-${leg0.end.id}`;
    const leg1Key = `${leg1.start.id}-${leg1.end.id}`;
    const assignments = {
        [leg0Key]: ['m1'],
        [leg1Key]: ['m2', 'm3'],
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
    for (const { membershipId, legKey } of solved) {
        if (!runnersByLeg.has(legKey)) runnersByLeg.set(legKey, new Set());
        runnersByLeg.get(legKey).add(membershipId);
    }
    for (const leg of SMALL_COURSE.legs) {
        const key = `${leg.start.id}-${leg.end.id}`;
        const runners = runnersByLeg.get(key);
        assert.ok(runners && runners.size >= 1, `leg ${key} has no runner assigned`);
    }

    // No duplicate member-on-same-leg (the choice rule is 0/1 per atom, but
    // assert on the actual parsed output to catch any parsing regressions).
    for (const [legKey, runners] of runnersByLeg) {
        const count = solved.filter((s) => s.legKey === legKey && s.membershipId === [...runners][0]).length;
        assert.ok(count <= runners.size, `leg ${legKey} lists a member more than once`);
    }
    const seenPairs = new Set();
    for (const { membershipId, legKey } of solved) {
        const key = `${legKey}::${membershipId}`;
        assert.ok(!seenPairs.has(key), `duplicate (leg, member) pair in solved model: ${key}`);
        seenPairs.add(key);
    }

    // Pins preserved verbatim.
    for (const key of pinnedPairs) {
        const [legKeyStr, membershipId] = key.split('::');
        assert.ok(
            solved.some((s) => s.legKey === legKeyStr && s.membershipId === membershipId),
            `pin ${key} missing from solved model`,
        );
    }

    // The pinned two-runner leg (leg1) stays intact: both m2 and m3, still there.
    const leg1Runners = runnersByLeg.get(leg1Key);
    assert.ok(leg1Runners.has('m2') && leg1Runners.has('m3'), 'pinned two-runner leg lost a runner');

    // Suggestions (non-pinned atoms) only ever place someone on a leg that
    // had no pin, or add a runner alongside pins -- never remove a pin.
    const suggestions = diffSuggestions(solved, pinnedPairs);
    for (const s of suggestions) {
        assert.ok(!pinnedPairs.has(`${s.legKey}::${s.membershipId}`));
    }
});

test('smoke: singleRunnerPerLeg caps every leg at one runner when the board allows it', async (t) => {
    let clingoRun;
    try {
        ({ run: clingoRun } = (await import('clingo-wasm')).default);
    } catch (err) {
        t.skip(`clingo-wasm not installed in tests/js/node_modules (run "npm install" in tests/js): ${err.message}`);
        return;
    }

    const state = buildState(SMALL_COURSE, MEMBERS, {});
    const { program: factsProgram } = generateFacts(state, { singleRunnerPerLeg: true });
    const program = buildProgram(DOMAIN_SOURCE, TEAM_SOURCE, factsProgram);

    const result = await clingoRun(program, 0);
    assert.equal(result.Result, 'OPTIMUM FOUND', `expected a solvable single-runner plan, got ${result.Result}`);

    const witness = bestWitness(result);
    const solved = parseAssignments(witness.Value);
    const runnersByLeg = new Map();
    for (const { membershipId, legKey } of solved) {
        if (!runnersByLeg.has(legKey)) runnersByLeg.set(legKey, new Set());
        runnersByLeg.get(legKey).add(membershipId);
    }
    for (const leg of SMALL_COURSE.legs) {
        const key = `${leg.start.id}-${leg.end.id}`;
        const runners = runnersByLeg.get(key);
        assert.equal(runners && runners.size, 1, `leg ${key} should have exactly one runner`);
    }
});

test('smoke: singleRunnerPerLeg holds a leg already double-covered by pins at its existing count, caps the rest at one', async (t) => {
    let clingoRun;
    try {
        ({ run: clingoRun } = (await import('clingo-wasm')).default);
    } catch (err) {
        t.skip(`clingo-wasm not installed in tests/js/node_modules (run "npm install" in tests/js): ${err.message}`);
        return;
    }

    // Same pinned two-runner leg as the earlier pin-preservation test, but
    // this time with singleRunnerPerLeg on: the pins predate the solve, so
    // that leg's cap rises to match them (2) instead of forcing UNSAT or
    // silently trying to strip one down to satisfy a flat cap of 1, while
    // every other leg still gets the default cap of one.
    const leg1 = SMALL_COURSE.legs[1];
    const leg1Key = `${leg1.start.id}-${leg1.end.id}`;
    const state = buildState(SMALL_COURSE, MEMBERS, { [leg1Key]: ['m2', 'm3'] });
    const { program: factsProgram } = generateFacts(state, { singleRunnerPerLeg: true });
    const program = buildProgram(DOMAIN_SOURCE, TEAM_SOURCE, factsProgram);

    const result = await clingoRun(program, 0);
    assert.equal(result.Result, 'OPTIMUM FOUND', `expected a solvable plan, got ${result.Result}`);

    const witness = bestWitness(result);
    const solved = parseAssignments(witness.Value);
    const runnersByLeg = new Map();
    for (const { membershipId, legKey: key } of solved) {
        if (!runnersByLeg.has(key)) runnersByLeg.set(key, new Set());
        runnersByLeg.get(key).add(membershipId);
    }

    for (const leg of SMALL_COURSE.legs) {
        const key = `${leg.start.id}-${leg.end.id}`;
        const runners = runnersByLeg.get(key);
        if (key === leg1Key) {
            assert.deepEqual([...runners].sort(), ['m2', 'm3'], 'leg should keep exactly its two pinned runners');
        } else {
            assert.equal(runners && runners.size, 1, `leg ${key} should be capped at one runner`);
        }
    }
});

test('smoke: the full 1 Line also solves SAT with a realistic 4-member team', async (t) => {
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
    const coveredLegs = new Set(solved.map((s) => s.legKey));
    for (const leg of FULL_COURSE.legs) {
        const key = `${leg.start.id}-${leg.end.id}`;
        assert.ok(coveredLegs.has(key), `leg ${key} uncovered on the full course`);
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

test('smoke: the real solver streams improving models, and the last one is the optimum', async (t) => {
    let clingo;
    try {
        clingo = (await import('clingo-wasm')).default;
    } catch (err) {
        t.skip(`clingo-wasm not installed: ${err.message}`);
        return;
    }

    const state = buildState(FULL_COURSE, MEMBERS, {});
    const { program: factsProgram, pinnedPairs } = generateFacts(state);
    const program = buildProgram(DOMAIN_SOURCE, TEAM_SOURCE, factsProgram);

    const streamed = [];
    const result = await clingo.run(program, 0, [], (witness) => {
        streamed.push(witnessToSuggestions(witness, pinnedPairs));
    });

    assert.equal(result.Result, 'OPTIMUM FOUND');

    // The premise of the whole live-preview feature: models actually arrive
    // during the solve, not just at the end.
    assert.ok(streamed.length > 1, `expected several streamed models, got ${streamed.length}`);

    // Every streamed model is a complete, leg-covering plan in its own right
    // -- this is what makes "Stop & keep best" safe to offer at any moment,
    // and it is why leg-solver-ui.js throttles instead of trying to diff
    // successive models into stable chips.
    for (const [i, suggestions] of streamed.entries()) {
        const covered = new Set(suggestions.map((s) => s.legKey));
        for (const leg of FULL_COURSE.legs) {
            const key = `${leg.start.id}-${leg.end.id}`;
            assert.ok(covered.has(key), `streamed model ${i} left leg ${key} uncovered`);
        }
    }

    // The final streamed model matches what the finished result reports, so
    // a captain who stops one model short of the end loses nothing but the
    // proof.
    assert.deepEqual(
        streamed[streamed.length - 1],
        witnessToSuggestions(bestWitness(result), pinnedPairs),
    );
});
