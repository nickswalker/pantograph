// Unit tests for app/static/js/leg-metrics.js (pure computation, no DOM,
// no solver). Uses Node's built-in test runner and assert module, so no
// JS build system / test framework needs to be installed.
//
// Run with plain Node (v18+):
//   node tests/js/leg-metrics.test.mjs
// or, for TAP-style output:
//   node --test tests/js/leg-metrics.test.mjs
//
// This file is .mjs so Node always parses it as an ES module regardless of
// package.json; it imports leg-metrics.js, which is ESM under
// app/static/js/package.json ({"type": "module"} -- Node-only, browsers
// don't consult it).

import test from 'node:test';
import assert from 'node:assert/strict';

import { legKey, legNumbering } from '../../app/static/js/leg-keys.js';

import {
    THRESHOLDS,
    legKeysForMember,
    sumDistanceHundredths,
    buildCommuteLookup,
    resolveStationId,
    memberDistanceMetric,
    memberEndExchangeMetric,
    memberCurrentSummary,
    legDurationMetric,
    legScheduleMetrics,
    isOverridden,
    overrideNote,
    teamMetrics,
    computeMetrics,
} from '../../app/static/js/leg-metrics.js';

// ---------------------------------------------------------------------------
// Hand-computed fixture: 4 members, 5 legs (indices 0-4). Numbers below are
// worked out by hand in comments.
//
// Legs (distance in hundredths of a mile):
//   0: A(100) -> B(101), 300  (3.00 mi)
//   1: B(101) -> C(102), 250  (2.50 mi)
//   2: C(102) -> D(103), 400  (4.00 mi)
//   3: D(103) -> E(104), 350  (3.50 mi)
//   4: E(104) -> F(105), 300  (3.00 mi)
//
// Commute: only pair (104, 105) has data (80 hundredths = 0.80 mi) -- used
// for Bob's "near" end-exchange case. Exchange 999 ("NonCourseStation")
// intentionally has NO commute entries, standing in for a real
// non_course_stations entry like "Boeing Access Road".
//
// Members:
//   Alice (m1): wants 5.0 mi @ 600 s/mi, wants to end at "ExchangeC"
//     (id 102). Assigned legs [0, 1] -> 3.00 + 2.50 = 5.50 mi (diff 0.5,
//     exactly at the "satisfied" boundary). Last leg (1) ends at 102 ==
//     preferred -> exact match.
//   Bob (m2): wants 2.0 mi @ 700 s/mi, wants to end at "ExchangeE" (id 105).
//     Assigned legs [1, 2, 3] -> 2.50+4.00+3.50=10.00 mi (diff 8.0 ->
//     violated). Last leg (3) ends at 104; commute(104,105) = 0.80 mi
//     <= 1.0 mi near threshold -> near.
//   Dana (m3): no preferred_miles, no preferred_station (no-preference
//     member). Assigned to leg 0 alongside Alice (co-runner, at a faster
//     pace: 550s/mi) -- exercises leg 0's pace being Alice's slower 600s/mi
//     (max).
//   Erin (m4): wants 10.0 mi, no stated pace, wants to end at
//     "NonCourseStation" (id 999, no commute data -- "no data", not
//     violated). Assigned to leg 4 alone -> 3.00 mi assigned vs 10.0
//     preferred -> violated. Her leg (4) has no pace data among its runners
//     -> that leg's duration is unknown.
// ---------------------------------------------------------------------------

