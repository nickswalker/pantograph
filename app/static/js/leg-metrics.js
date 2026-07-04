/**
 * Leg Assignment Metrics
 *
 * Pure, DOM-free computation over the board state -- exactly what
 * leg-board.js's `onAssignmentsChanged(state)` hook passes and what GET
 * /team/<id>/assignments returns. Nothing here touches `document`, so the
 * whole file is unit-testable from plain Node (tests/js/leg-metrics.test.mjs).
 *
 *   state = {
 *     course: {
 *       event, units,
 *       legs: [{index, start:{id,name}, end:{id,name}, distance, ascent, descent}, ...],
 *       commute: [[exchangeIdA, exchangeIdB, distanceHundredths], ...],
 *       station_index: {"<station name or alias>": exchangeId, ...},
 *       estimated_duration_seconds: <int, Team.estimated_duration_seconds>,
 *     },
 *     members: [{
 *       membership_id, name, status, willing_to_lead,
 *       preferred_miles (float|null), planned_pace_seconds (int|null),
 *       preferred_station (string|null),
 *     }, ...],
 *     assignments: {"<legIndex>": [membershipId, ...], ...},
 *   }
 *
 * A member with no stated preference for a dimension gets `null` back for it
 * -- never "satisfied" or "violated". That also covers a preferred station
 * that resolves to no course exchange, and a member assigned nowhere yet.
 * Preference values are effective ones (captain overrides already applied by
 * the server); an overridden metric carries `overridden: true` so a satisfied
 * badge is never mistaken for agreement with the member's own answer.
 *
 * No function here touches `document` or any global; every function takes
 * its inputs as arguments and returns plain data, so this file can be
 * required from plain Node for unit tests (see
 * tests/js/leg-metrics.test.js) or imported into the browser unmodified.
 *
 * ---- Units --------------------------------------------------------------
 * Distance (leg + commute) arrives as integer hundredths-of-a-mile
 * (`ceil(miles * 100)`, per data/legs_2026.json's `units` block). All the
 * "*Miles" fields below are already converted to plain miles (float) for
 * display; the raw hundredths are also included for callers that want them.
 *
 * ---- Metrics + thresholds (defaults -- flag if you disagree) ------------
 *
 * Members with no stated preference for a dimension get `null` back for
 * that dimension (no badge), never "satisfied"/"violated". This also covers
 * a `preferred_station` that fails to resolve to a *course* exchange (e.g.
 * "Boeing Access Road", a `non_course_stations` entry with no leg/commute
 * data -- see docs/plans/leg-assignments.md) and a member who has a
 * preference but isn't assigned to any leg yet (nothing to compare against).
 *
 * 1. Distance (`preferred_miles` vs sum of assigned legs' distance):
 *    satisfied if |assigned - preferred| <= 0.5 mi, near if <= 1.5 mi,
 *    else violated.
 * 2. End exchange (`preferred_station` vs the END of the member's LAST
 *    assigned leg, by leg index): satisfied on an exact exchange-id match;
 *    otherwise near if the commute distance between the two exchanges is
 *    <= 1.0 mi, else violated. "No data" if the preferred station doesn't
 *    resolve, or resolves to an exchange with no commute data.
 * 3. Leg 1 / leadership (`willing_to_lead` vs being assigned to leg index
 *    0): only evaluated for members actually assigned to leg 0 (otherwise
 *    the dimension isn't applicable -- not "no data", just not shown).
 *    `willing_to_lead` is a non-nullable boolean column, so there is no "no
 *    data" state for it: satisfied if true, violated if false.
 * 4. Per-leg estimated duration: with multiple runners on a leg, the leg's
 *    pace is the SLOWEST (max) `planned_pace_seconds` among them -- this
 *    matches the ASP domain's `legPace` semantics (see the plan). A leg
 *    with runners but none of them having stated a pace has "no data"
 *    duration (not zero).
 * 5. Team totals: uncovered-leg count, and total estimated duration
 *    (sum of legs with a known duration) vs `Team.estimated_duration_seconds`:
 *    satisfied within 5%, near within 15%, else violated -- but only once
 *    every leg is covered AND has pace data, otherwise "no data" (a partial
 *    total is misleading, not a real comparison).
 */

export const THRESHOLDS = {
    distanceSatisfiedMi: 0.5,
    distanceNearMi: 1.5,
    endExchangeNearMi: 1.0,
    teamDurationSatisfiedPct: 0.05,
    teamDurationNearPct: 0.15,
};

// ---- Small shared helpers ------------------------------------------------

function classify(absDiff, satisfiedMax, nearMax) {
    if (absDiff <= satisfiedMax) return 'satisfied';
    if (absDiff <= nearMax) return 'near';
    return 'violated';
}

function pairKey(a, b) {
    return a <= b ? `${a}-${b}` : `${b}-${a}`;
}

function hasValue(x) {
    return x !== null && x !== undefined;
}

