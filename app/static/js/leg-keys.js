/**
 * Leg identity, shared by the board, the metrics and the solver.
 *
 * A leg is identified by its start/end exchange pair, not by a running index.
 * Index is per-line -- leg 20 is Roosevelt->Northgate on the 1 Line but
 * Northgate->Pinehurst on the 2 Line -- whereas the pair is stable across
 * lines, so a leg on the shared Chinatown-Lynnwood trunk is the same leg no
 * matter which line a team registered for.
 * One canonical string spelling keeps board state, metric lookups and
 * solver atoms agreeing without repeated tuple juggling.
 */

/** Canonical string key for a course leg (`course.legs[]` entry). */
export function legKey(leg) {
    return `${leg.start.id}-${leg.end.id}`;
}

/** Canonical string key for a stored assignment (API payload shape). */
export function assignmentKey(assignment) {
    return `${assignment.start_exchange}-${assignment.end_exchange}`;
}

/** Split a canonical key back into numeric exchange ids. */
export function parseLegKey(key) {
    const [start, end] = String(key).split('-');
    return { start_exchange: Number(start), end_exchange: Number(end) };
}

/** Human label for a leg, e.g. "Federal Way Downtown -> Star Lake". */
export function legLabel(leg) {
    return `${leg.start.name ?? leg.start.id} to ${leg.end.name ?? leg.end.id}`;
}

export const LINE_NAMES = { lrr_1line: '1 Line', lrr_2line: '2 Line' };

/** The digit inside a line's coloured circle (see `.line-pill` in style.css). */
export const LINE_CODES = { lrr_1line: '1', lrr_2line: '2' };

/**
 * A leg's running position, numbered from 1 within each line it belongs to.
 *
 * Position is per-line, so a leg on the shared trunk has two of them and
 * shows both: "14/13" is the 1 Line's leg 14 and the 2 Line's leg 13, one
 * stretch of course that each branch counts differently. A leg on one branch
 * only has the single number.
 *
 * `teamLines` scopes it to the lines the team actually runs, so a single-line
 * team never sees the other line's count.
 *
 * Returns `{text, title}` -- `text` for display, `title` spelling it out --
 * or nulls when the leg carries no usable sequence data.
 */
export function legNumbering(leg, teamLines) {
    // Fall back to the leg's own lines so a number still shows if the course
    // ever arrives without its line list.
    const lines = (teamLines && teamLines.length ? teamLines : leg.lines) || [];
    const positions = lines
        .filter(line => (leg.lines || []).includes(line))
        .map(line => ({ line, number: (leg.sequence ? leg.sequence[line] : undefined) + 1 }))
        .filter(position => Number.isFinite(position.number));

    if (!positions.length) return { text: null, title: null };

    return {
        text: positions.map(position => position.number).join('/'),
        title: positions.length > 1
            ? positions.map(p => `leg ${p.number} on the ${LINE_NAMES[p.line] || p.line}`).join(', ')
            : `Leg ${positions[0].number}`,
    };
}
