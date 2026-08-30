/**
 * Leg Assignment Metrics
 *
 * Pure, DOM-free computation over the board state -- exactly what
 * leg-board.js's `onAssignmentsChanged(state)` hook passes and what GET
 * /team/<id>/assignments returns. Nothing here touches `document`, so the
 * whole file is unit-testable from plain Node (tests/js/leg-metrics.test.mjs).
 *
 * Distances arrive as integer hundredths of a mile per the course model's
 * `units` block; the `*Miles` fields below are already converted, with the
 * raw hundredths kept for callers that want them.
 *
 * A member with no stated preference for a dimension gets `null` back for it
 * -- never "satisfied" or "violated". That also covers a preferred station
 * that resolves to no course exchange, and a member assigned nowhere yet.
 * Preference values are effective ones (captain overrides already applied by
 * the server); an overridden metric carries `overridden: true` so a satisfied
 * badge is never mistaken for agreement with the member's own answer.
 *
 * Thresholds for satisfied/near/violated live in THRESHOLDS below; each
 * metric function documents how it uses them.
 */

import { legKey } from './leg-keys.js';

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

// ---- Captain overrides ---------------------------------------------------

/** Has a captain adjusted this member's stated `field`? */
export function isOverridden(member, field) {
    return Object.prototype.hasOwnProperty.call(member.overrides || {}, field);
}

/**
 * Parenthesised tooltip suffix naming what the member originally stated for
 * `field`, or '' if the captain hasn't adjusted it. `format` renders the
 * stated value for display.
 *
 * A stale override (the member has edited their own answer since) says so:
 * the captain's value still wins, but silently outvoting a fresh answer is
 * exactly the failure mode worth surfacing.
 */
export function overrideNote(member, field, format = String) {
    if (!isOverridden(member, field)) return '';

    const stated = (member.stated || {})[field];
    const base = hasValue(stated)
        ? `captain-adjusted from ${format(stated)}`
        : 'set by captain; member stated no preference';
    const stale = (member.stale_override_fields || []).includes(field);
    return stale
        ? ` (${base} -- member has since changed their own answer)`
        : ` (${base})`;
}

/**
 * Leg keys a member is assigned to, in course running order.
 *
 * Order comes from `course.legs` rather than the key itself: exchange ids
 * ascend within a branch but not across the Y, so sorting keys directly would
 * interleave the two branches.
 */
export function legKeysForMember(course, assignments, membershipId) {
    const held = new Set();
    for (const [key, membershipIds] of Object.entries(assignments || {})) {
        if (membershipIds.includes(membershipId)) held.add(key);
    }
    return (course && course.legs ? course.legs : [])
        .map(legKey)
        .filter(key => held.has(key));
}

function legByKey(course, key) {
    return (course.legs || []).find(leg => legKey(leg) === key) || null;
}