function buildFixture() {
    const course = {
        event: 'test',
        units: {},
        legs: [
            { start: { id: 100, name: 'A' }, end: { id: 101, name: 'B' }, distance: 300, ascent: 0, descent: 0 },
            { start: { id: 101, name: 'B' }, end: { id: 102, name: 'C' }, distance: 250, ascent: 0, descent: 0 },
            { start: { id: 102, name: 'C' }, end: { id: 103, name: 'D' }, distance: 400, ascent: 0, descent: 0 },
            { start: { id: 103, name: 'D' }, end: { id: 104, name: 'E' }, distance: 350, ascent: 0, descent: 0 },
            { start: { id: 104, name: 'E' }, end: { id: 105, name: 'F' }, distance: 300, ascent: 0, descent: 0 },
        ],
        commute: [[104, 105, 80]],
        station_index: { ExchangeC: 102, ExchangeE: 105, NonCourseStation: 999 },
        estimated_duration_seconds: 5000,
        event_start_time: '2026-10-03T08:30:00-07:00',
    };

    const members = [
        { membership_id: 'm1', name: 'Alice', willing_to_lead: true, preferred_miles: 5.0, planned_pace_seconds: 600, preferred_station: 'ExchangeC' },
        { membership_id: 'm2', name: 'Bob', willing_to_lead: false, preferred_miles: 2.0, planned_pace_seconds: 700, preferred_station: 'ExchangeE' },
        { membership_id: 'm3', name: 'Dana', willing_to_lead: false, preferred_miles: null, planned_pace_seconds: 550, preferred_station: null },
        { membership_id: 'm4', name: 'Erin', willing_to_lead: true, preferred_miles: 10.0, planned_pace_seconds: null, preferred_station: 'NonCourseStation' },
    ];

    const assignments = {
        '100-101': ['m1', 'm3'],
        '101-102': ['m1', 'm2'],
        '102-103': ['m2'],
        '103-104': ['m2'],
        '104-105': ['m4'],
    };

    return { course, members, assignments };
}

test('legKeysForMember returns legs a member holds in course order, empty for unassigned', () => {
    const { course, assignments } = buildFixture();
    assert.deepEqual(legKeysForMember(course, assignments, 'm2'), ['101-102', '102-103', '103-104']);
    assert.deepEqual(legKeysForMember(course, assignments, 'nobody'), []);
});

test('sumDistanceHundredths sums leg distances for given keys', () => {
    const { course } = buildFixture();
    assert.equal(sumDistanceHundredths(course, ['100-101', '101-102']), 550);
    assert.equal(sumDistanceHundredths(course, []), 0);
});

test('buildCommuteLookup is symmetric and only has data for known pairs', () => {
    const { course } = buildFixture();
    const lookup = buildCommuteLookup(course);
    assert.equal(lookup.get('104-105'), 80);
    assert.equal(lookup.has('105-999'), false);
});

test('resolveStationId resolves aliases and returns null for unknown names', () => {
    const { course } = buildFixture();
    assert.equal(resolveStationId(course, 'ExchangeC'), 102);
    assert.equal(resolveStationId(course, 'NonCourseStation'), 999);
    assert.equal(resolveStationId(course, 'Nowhere'), null);
    assert.equal(resolveStationId(course, null), null);
});

// ---- Fixture: member distance metric --------------------------------------

test('memberDistanceMetric: satisfied at exactly the 0.5mi boundary (Alice)', () => {
    const { course, assignments, members } = buildFixture();
    const alice = members[0];
    const m = memberDistanceMetric(alice, course, assignments);
    assert.equal(m.assignedMiles, 5.5);
    assert.equal(m.preferredMiles, 5.0);
    assert.equal(m.status, 'satisfied');
    assert.equal(m.tooltip, '5.5 mi assigned, wanted 5.0 mi');
});

test('memberDistanceMetric: violated when far off (Bob, Erin)', () => {
    const { course, assignments, members } = buildFixture();
    const bob = members[1];
    const erin = members[3];
    assert.equal(memberDistanceMetric(bob, course, assignments).status, 'violated');
    assert.equal(memberDistanceMetric(bob, course, assignments).assignedMiles, 10.0);
    assert.equal(memberDistanceMetric(erin, course, assignments).status, 'violated');
    assert.equal(memberDistanceMetric(erin, course, assignments).assignedMiles, 3.0);
});

test('memberDistanceMetric: no preference stated -> null (Dana)', () => {
    const { course, assignments, members } = buildFixture();
    assert.equal(memberDistanceMetric(members[2], course, assignments), null);
});

test('memberDistanceMetric: near/violated boundary is exclusive above 1.5mi', () => {
    const course = { legs: [{ start: { id: 1 }, end: { id: 2 }, distance: 200 }] }; // 2.00 mi
    const memberNear = { membership_id: 'x', preferred_miles: 0.5 }; // diff exactly 1.5
    const memberViolated = { membership_id: 'x', preferred_miles: 0.49 }; // diff 1.51
    const assignments = { '1-2': ['x'] };
    assert.equal(memberDistanceMetric(memberNear, course, assignments).status, 'near');
    assert.equal(memberDistanceMetric(memberViolated, course, assignments).status, 'violated');
});

// ---- Fixture: end-exchange metric -----------------------------------------