/** Leg indexes (numbers, ascending) a member is assigned to. */
export function legIndexesForMember(assignments, membershipId) {
    const indexes = [];
    for (const [legIndexStr, membershipIds] of Object.entries(assignments || {})) {
        if (membershipIds.includes(membershipId)) indexes.push(Number(legIndexStr));
    }
    return indexes.sort((a, b) => a - b);
}

function legByIndex(course, legIndex) {
    return course.legs.find(leg => leg.index === legIndex) || null;
}

/** Sum of `leg.distance` (hundredths of a mile) across the given leg indexes. */
export function sumDistanceHundredths(course, legIndexes) {
    return legIndexes.reduce((total, legIndex) => {
        const leg = legByIndex(course, legIndex);
        return total + (leg ? leg.distance : 0);
    }, 0);
}

/**
 * Symmetric exchange-id-pair -> distance (hundredths of a mile) lookup built
 * from `course.commute`. Only pairs the course actually has data for are
 * present; a missing pair means "no data" for the caller (this is how
 * non-course stations like "Boeing Access Road" naturally fall out).
 */
export function buildCommuteLookup(course) {
    const lookup = new Map();
    for (const [a, b, distance] of (course.commute || [])) {
        lookup.set(pairKey(a, b), distance);
    }
    return lookup;
}

/** Resolve a `preferred_station` name to an exchange id, or null if unknown. */
export function resolveStationId(course, stationName) {
    if (!stationName) return null;
    const index = course.station_index || {};
    const id = index[stationName.trim()];
    return hasValue(id) ? id : null;
}

// ---- Per-member metrics --------------------------------------------------

/** Total assigned distance vs `preferred_miles`. Null if no preference stated. */
export function memberDistanceMetric(member, course, assignments) {
    if (!hasValue(member.preferred_miles)) return null;

    const legIndexes = legIndexesForMember(assignments, member.membership_id);
    const assignedHundredths = sumDistanceHundredths(course, legIndexes);
    const assignedMiles = assignedHundredths / 100;
    const preferredMiles = member.preferred_miles;
    const diffMiles = assignedMiles - preferredMiles;
    const status = classify(Math.abs(diffMiles), THRESHOLDS.distanceSatisfiedMi, THRESHOLDS.distanceNearMi);

    return {
        dimension: 'distance',
        status,
        assignedMiles,
        preferredMiles,
        diffMiles,
        legCount: legIndexes.length,
        tooltip: `${assignedMiles.toFixed(1)} mi assigned, wanted ${preferredMiles.toFixed(1)} mi`,
    };
}

/**
 * End exchange (the END of the member's LAST assigned leg, by leg index) vs
 * `preferred_station`. Null if no preference stated, the member isn't
 * assigned anywhere yet, the preference doesn't resolve to a known station,
 * or it resolves to a station with no commute data (non-course station).
 */
export function memberEndExchangeMetric(member, course, assignments, commuteLookup) {
    if (!member.preferred_station) return null;

    const legIndexes = legIndexesForMember(assignments, member.membership_id);
    if (legIndexes.length === 0) return null;

    const lastLegIndex = legIndexes[legIndexes.length - 1];
    const lastLeg = legByIndex(course, lastLegIndex);
    if (!lastLeg) return null;

    const endExchangeId = lastLeg.end.id;
    const preferredId = resolveStationId(course, member.preferred_station);
    if (preferredId === null) return null;

    if (preferredId === endExchangeId) {
        return {
            dimension: 'endExchange',
            status: 'satisfied',
            distanceMiles: 0,
            endExchangeName: lastLeg.end.name,
            preferredStation: member.preferred_station,
            tooltip: `Ends at ${lastLeg.end.name}, exactly matching preferred stop ${member.preferred_station}`,
        };
    }

    const lookup = commuteLookup || buildCommuteLookup(course);
    const distanceHundredths = lookup.get(pairKey(preferredId, endExchangeId));
    if (!hasValue(distanceHundredths)) return null; // non-course station (or otherwise unconnected) -- no data

    const distanceMiles = distanceHundredths / 100;
    const status = distanceMiles <= THRESHOLDS.endExchangeNearMi ? 'near' : 'violated';

    return {
        dimension: 'endExchange',
        status,
        distanceMiles,
        endExchangeName: lastLeg.end.name,
        preferredStation: member.preferred_station,
        tooltip: `Ends at ${lastLeg.end.name}, ${distanceMiles.toFixed(1)} mi from preferred stop ${member.preferred_station}`,
    };
}

/**
 * Leg-1 leadership: only meaningful for members assigned to leg index 0.
 * `willing_to_lead` is non-nullable, so there's no "no data" state -- just
 * "not applicable" (null) for anyone not on leg 0.
 */
