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

import {
    THRESHOLDS,
    legIndexesForMember,
    sumDistanceHundredths,
    buildCommuteLookup,
    resolveStationId,
    memberDistanceMetric,
    memberEndExchangeMetric,
    memberLeadLegMetric,
    legDurationMetric,
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
//   Alice (m1): willing_to_lead, wants 5.0 mi @ 600 s/mi, wants to end at
//     "ExchangeC" (id 102). Assigned legs [0, 1] -> 3.00 + 2.50 = 5.50 mi
//     (diff 0.5, exactly at the "satisfied" boundary). Last leg (1) ends at
//     102 == preferred -> exact match.
//   Bob (m2): not willing to lead, wants 2.0 mi @ 700 s/mi, wants to end at
//     "ExchangeE" (id 105). Assigned legs [1, 2, 3] -> 2.50+4.00+3.50=10.00
//     mi (diff 8.0 -> violated). Last leg (3) ends at 104; commute(104,105)
//     = 0.80 mi <= 1.0 mi near threshold -> near.
//   Dana (m3): no preferred_miles, no preferred_station (no-preference
//     member) but not willing to lead. Assigned to leg 0 alongside Alice
//     (co-runner, at a faster pace: 550s/mi) -- exercises leg 0's pace being
//     Alice's slower 600s/mi (max), and Dana being on leg 0 unwilling ->
//     violated leadLeg.
//   Erin (m4): willing to lead (but not assigned to leg 0, so leadLeg is
//     n/a), wants 10.0 mi, no stated pace, wants to end at "NonCourseStation"
//     (id 999, no commute data -- "no data", not violated). Assigned to leg
//     4 alone -> 3.00 mi assigned vs 10.0 preferred -> violated. Her leg (4)
//     has no pace data among its runners -> that leg's duration is unknown.
// ---------------------------------------------------------------------------

function buildFixture() {
    const course = {
        event: 'test',
        units: {},
        legs: [
            { index: 0, start: { id: 100, name: 'A' }, end: { id: 101, name: 'B' }, distance: 300, ascent: 0, descent: 0 },
            { index: 1, start: { id: 101, name: 'B' }, end: { id: 102, name: 'C' }, distance: 250, ascent: 0, descent: 0 },
            { index: 2, start: { id: 102, name: 'C' }, end: { id: 103, name: 'D' }, distance: 400, ascent: 0, descent: 0 },
            { index: 3, start: { id: 103, name: 'D' }, end: { id: 104, name: 'E' }, distance: 350, ascent: 0, descent: 0 },
            { index: 4, start: { id: 104, name: 'E' }, end: { id: 105, name: 'F' }, distance: 300, ascent: 0, descent: 0 },
        ],
        commute: [[104, 105, 80]],
        station_index: { ExchangeC: 102, ExchangeE: 105, NonCourseStation: 999 },
        estimated_duration_seconds: 5000,
    };

    const members = [
        { membership_id: 'm1', name: 'Alice', willing_to_lead: true, preferred_miles: 5.0, planned_pace_seconds: 600, preferred_station: 'ExchangeC' },
        { membership_id: 'm2', name: 'Bob', willing_to_lead: false, preferred_miles: 2.0, planned_pace_seconds: 700, preferred_station: 'ExchangeE' },
        { membership_id: 'm3', name: 'Dana', willing_to_lead: false, preferred_miles: null, planned_pace_seconds: 550, preferred_station: null },
        { membership_id: 'm4', name: 'Erin', willing_to_lead: true, preferred_miles: 10.0, planned_pace_seconds: null, preferred_station: 'NonCourseStation' },
    ];

    const assignments = {
        0: ['m1', 'm3'],
        1: ['m1', 'm2'],
        2: ['m2'],
        3: ['m2'],
        4: ['m4'],
    };

    return { course, members, assignments };
}

test('legIndexesForMember returns sorted legs a member holds, empty for unassigned', () => {
    const { assignments } = buildFixture();
    assert.deepEqual(legIndexesForMember(assignments, 'm2'), [1, 2, 3]);
    assert.deepEqual(legIndexesForMember(assignments, 'nobody'), []);
});

test('sumDistanceHundredths sums leg distances for given indexes', () => {
    const { course } = buildFixture();
    assert.equal(sumDistanceHundredths(course, [0, 1]), 550);
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
    const course = { legs: [{ index: 0, distance: 200 }] }; // 2.00 mi
    const memberNear = { membership_id: 'x', preferred_miles: 0.5 }; // diff exactly 1.5
    const memberViolated = { membership_id: 'x', preferred_miles: 0.49 }; // diff 1.51
    const assignments = { 0: ['x'] };
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
        legs: [{ index: 0, start: { id: 1, name: 'X' }, end: { id: 2, name: 'Y' } }],
        commute: [[2, 3, 150]], // 1.5 mi, over the 1.0mi near threshold
        station_index: { Elsewhere: 3 },
    };
    const member = { membership_id: 'x', preferred_station: 'Elsewhere' };
    const m = memberEndExchangeMetric(member, course, { 0: ['x'] });
    assert.equal(m.status, 'violated');
    assert.equal(m.distanceMiles, 1.5);
});