/** Sum of `leg.distance` (hundredths of a mile) across the given leg keys. */
export function sumDistanceHundredths(course, legKeys) {
    return legKeys.reduce((total, key) => {
        const leg = legByKey(course, key);
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

    const legKeys = legKeysForMember(course, assignments, member.membership_id);
    const assignedHundredths = sumDistanceHundredths(course, legKeys);
    const assignedMiles = assignedHundredths / 100;
    const preferredMiles = member.preferred_miles;
    const diffMiles = assignedMiles - preferredMiles;
    const status = classify(Math.abs(diffMiles), THRESHOLDS.distanceSatisfiedMi, THRESHOLDS.distanceNearMi);

    const note = overrideNote(member, 'preferred_miles', miles => `${Number(miles).toFixed(1)} mi`);

    return {
        dimension: 'distance',
        status,
        assignedMiles,
        preferredMiles,
        diffMiles,
        legCount: legKeys.length,
        overridden: isOverridden(member, 'preferred_miles'),
        tooltip: `${assignedMiles.toFixed(1)} mi assigned, wanted ${preferredMiles.toFixed(1)} mi${note}`,
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

    const legKeys = legKeysForMember(course, assignments, member.membership_id);
    if (legKeys.length === 0) return null;

    const lastLeg = legByKey(course, legKeys[legKeys.length - 1]);
    if (!lastLeg) return null;

    const endExchangeId = lastLeg.end.id;
    const preferredId = resolveStationId(course, member.preferred_station);
    if (preferredId === null) return null;

    const note = overrideNote(member, 'preferred_station');
    const overridden = isOverridden(member, 'preferred_station');

    if (preferredId === endExchangeId) {
        return {
            dimension: 'endExchange',
            status: 'satisfied',
            distanceMiles: 0,
            endExchangeName: lastLeg.end.name,
            preferredStation: member.preferred_station,
            overridden,
            tooltip: `Ends at ${lastLeg.end.name}, exactly matching preferred stop ${member.preferred_station}${note}`,
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
        overridden,
        tooltip: `Ends at ${lastLeg.end.name}, ${distanceMiles.toFixed(1)} mi from preferred stop ${member.preferred_station}${note}`,
    };
}

/**
 * What a member is actually assigned right now, regardless of what they
 * asked for: total miles, the end of their last leg, and effective pace.
 *
 * Effective pace is the distance-weighted average of each leg's own pace --
 * the slowest stated pace among that leg's runners (see `legDurationMetric`)
 * -- so pairing with a slower runner shows up here. Legs with no pace data
 * are skipped, not counted as zero.
 */
export function memberCurrentSummary(member, course, assignments, legMetricsByKey) {
    const legKeys = legKeysForMember(course, assignments, member.membership_id);
    const assignedMiles = sumDistanceHundredths(course, legKeys) / 100;

    let pacedDurationSeconds = 0;
    let pacedHundredths = 0;
    for (const key of legKeys) {
        const leg = legByKey(course, key);
        const legMetric = (legMetricsByKey || {})[key];
        if (leg && legMetric && hasValue(legMetric.durationSeconds)) {
            pacedDurationSeconds += legMetric.durationSeconds;
            pacedHundredths += leg.distance;
        }
    }

    const lastLeg = legKeys.length ? legByKey(course, legKeys[legKeys.length - 1]) : null;

    return {
        legCount: legKeys.length,
        assignedMiles,
        paceSeconds: pacedHundredths > 0 ? pacedDurationSeconds / (pacedHundredths / 100) : null,
        endExchangeName: lastLeg ? lastLeg.end.name : null,
    };
}

/** All per-member metrics for one member, keyed by dimension. */
export function memberMetrics(member, course, assignments, commuteLookup, legMetricsByKey) {
    return {
        distance: memberDistanceMetric(member, course, assignments),
        endExchange: memberEndExchangeMetric(member, course, assignments, commuteLookup),
        current: memberCurrentSummary(member, course, assignments, legMetricsByKey),
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
    const membershipIds = (assignments && assignments[legKey(leg)]) || [];
    if (membershipIds.length === 0) {
        return { legKey: legKey(leg), covered: false, durationSeconds: null, paceSeconds: null };
    }

    const paced = membershipIds
        .map(id => membersById.get(id))
        .filter(Boolean)
        .filter(m => hasValue(m.planned_pace_seconds));

    if (paced.length === 0) {
        return { legKey: legKey(leg), covered: true, durationSeconds: null, paceSeconds: null };
    }

    // Keep the slowest *runner*, not just their number, so the leg can say
    // whether the pace it was estimated from is a captain-adjusted one.
    const slowest = paced.reduce((a, b) => (b.planned_pace_seconds > a.planned_pace_seconds ? b : a));
    const paceSeconds = slowest.planned_pace_seconds;
    const durationSeconds = paceSeconds * (leg.distance / 100);
    return {
        legKey: legKey(leg),
        covered: true,
        durationSeconds,
        paceSeconds,
        paceOverridden: isOverridden(slowest, 'planned_pace_seconds'),
    };
}

// ---- Schedule (wall-clock handoff times) ----------------------------------

/**
 * Wall-clock start/end for each leg, accumulated from `course.event_start_time`.
 *
 * The course is a Y, not a line (see `legs_for()` in course_service.py), so
 * the clock is driven by when the baton *arrives* at each exchange rather
 * than by a cursor down a single chain. A leg starting at a terminus (an
 * exchange no leg ends at) starts at the event start, so both branches of an
 * Interline course start together. Any other leg starts at the LATEST
 * arrival among the legs ending at its start exchange -- a converging
 * exchange waits for the slower branch.
 *
 * Unknowns propagate and are never bridged with an invented pace: a leg with
 * no duration gets a start but no end (`no-duration`); a leg whose start
 * exchange has any unknown arrival can't be placed (`awaiting-arrival`); a
 * non-terminus nothing has reached yet is a `discontinuity`; no parseable
 * event start blanks everything (`no-event-start`).
 *
 * Returns `{byKey, firstUnknownLegKey, allKnown}`, each entry
 * `{legKey, startMs, endMs, durationSeconds, known, reason}` with epoch-ms
 * times that are null when unknown.
 */
export function legScheduleMetrics(course, legMetricsByKey) {
    const byKey = {};
    const legs = (course && course.legs) || [];

    const eventStartMs = course && course.event_start_time
        ? Date.parse(course.event_start_time)
        : NaN;
    const startMs = Number.isNaN(eventStartMs) ? null : eventStartMs;

    // Exchanges no leg ends at: where the race starts from. One per branch.
    const endpoints = new Set(legs.map(leg => leg.end.id));
    const isTerminus = exchangeId => !endpoints.has(exchangeId);

    // exchange id -> {latestMs, hasUnknown}, built up as the walk places each
    // leg's end. `hasUnknown` is what stops a converging exchange from being
    // scheduled off whichever branch happens to be known.
    const arrivals = new Map();
    function recordArrival(exchangeId, endMs) {
        const current = arrivals.get(exchangeId) || { latestMs: null, hasUnknown: false };
        if (hasValue(endMs)) {
            current.latestMs = hasValue(current.latestMs) ? Math.max(current.latestMs, endMs) : endMs;
        } else {
            current.hasUnknown = true;
        }
        arrivals.set(exchangeId, current);
    }

    let firstUnknownLegKey = null;

    for (const leg of legs) {
        const key = legKey(leg);
        const durationSeconds = (legMetricsByKey && legMetricsByKey[key])
            ? legMetricsByKey[key].durationSeconds
            : null;

        let legStartMs = null;
        let reason = null;

        if (startMs === null) {
            reason = 'no-event-start';
        } else if (isTerminus(leg.start.id)) {
            legStartMs = startMs;
        } else {
            const arrival = arrivals.get(leg.start.id);
            if (!arrival) {
                // Nothing has reached this exchange yet, but something later
                // in the course does end here -- the order can't be run.
                reason = 'discontinuity';
            } else if (arrival.hasUnknown || !hasValue(arrival.latestMs)) {
                reason = 'awaiting-arrival';
            } else {
                legStartMs = arrival.latestMs;
            }
        }

        const legEndMs = hasValue(legStartMs) && hasValue(durationSeconds)
            ? legStartMs + durationSeconds * 1000
            : null;

        if (legEndMs === null && reason === null) reason = 'no-duration';

        byKey[key] = {
            legKey: key,
            startMs: legStartMs,
            endMs: legEndMs,
            durationSeconds,
            known: legEndMs !== null,
            reason,
        };
        if (legEndMs === null && !firstUnknownLegKey) firstUnknownLegKey = key;

        recordArrival(leg.end.id, legEndMs);
    }

    return { byKey, firstUnknownLegKey, allKnown: firstUnknownLegKey === null };
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
 *     members: {"<membershipId>": {distance, endExchange, current}, ...},
 *     legs: {"<legKey>": {legKey, covered, durationSeconds, paceSeconds}, ...},
 *     team: {...see teamMetrics},
 *     schedule: {...see legScheduleMetrics},
 *   }
 * Each per-member/per-leg dimension is either a metric object or null ("no
 * data" / not applicable) -- never a bare "violated" guess.
 */
export function computeMetrics(state) {
    const { course, members, assignments } = state || {};
    if (!course || !members) {
        return {
            members: {}, legs: {}, team: null,
            schedule: { byKey: {}, firstUnknownLegKey: null, allKnown: true },
        };
    }

    const membersById = new Map(members.map(m => [m.membership_id, m]));
    const commuteLookup = buildCommuteLookup(course);

    // Computed first: memberCurrentSummary() needs each leg's effective pace
    // to report a member's own effective pace (see its doc comment).
    const legs_ = {};
    for (const leg of course.legs) {
        legs_[legKey(leg)] = legDurationMetric(leg, assignments, membersById);
    }

    const members_ = {};
    for (const member of members) {
        members_[member.membership_id] = memberMetrics(member, course, assignments, commuteLookup, legs_);
    }

    const team = teamMetrics(course, assignments, membersById);
    const schedule = legScheduleMetrics(course, legs_);

    return { members: members_, legs: legs_, team, schedule };
}
