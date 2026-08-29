% team-assign.lp -- per-team "Optimize remaining" instance program.
%
% Derived from relay-scheduler (sibling repo, read-only source of truth):
%   repo:   ../light-rail-relay/relay-scheduler
%   file:   lrr2026/team-optimize.lpx
%   commit: f2e941560bfe42f6646fffdab560e5532c4a9d74 (2026-04-23)
%
% Load together with the vendored scheduling-domain.lp (also in
% app/static/asp/) plus facts generated at runtime by
% app/static/js/leg-solver.js from the current board state:
%   participant/1, leg/3, distance/3, ascent/3, descent/3,
%   commuteDistance/3, preferredDistance/2, preferredPace/2,
%   preferredEndExchange/2, willingToLead/1, and assignment/2 PIN facts for
%   every chip already placed on the board.
%
% ---------------------------------------------------------------------------
% Changes from team-optimize.lpx
% ---------------------------------------------------------------------------
%  1. Hardcoded `participant("A";"B";"C";"D").` removed -- participants are
%     generated facts, one per current team member.
%  2. Inline preference stubs removed:
%       - `preferredDistance(P, Ideal) :- ... totalLength/participantCount`
%         (a uniform mean-distance target for everyone)
%       - `preferredPace(P, 600)` (a uniform stated pace for everyone)
%       - `willingToLead(P) :- participant(P)` (everyone "volunteers")
%     Real per-member preferences arrive as facts instead, present ONLY for
%     members who actually stated them (this file's `dist-even-close` weak
%     constraint below is the fallback for members who did *not* state a
%     preferredDistance -- see priority 3 below).
%  3. Coverage constraint RELAXED from exactly-1 to at-least-1. Multiple
%     runners per leg are allowed on this board (design decision 2 in the
%     plan), and a leg the captain has already pinned two people onto must
%     never make the program UNSAT. The solver only has to add single
%     runners to legs nobody has claimed yet; `legPace`'s `#max` (in
%     scheduling-domain.lp) already handles shared legs correctly.
%
% ---------------------------------------------------------------------------
% Objective priorities (most to least important -- clingo optimizes the
% highest `@ Priority` level first, then breaks ties with the next level
% down; see scheduling-domain.lp for what each `objective(Priority, Name)`
% name actually minimizes/maximizes over)
% ---------------------------------------------------------------------------
%   5  dist-pref-overage -- don't blow past someone's stated distance cap.
%      The complaint a captain is most likely to hear about, so it's fixed
%      first.
%   4  commute-pref      -- land people near the exchange they asked to end
%      at. Getting picked up/rejoining transit in the wrong place is a real
%      logistics problem, so this outranks fine-tuning exact mileage.
%   3  dist-even-close   -- once the above are settled, keep everyone within
%      ~1 mile (100 hundredths) of the course's fair-share mean distance.
%      Guarded to members with NO stated `preferredDistance` -- members who
%      did state one are already covered by priorities 5/2, and mixing the
%      mean-based target in for them would fight their explicit preference.
%      This is what keeps a no-preference member from getting stuck with
%      whatever's left over.
%   2  dist-pref         -- fine-tune towards each member's *exact* stated
%      distance (symmetric |Actual - Preferred|), refining beyond just
%      avoiding overage.
%   1  pace-pref         -- try to seat people on legs run near their stated
%      pace.
%   0  exchange-count    -- minor tie-breaker: fewer separate running
%      stints (non-consecutive legs) for the same person.
%
% Deliberately NOT ported from team-optimize.lpx / scheduling-domain.lp:
%   - "duration": minimizing total race time rewards stacking the fastest
%     runners onto every leg, which actively fights the fairness/preference
%     objectives above and isn't something any member expresses a
%     preference for. The board already surfaces estimated duration as an
%     informational badge without the solver needing to chase it.
%   - "total-rest-time": team-optimize.lpx's `restTime`/`totalRestTime`
%     maximize was tied to its original hardcoded 4-person scenario and
%     isn't a preference any member states. Dropping it keeps the ground
%     program (and wasm solve time) smaller, which matters for a
%     client-side, first-click, cancellable solve.
objective(
    5, "dist-pref-overage";
    4, "commute-pref";
    3, "dist-even-close";
    2, "dist-pref";
    1, "pace-pref";
    0, "exchange-count"
).

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
% Event constraints
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

% At least 1 person on each leg (relaxed from team-optimize.lpx's exactly-1
% -- see change #3 above).
:- legCoverage(T, 0), leg(T, _, _).

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
% Generate
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

% Each participant runs or does not run each leg. Already-pinned
% assignment/2 facts (emitted by leg-solver.js for every chip currently on
% the board) are simply additional support for the same atom: clingo is
% fine with a fact and a choice-rule head coinciding, and the pin is
% unconditionally true regardless of what the choice construct would
% otherwise have picked, so a pinned assignment can never be "un-chosen" by
% the solver.
0{
   assignment(P, leg(T, Start, Stop))
}1 :- participant(P), leg(T, Start, Stop).

%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%
% Distance-evenness fallback (priority 3, "dist-even-close")
%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%

participantCount(N) :- N = #count{P: participant(P)}.
totalLength(Length) :- Length = #sum{Distance,T: legDist(T,Distance)}.

% Just get close enough to the fair-share mean -- only for participants who
% have no stated preferredDistance of their own (see priority 3's note
% above).
:~ |Actual - Ideal| > 100, participantDist(P, Actual), Ideal=TotalLength/NumParticipants, totalLength(TotalLength), participantCount(NumParticipants), participant(P), not preferredDistance(P, _), objective(Priority, "dist-even-close"). [1 @ Priority, P]