test('memberEndExchangeMetric: exact match (Alice)', () => {
    const { course, assignments, members } = buildFixture();
    const m = memberEndExchangeMetric(members[0], course, assignments);
    assert.equal(m.status, 'satisfied');
    assert.equal(m.distanceMiles, 0);
});

test('memberEndExchangeMetric: near via commute distance (Bob)', () => {
    const { course, assignments, members } = buildFixture();
    const m = memberEndExchangeMetric(members[1], course, assignments);
    assert.equal(m.status, 'near');
    assert.equal(m.distanceMiles, 0.8);
    assert.equal(m.tooltip, 'Ends at E, 0.8 mi from preferred stop ExchangeE');
});

test('memberEndExchangeMetric: no preference stated -> null (Dana)', () => {
    const { course, assignments, members } = buildFixture();
    assert.equal(memberEndExchangeMetric(members[2], course, assignments), null);
});

test('memberEndExchangeMetric: preference resolves to a non-course station -> null (Erin)', () => {
    const { course, assignments, members } = buildFixture();
    assert.equal(memberEndExchangeMetric(members[3], course, assignments), null);
});

test('memberEndExchangeMetric: not assigned anywhere -> null', () => {
    const { course, members } = buildFixture();
    assert.equal(memberEndExchangeMetric(members[0], course, {}), null);
});

test('memberEndExchangeMetric: violated when commute distance exceeds the near threshold', () => {
    const course = {
        legs: [{ start: { id: 1, name: 'X' }, end: { id: 2, name: 'Y' } }],
        commute: [[2, 3, 150]], // 1.5 mi, over the 1.0mi near threshold
        station_index: { Elsewhere: 3 },
    };
    const member = { membership_id: 'x', preferred_station: 'Elsewhere' };
    const m = memberEndExchangeMetric(member, course, { '1-2': ['x'] });
    assert.equal(m.status, 'violated');
    assert.equal(m.distanceMiles, 1.5);
});

// ---- Fixture: per-leg duration (max/slowest pace among co-runners) --------

test('legDurationMetric: multi-runner leg uses the SLOWEST stated pace', () => {
    const { course, assignments, members } = buildFixture();
    const membersById = new Map(members.map(m => [m.membership_id, m]));
    const leg0 = legDurationMetric(course.legs[0], assignments, membersById); // Alice 600, Dana 550
    assert.equal(leg0.paceSeconds, 600);
    assert.equal(leg0.durationSeconds, 1800); // 600 * 3.00mi

    const leg1 = legDurationMetric(course.legs[1], assignments, membersById); // Alice 600, Bob 700
    assert.equal(leg1.paceSeconds, 700);
    assert.equal(leg1.durationSeconds, 1750); // 700 * 2.50mi
});

test('legDurationMetric: covered leg with no pace data among runners -> unknown duration', () => {
    const { course, assignments, members } = buildFixture();
    const membersById = new Map(members.map(m => [m.membership_id, m]));
    const leg4 = legDurationMetric(course.legs[4], assignments, membersById); // Erin only, no pace
    assert.equal(leg4.covered, true);
    assert.equal(leg4.durationSeconds, null);
});

test('legDurationMetric: uncovered leg', () => {
    const course = { legs: [{ start: { id: 1 }, end: { id: 2 }, distance: 100 }] };
    const leg = legDurationMetric(course.legs[0], {}, new Map());
    assert.equal(leg.covered, false);
    assert.equal(leg.durationSeconds, null);
});

// ---- Fixture: team-level metrics -------------------------------------------

test('teamMetrics: all legs covered, but one missing pace data -> "no data" total', () => {
    const { course, assignments, members } = buildFixture();
    const membersById = new Map(members.map(m => [m.membership_id, m]));
    const team = teamMetrics(course, assignments, membersById);

    assert.equal(team.uncoveredLegsCount, 0);
    assert.equal(team.legsMissingPaceCount, 1);
    assert.equal(team.totalEstimatedDurationSeconds, 1800 + 1750 + 2800 + 2450); // leg4 contributes 0 (unknown)
    assert.equal(team.status, 'no data');
    assert.equal(team.diffSeconds, null);
});

test('teamMetrics: uncovered legs are counted', () => {
    const { course, members } = buildFixture();
    const membersById = new Map(members.map(m => [m.membership_id, m]));
    const partialAssignments = { '100-101': ['m1'] }; // the other four legs have nobody
    const team = teamMetrics(course, partialAssignments, membersById);
    assert.equal(team.uncoveredLegsCount, 4);
});