// ---- Fixture: leg-1 leadership metric --------------------------------------

test('memberLeadLegMetric: satisfied / violated / not-applicable', () => {
    const { assignments, members } = buildFixture();
    assert.equal(memberLeadLegMetric(members[0], assignments).status, 'satisfied'); // Alice: on leg 0, willing
    assert.equal(memberLeadLegMetric(members[2], assignments).status, 'violated'); // Dana: on leg 0, unwilling
    assert.equal(memberLeadLegMetric(members[1], assignments), null); // Bob: not on leg 0
    assert.equal(memberLeadLegMetric(members[3], assignments), null); // Erin: willing but not on leg 0
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
    const course = { legs: [{ index: 9, distance: 100 }] };
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
    const partialAssignments = { 0: ['m1'] }; // legs 1-4 have nobody
    const team = teamMetrics(course, partialAssignments, membersById);
    assert.equal(team.uncoveredLegsCount, 4);
});

test('teamMetrics: fully known total classifies satisfied/near/violated against Team.estimated_duration_seconds', () => {
    const course = {
        legs: [{ index: 0, distance: 100 }], // 1.00 mi
        estimated_duration_seconds: 600,
    };
    const membersById = new Map([['x', { planned_pace_seconds: 600 }]]); // duration = 600s, exact match
    assert.equal(teamMetrics(course, { 0: ['x'] }, membersById).status, 'satisfied');

    const near = new Map([['x', { planned_pace_seconds: 660 }]]); // duration 660s, 10% over -> near (<=15%)
    assert.equal(teamMetrics(course, { 0: ['x'] }, near).status, 'near');

    const violated = new Map([['x', { planned_pace_seconds: 900 }]]); // duration 900s, 50% over -> violated
    assert.equal(teamMetrics(course, { 0: ['x'] }, violated).status, 'violated');
});

// ---- Top-level computeMetrics wraps everything correctly -------------------

test('computeMetrics: assembles member, leg, and team metrics from full state', () => {
    const { course, members, assignments } = buildFixture();
    const result = computeMetrics({ course, members, assignments });

    assert.equal(result.members.m1.distance.status, 'satisfied');
    assert.equal(result.members.m1.endExchange.status, 'satisfied');
    assert.equal(result.members.m1.leadLeg.status, 'satisfied');

    assert.equal(result.members.m2.distance.status, 'violated');
    assert.equal(result.members.m2.endExchange.status, 'near');
    assert.equal(result.members.m2.leadLeg, null);

    assert.equal(result.members.m3.distance, null);
    assert.equal(result.members.m3.endExchange, null);
    assert.equal(result.members.m3.leadLeg.status, 'violated');

    assert.equal(result.members.m4.distance.status, 'violated');
    assert.equal(result.members.m4.endExchange, null);
    assert.equal(result.members.m4.leadLeg, null);

    assert.equal(result.legs[0].paceSeconds, 600);
    assert.equal(result.legs[4].durationSeconds, null);

    assert.equal(result.team.uncoveredLegsCount, 0);
    assert.equal(result.team.status, 'no data');
});

test('computeMetrics: missing course/members returns empty shape without throwing', () => {
    assert.deepEqual(computeMetrics({}), { members: {}, legs: {}, team: null });
});

test('THRESHOLDS are exported and documented for badge rendering to reuse', () => {
    assert.equal(typeof THRESHOLDS.distanceSatisfiedMi, 'number');
    assert.equal(typeof THRESHOLDS.endExchangeNearMi, 'number');
});