export function memberLeadLegMetric(member, assignments) {
    const legZero = (assignments && assignments[0]) || [];
    if (!legZero.includes(member.membership_id)) return null;

    const status = member.willing_to_lead ? 'satisfied' : 'violated';
    return {
        dimension: 'leadLeg',
        status,
        willingToLead: !!member.willing_to_lead,
        tooltip: member.willing_to_lead
            ? 'Assigned to leg 1, as volunteered'
            : 'Assigned to leg 1 without volunteering to lead',
    };
}

/** All per-member metrics for one member, keyed by dimension. */
export function memberMetrics(member, course, assignments, commuteLookup) {
    return {
        distance: memberDistanceMetric(member, course, assignments),
        endExchange: memberEndExchangeMetric(member, course, assignments, commuteLookup),
        leadLeg: memberLeadLegMetric(member, assignments),
    };
}

// ---- Per-leg metrics ------------------------------------------------------

/**
 * Estimated duration for one leg. With multiple runners, the leg's pace is
 * the SLOWEST (max) stated pace among them (matches the ASP domain's
 * `legPace`). `covered` is false when nobody is assigned; `durationSeconds`
 * is null when the leg has runners but none of them stated a pace.
 */
export function legDurationMetric(leg, assignments, membersById) {
    const membershipIds = (assignments && assignments[leg.index]) || [];
    if (membershipIds.length === 0) {
        return { legIndex: leg.index, covered: false, durationSeconds: null, paceSeconds: null };
    }

    const paces = membershipIds
        .map(id => membersById.get(id))
        .filter(Boolean)
        .map(m => m.planned_pace_seconds)
        .filter(hasValue);

    if (paces.length === 0) {
        return { legIndex: leg.index, covered: true, durationSeconds: null, paceSeconds: null };
    }

    const paceSeconds = Math.max(...paces);
    const durationSeconds = paceSeconds * (leg.distance / 100);
    return { legIndex: leg.index, covered: true, durationSeconds, paceSeconds };
}

// ---- Team-level metrics ---------------------------------------------------

/**
 * Team totals: uncovered-leg count, legs missing pace data, total estimated
 * duration, and how that total compares to `Team.estimated_duration_seconds`
 * (satisfied within 5%, near within 15%, else violated) -- only once every
 * leg is covered and has pace data, otherwise `status` is 'no data' (a
 * partial total is misleading, not a real comparison).
 */
export function teamMetrics(course, assignments, membersById) {
    const legMetrics = course.legs.map(leg => legDurationMetric(leg, assignments, membersById));

    const uncoveredLegsCount = legMetrics.filter(m => !m.covered).length;
    const legsMissingPaceCount = legMetrics.filter(m => m.covered && !hasValue(m.durationSeconds)).length;
    const totalEstimatedDurationSeconds = legMetrics.reduce((sum, m) => sum + (m.durationSeconds || 0), 0);
    const teamEstimatedDurationSeconds = hasValue(course.estimated_duration_seconds)
        ? course.estimated_duration_seconds
        : null;

    let status = 'no data';
    let diffSeconds = null;
    let diffPct = null;

    if (
        uncoveredLegsCount === 0 &&
        legsMissingPaceCount === 0 &&
        hasValue(teamEstimatedDurationSeconds) &&
        teamEstimatedDurationSeconds > 0
    ) {
        diffSeconds = totalEstimatedDurationSeconds - teamEstimatedDurationSeconds;
        diffPct = Math.abs(diffSeconds) / teamEstimatedDurationSeconds;
        status = classify(diffPct, THRESHOLDS.teamDurationSatisfiedPct, THRESHOLDS.teamDurationNearPct);
    }

    return {
        uncoveredLegsCount,
        legsMissingPaceCount,
        totalEstimatedDurationSeconds,
        teamEstimatedDurationSeconds,
        diffSeconds,
        diffPct,
        status,
        legs: legMetrics,
    };
}

// ---- Top-level entry point ------------------------------------------------

/**
 * Compute every metric for the current board state. Returns:
 *   {
 *     members: {"<membershipId>": {distance, endExchange, leadLeg}, ...},
 *     legs: {"<legIndex>": {legIndex, covered, durationSeconds, paceSeconds}, ...},
 *     team: {...see teamMetrics},
 *   }
 * Each per-member/per-leg dimension is either a metric object or null ("no
 * data" / not applicable) -- never a bare "violated" guess.
 */
export function computeMetrics(state) {
    const { course, members, assignments } = state || {};
    if (!course || !members) {
        return { members: {}, legs: {}, team: null };
    }

    const membersById = new Map(members.map(m => [m.membership_id, m]));
    const commuteLookup = buildCommuteLookup(course);

    const members_ = {};
    for (const member of members) {
        members_[member.membership_id] = memberMetrics(member, course, assignments, commuteLookup);
    }

    const legs_ = {};
    for (const leg of course.legs) {
        legs_[leg.index] = legDurationMetric(leg, assignments, membersById);
    }

    const team = teamMetrics(course, assignments, membersById);

    return { members: members_, legs: legs_, team };
}