test('teamMetrics: fully known total classifies satisfied/near/violated against Team.estimated_duration_seconds', () => {
    const course = {
        legs: [{ start: { id: 1 }, end: { id: 2 }, distance: 100 }], // 1.00 mi
        estimated_duration_seconds: 600,
    };
    const membersById = new Map([['x', { planned_pace_seconds: 600 }]]); // duration = 600s, exact match
    assert.equal(teamMetrics(course, { '1-2': ['x'] }, membersById).status, 'satisfied');

    const near = new Map([['x', { planned_pace_seconds: 660 }]]); // duration 660s, 10% over -> near (<=15%)
    assert.equal(teamMetrics(course, { '1-2': ['x'] }, near).status, 'near');

    const violated = new Map([['x', { planned_pace_seconds: 900 }]]); // duration 900s, 50% over -> violated
    assert.equal(teamMetrics(course, { '1-2': ['x'] }, violated).status, 'violated');
});

// ---- Top-level computeMetrics wraps everything correctly -------------------

test('computeMetrics: assembles member, leg, and team metrics from full state', () => {
    const { course, members, assignments } = buildFixture();
    const result = computeMetrics({ course, members, assignments });

    assert.equal(result.members.m1.distance.status, 'satisfied');
    assert.equal(result.members.m1.endExchange.status, 'satisfied');
    assert.equal(result.members.m1.current.assignedMiles, 5.5);

    assert.equal(result.members.m2.distance.status, 'violated');
    assert.equal(result.members.m2.endExchange.status, 'near');
    assert.equal(result.members.m2.current.assignedMiles, 10.0);

    assert.equal(result.members.m3.distance, null);
    assert.equal(result.members.m3.endExchange, null);
    assert.equal(result.members.m3.current.assignedMiles, 3.0);

    assert.equal(result.members.m4.distance.status, 'violated');
    assert.equal(result.members.m4.endExchange, null);
    assert.equal(result.members.m4.current.assignedMiles, 3.0);

    assert.equal(result.legs['100-101'].paceSeconds, 600);
    assert.equal(result.legs['104-105'].durationSeconds, null);

    assert.equal(result.team.uncoveredLegsCount, 0);
    assert.equal(result.team.status, 'no data');
});

test('computeMetrics: missing course/members returns empty shape without throwing', () => {
    assert.deepEqual(computeMetrics({}), {
        members: {}, legs: {}, team: null,
        schedule: { byKey: {}, firstUnknownLegKey: null, allKnown: true },
    });
});

test('THRESHOLDS are exported and documented for badge rendering to reuse', () => {
    assert.equal(typeof THRESHOLDS.distanceSatisfiedMi, 'number');
    assert.equal(typeof THRESHOLDS.endExchangeNearMi, 'number');
});

// ---- Captain overrides ----------------------------------------------------
//
// The server resolves stated + override into the effective values that ride
// in the ordinary preference keys, so nothing here has to merge anything --
// what these cover is that the metrics *say* when a value was adjusted, so a
// green badge is never mistaken for agreement with the member's own answer.

test('isOverridden distinguishes an adjusted field from an untouched one', () => {
    const member = { overrides: { preferred_miles: 4.0 } };
    assert.equal(isOverridden(member, 'preferred_miles'), true);
    assert.equal(isOverridden(member, 'planned_pace_seconds'), false);
    assert.equal(isOverridden({}, 'preferred_miles'), false);
});

test('overrideNote names the value the member stated, and flags a stale override', () => {
    const member = {
        overrides: { preferred_miles: 4.0 },
        stated: { preferred_miles: 6.5 },
    };
    assert.equal(
        overrideNote(member, 'preferred_miles', m => `${m} mi`),
        ' (captain-adjusted from 6.5 mi)',
    );

    // Member has since edited their own registration: the captain's value
    // still wins, but silently outvoting a fresh answer is worth saying.
    const stale = { ...member, stale_override_fields: ['preferred_miles'] };
    assert.match(overrideNote(stale, 'preferred_miles'), /has since changed their own answer/);

    // A preference the captain supplied where the member gave none.
    const supplied = { overrides: { preferred_miles: 4.0 }, stated: { preferred_miles: null } };
    assert.equal(overrideNote(supplied, 'preferred_miles'), ' (set by captain; member stated no preference)');

    assert.equal(overrideNote({ overrides: {} }, 'preferred_miles'), '');
});

test('member metrics score the effective value and mark it as adjusted', () => {
    const { course, assignments } = buildFixture();
    // Alice runs 5.50 mi and asked for 5.0 (satisfied); the captain lowered
    // her to 4.0, which the same 5.50 mi now misses by 1.5 -> near.
    const adjusted = {
        membership_id: 'm1', name: 'Alice', willing_to_lead: true,
        preferred_miles: 4.0, planned_pace_seconds: 600, preferred_station: 'ExchangeC',
        stated: { preferred_miles: 5.0, planned_pace_seconds: 600, preferred_station: 'ExchangeC', willing_to_lead: true },
        overrides: { preferred_miles: 4.0 },
    };

    const distance = memberDistanceMetric(adjusted, course, assignments);
    assert.equal(distance.status, 'near');
    assert.equal(distance.preferredMiles, 4.0);
    assert.equal(distance.overridden, true);
    assert.match(distance.tooltip, /captain-adjusted from 5\.0 mi/);

    // An untouched dimension stays unmarked.
    assert.equal(memberEndExchangeMetric(adjusted, course, assignments).overridden, false);
});

// ---- Fixture: member's current (as-assigned) summary -----------------------

test('memberCurrentSummary: effective pace can be slower than the member\'s own (Dana, paired with Alice)', () => {
    const { course, assignments, members } = buildFixture();
    const membersById = new Map(members.map(m => [m.membership_id, m]));
    const legMetrics = {};
    for (const leg of course.legs) legMetrics[legKey(leg)] = legDurationMetric(leg, assignments, membersById);

    // Dana stated 550 s/mi, but leg 0's pace is set by the slower Alice
    // (600) -- Dana's effective pace here should reflect that, not her own.
    const dana = memberCurrentSummary(members[2], course, assignments, legMetrics);
    assert.equal(dana.assignedMiles, 3.0);
    assert.equal(dana.paceSeconds, 600);
    assert.equal(dana.endExchangeName, 'B');
});

test('memberCurrentSummary: distance-weighted average pace across multiple legs (Alice)', () => {
    const { course, assignments, members } = buildFixture();
    const membersById = new Map(members.map(m => [m.membership_id, m]));
    const legMetrics = {};
    for (const leg of course.legs) legMetrics[legKey(leg)] = legDurationMetric(leg, assignments, membersById);

    // Alice: leg 0 at 600 s/mi (3.00mi) and leg 1 at 700 s/mi (2.50mi, set
    // by the slower Bob) -> (1800 + 1750) / 5.50.
    const alice = memberCurrentSummary(members[0], course, assignments, legMetrics);
    assert.equal(alice.assignedMiles, 5.5);
    assert.equal(alice.paceSeconds, (600 * 3.0 + 700 * 2.5) / 5.5);
    assert.equal(alice.endExchangeName, 'C');
});

test('memberCurrentSummary: no pace data among a leg\'s runners -> null pace, not zero (Erin)', () => {
    const { course, assignments, members } = buildFixture();
    const membersById = new Map(members.map(m => [m.membership_id, m]));
    const legMetrics = {};
    for (const leg of course.legs) legMetrics[legKey(leg)] = legDurationMetric(leg, assignments, membersById);

    const erin = memberCurrentSummary(members[3], course, assignments, legMetrics);
    assert.equal(erin.assignedMiles, 3.0);
    assert.equal(erin.paceSeconds, null);
    assert.equal(erin.endExchangeName, 'F');
});

test('memberCurrentSummary: unassigned member gets zeroed-out, not null, summary', () => {
    const { course } = buildFixture();
    const nobody = { membership_id: 'ghost' };
    const summary = memberCurrentSummary(nobody, course, {}, {});
    assert.equal(summary.legCount, 0);
    assert.equal(summary.assignedMiles, 0);
    assert.equal(summary.paceSeconds, null);
    assert.equal(summary.endExchangeName, null);
});

test('legDurationMetric flags when the slowest pace on the leg is an adjusted one', () => {
    const { course, assignments } = buildFixture();
    const membersById = new Map([
        ['m1', { membership_id: 'm1', planned_pace_seconds: 600, overrides: { planned_pace_seconds: 600 }, stated: { planned_pace_seconds: 540 } }],
        ['m3', { membership_id: 'm3', planned_pace_seconds: 550 }],
    ]);
    // Leg 0 holds m1 (600, adjusted) and m3 (550): the slowest sets the pace.
    const paced = legDurationMetric(course.legs[0], assignments, membersById);
    assert.equal(paced.paceSeconds, 600);
    assert.equal(paced.paceOverridden, true);

    // If the adjusted runner isn't the slowest, the leg's estimate isn't theirs.
    const fasterOverride = new Map([
        ['m1', { membership_id: 'm1', planned_pace_seconds: 500, overrides: { planned_pace_seconds: 500 }, stated: { planned_pace_seconds: 600 } }],
        ['m3', { membership_id: 'm3', planned_pace_seconds: 550 }],
    ]);
    const unaffected = legDurationMetric(course.legs[0], assignments, fasterOverride);
    assert.equal(unaffected.paceSeconds, 550);
    assert.equal(unaffected.paceOverridden, false);
});

// ---- Schedule (wall-clock handoff times) ----------------------------------
//
// Against the fixture: legs 0-3 have paced runners, leg 4 (Erin, no stated
// pace) does not. Durations, at each leg's slowest assigned pace:
//   leg 0: 3.00 mi @ 600 (Alice, slower than Dana's 550) = 1800 s
//   leg 1: 2.50 mi @ 700 (Bob, slower than Alice)        = 1750 s
//   leg 2: 4.00 mi @ 700                                 = 2800 s
//   leg 3: 3.50 mi @ 700                                 = 2450 s
//   leg 4: no pace among its runners                     = unknown
// So the clock runs 08:30, 09:00, 09:29:10, 10:15:50, 10:56:40 and then stops.

const FIXTURE_START_MS = Date.parse('2026-10-03T08:30:00-07:00');

function scheduleForFixture() {
    const { course, members, assignments } = buildFixture();
    const membersById = new Map(members.map(m => [m.membership_id, m]));
    const legMetrics = {};
    for (const leg of course.legs) {
        legMetrics[legKey(leg)] = legDurationMetric(leg, assignments, membersById);
    }
    return legScheduleMetrics(course, legMetrics);
}

test('legScheduleMetrics accumulates each leg\'s start from the event start', () => {
    const { byKey } = scheduleForFixture();

    assert.equal(byKey['100-101'].startMs, FIXTURE_START_MS);
    assert.equal(byKey['100-101'].endMs, FIXTURE_START_MS + 1800 * 1000);
    assert.equal(byKey['100-101'].known, true);

    // Each leg starts where the one before it finished.
    assert.equal(byKey['101-102'].startMs, byKey['100-101'].endMs);
    assert.equal(byKey['102-103'].startMs, FIXTURE_START_MS + (1800 + 1750) * 1000);
    assert.equal(byKey['103-104'].startMs, FIXTURE_START_MS + (1800 + 1750 + 2800) * 1000);
    assert.equal(byKey['103-104'].endMs, FIXTURE_START_MS + (1800 + 1750 + 2800 + 2450) * 1000);
});

test('legScheduleMetrics: a leg with no duration keeps its own start but has no end', () => {
    const { byKey, firstUnknownLegKey, allKnown } = scheduleForFixture();

    // Leg 4 is handed off to on time -- it's when it *finishes* that nobody
    // can say, because Erin never stated a pace.
    assert.equal(byKey['104-105'].startMs, FIXTURE_START_MS + (1800 + 1750 + 2800 + 2450) * 1000);
    assert.equal(byKey['104-105'].endMs, null);
    assert.equal(byKey['104-105'].known, false);
    assert.equal(byKey['104-105'].reason, 'no-duration');

    assert.equal(firstUnknownLegKey, '104-105');
    assert.equal(allKnown, false);
});

test('legScheduleMetrics: one unpaced leg blanks the clock for every leg after it', () => {
    const { course, members, assignments } = buildFixture();
    const membersById = new Map(members.map(m => [m.membership_id, m]));
    // Take the pace off Bob, who runs legs 1-3. Leg 1 survives -- Alice is on
    // it too and still has a pace (2.50 mi @ 600 = 1500 s) -- but leg 2 is
    // Bob alone, so it loses its duration, and legs 3 and 4 lose their start
    // times as a consequence. No fallback pace is invented to carry the chain
    // across the gap.
    membersById.set('m2', { membership_id: 'm2', planned_pace_seconds: null });

    const legMetrics = {};
    for (const leg of course.legs) {
        legMetrics[legKey(leg)] = legDurationMetric(leg, assignments, membersById);
    }
    const { byKey, firstUnknownLegKey } = legScheduleMetrics(course, legMetrics);

    assert.equal(byKey['100-101'].known, true);
    assert.equal(byKey['101-102'].known, true);
    assert.equal(byKey['101-102'].durationSeconds, 1500);
    assert.equal(firstUnknownLegKey, '102-103');
    // Leg 2 still starts when leg 1 hands off...
    assert.equal(byKey['102-103'].startMs, FIXTURE_START_MS + (1800 + 1500) * 1000);
    assert.equal(byKey['102-103'].endMs, null);
    // ...but nothing downstream has a start at all.
    for (const key of ['103-104', '104-105']) {
        assert.equal(byKey[key].startMs, null, `${key} should have no start`);
        assert.equal(byKey[key].endMs, null);
        assert.equal(byKey[key].known, false);
    }
});

test('legScheduleMetrics: per-leg durations survive the gap even when the clock does not', () => {
    const { byKey } = scheduleForFixture();
    // A leg's own estimate doesn't depend on the chain, so it stays available
    // for display on a half-assigned board.
    assert.equal(byKey['102-103'].durationSeconds, 2800);
    assert.equal(byKey['104-105'].durationSeconds, null);
});

// ---- Schedule: the Y-shaped Interline course ------------------------------
//
// The real course converges: the 1 Line branch and the 2 Line branch both run
// to International District/Chinatown, then share a trunk north. Miniature of
// that shape -- two branches from termini 1 and 2 meeting at 3, then 3 -> 4:
//
//   branch A: 1 -> 3   (600 s)
//   branch B: 2 -> 3   (1200 s, the slower branch)
//   trunk:    3 -> 4   (300 s)

function buildYCourse() {
    return {
        legs: [
            { start: { id: 1, name: 'A terminus' }, end: { id: 3, name: 'Junction' }, distance: 100 },
            { start: { id: 2, name: 'B terminus' }, end: { id: 3, name: 'Junction' }, distance: 100 },
            { start: { id: 3, name: 'Junction' }, end: { id: 4, name: 'Finish' }, distance: 100 },
        ],
        lines: ['lrr_1line', 'lrr_2line'],
        event_start_time: '2026-10-03T08:30:00-07:00',
    };
}

const Y_METRICS = {
    '1-3': { legKey: '1-3', covered: true, durationSeconds: 600, paceSeconds: 600 },
    '2-3': { legKey: '2-3', covered: true, durationSeconds: 1200, paceSeconds: 1200 },
    '3-4': { legKey: '3-4', covered: true, durationSeconds: 300, paceSeconds: 300 },
};

test('legScheduleMetrics: every branch starts at the event start, not one after the other', () => {
    const { byKey } = legScheduleMetrics(buildYCourse(), Y_METRICS);
    // Both termini are start lines: the two branches go off together.
    assert.equal(byKey['1-3'].startMs, FIXTURE_START_MS);
    assert.equal(byKey['2-3'].startMs, FIXTURE_START_MS);
});

test('legScheduleMetrics: a converging exchange waits for the SLOWER branch', () => {
    const { byKey, allKnown } = legScheduleMetrics(buildYCourse(), Y_METRICS);

    // Branch A is in at +600 s, but the trunk can't leave until branch B
    // arrives at +1200 s -- scheduling it off the first arrival would have the
    // baton leaving before it exists.
    assert.equal(byKey['1-3'].endMs, FIXTURE_START_MS + 600 * 1000);
    assert.equal(byKey['2-3'].endMs, FIXTURE_START_MS + 1200 * 1000);
    assert.equal(byKey['3-4'].startMs, FIXTURE_START_MS + 1200 * 1000);
    assert.equal(byKey['3-4'].endMs, FIXTURE_START_MS + 1500 * 1000);
    assert.equal(allKnown, true);
});

test('legScheduleMetrics: an unknown branch blanks the trunk, even if the other branch is known', () => {
    const metrics = { ...Y_METRICS, '2-3': { legKey: '2-3', covered: true, durationSeconds: null, paceSeconds: null } };
    const { byKey } = legScheduleMetrics(buildYCourse(), metrics);

    assert.equal(byKey['1-3'].known, true);
    // Branch B never finishes as far as the schedule knows, so the junction
    // has no latest arrival -- taking branch A's would be a guess.
    assert.equal(byKey['2-3'].startMs, FIXTURE_START_MS);
    assert.equal(byKey['2-3'].endMs, null);
    assert.equal(byKey['3-4'].startMs, null);
    assert.equal(byKey['3-4'].reason, 'awaiting-arrival');
});

test('legScheduleMetrics: a leg placed before anything reaches its start is a discontinuity', () => {
    // The trunk listed ahead of the branch that feeds it: exchange 3 is no
    // terminus, but nothing has arrived there yet at that point in the walk.
    const course = buildYCourse();
    course.legs = [course.legs[2], course.legs[0], course.legs[1]];
    const { byKey } = legScheduleMetrics(course, Y_METRICS);

    assert.equal(byKey['3-4'].startMs, null);
    assert.equal(byKey['3-4'].reason, 'discontinuity');
    assert.equal(byKey['3-4'].durationSeconds, 300);
    // The branches themselves are unaffected -- they start from termini.
    assert.equal(byKey['1-3'].startMs, FIXTURE_START_MS);
});

test('legScheduleMetrics: no event start time -> no clock anywhere, durations intact', () => {
    const { course, members, assignments } = buildFixture();
    delete course.event_start_time;
    const membersById = new Map(members.map(m => [m.membership_id, m]));
    const legMetrics = {};
    for (const leg of course.legs) {
        legMetrics[legKey(leg)] = legDurationMetric(leg, assignments, membersById);
    }
    const { byKey, allKnown, firstUnknownLegKey } = legScheduleMetrics(course, legMetrics);

    assert.equal(allKnown, false);
    assert.equal(firstUnknownLegKey, '100-101');
    assert.equal(byKey['100-101'].startMs, null);
    assert.equal(byKey['100-101'].reason, 'no-event-start');
    assert.equal(byKey['100-101'].durationSeconds, 1800);
});

test('computeMetrics exposes the schedule alongside the other metrics', () => {
    const { course, members, assignments } = buildFixture();
    const result = computeMetrics({ course, members, assignments });
    assert.equal(result.schedule.byKey['100-101'].startMs, FIXTURE_START_MS);
    assert.equal(result.schedule.allKnown, false);
});

// ---- Leg numbering (leg-keys.js), shared by the board and the schedule -----

test('legNumbering: a branch leg carries its single line position', () => {
    const leg = { start: { id: 1 }, end: { id: 2 }, lines: ['lrr_1line'], sequence: { lrr_1line: 0 } };
    const numbering = legNumbering(leg, ['lrr_1line', 'lrr_2line']);
    assert.equal(numbering.text, '1');
    assert.equal(numbering.title, 'Leg 1');
});

test('legNumbering: a shared trunk leg shows both lines\' positions', () => {
    // The trunk is the 1 Line's leg 14 and the 2 Line's leg 13: each branch
    // counts a different number of legs before reaching it.
    const leg = {
        start: { id: 1253 }, end: { id: 1252 },
        lines: ['lrr_1line', 'lrr_2line'],
        sequence: { lrr_1line: 13, lrr_2line: 12 },
    };
    const numbering = legNumbering(leg, ['lrr_1line', 'lrr_2line']);
    assert.equal(numbering.text, '14/13');
    assert.equal(numbering.title, 'leg 14 on the 1 Line, leg 13 on the 2 Line');
});

test('legNumbering: a single-line team never sees the other line\'s count', () => {
    const leg = {
        start: { id: 1253 }, end: { id: 1252 },
        lines: ['lrr_1line', 'lrr_2line'],
        sequence: { lrr_1line: 13, lrr_2line: 12 },
    };
    assert.equal(legNumbering(leg, ['lrr_1line']).text, '14');
    assert.equal(legNumbering(leg, ['lrr_2line']).text, '13');
});

test('legNumbering: no usable sequence data -> nulls, not a bogus number', () => {
    const leg = { start: { id: 1 }, end: { id: 2 }, lines: ['lrr_1line'] };
    assert.deepEqual(legNumbering(leg, ['lrr_1line']), { text: null, title: null });
});
