# Launch Drills — Proving the Governance Loop Live

Four drills, run on production at slow or manual pace, with the approval gate
(`PINWHEEL_RULES_REQUIRE_APPROVAL=true`) on. Each drill proves one enactment
path end-to-end with real Discord interactions. Run them in order — each is
strictly wilder than the last. The admin (you) participates: every drill ends
with an approval DM before anything goes live.

## Prerequisites

- [ ] Prod deployed with tonight's build; `flyctl status` healthy
- [ ] Fresh backup: `flyctl ssh console -C "cp /data/pinwheel.db /data/pinwheel.db.bak"`
- [ ] Pace set to slow or manual: `curl -X POST https://pinwheel.fly.dev/api/pace -H 'Content-Type: application/json' -d '{"pace":"slow"}'`
- [ ] You are enrolled via `/join` and hold PROPOSE tokens (`/tokens`)

## Drill 1 — Parameter change (the classic)

1. `/propose Three pointers are worth 4 points`
2. Confirm the AI interpretation card (parameter `three_point_value` → 4).
3. Vote yes: `/vote yes`. Get a second governor to vote if available.
4. Wait one tally cycle (minimum voting period) + the tally round.
5. **Verify — pass recorded:** tally embed shows PASSED; you receive the
   *"Passed Proposal Awaiting Your Approval"* DM.
6. Tap **Approve & Enact**.
7. **Verify — live:** next round's box scores show 4-point threes;
   `/rules` (web) lists the change; `rule.enacted` in the event log.

## Drill 2 — Conditional effect (hook callback)

1. `/propose In the final period, every made shot gives the shooter's team a small hot-hand bonus`
2. Confirm; vote yes; wait for tally; approve via DM.
3. **Verify:** `/effects` (Discord) and the web rules page both list the
   effect with its hook point; `effect.registered` event exists; game
   commentary reflects the mechanic.

## Drill 3 — Structural change (game definition patch)

1. `/propose Add a half-court shot called The Prayer worth 5 points, very hard to make`
2. Confirm; vote yes (Tier 3 — needs 60%); approve via DM.
3. **Verify:** play-by-play shows "The Prayer" attempts with custom
   narration within a few rounds (selection_weight makes it rare — check
   several games); web game detail renders the new action.

## Drill 4 — Generated code (Code Council, the whole gauntlet)

1. `/propose Whenever a team is down by 10 or more, their bench erupts and the next steal is worth 2 bonus points`
   (anything beyond the action primitives works — the point is to trigger codegen)
2. Confirm. The proposal goes to vote with the AI's approximation live-able;
   the Code Council (3 AI reviewers) runs in the background.
3. Vote yes (Tier 5 — needs 67%); approve the *proposal* via the gate DM.
4. **Verify — council:** you receive the codegen pending DM after unanimous
   council approval. Review the generated code in the DM (or `/admin`).
5. Tap approve on the codegen DM.
6. **Verify — live:** `/effects` shows the codegen effect as Approved;
   the mechanic fires in subsequent games; if the sandbox auto-disables it,
   the disable persists across restart (`flyctl apps restart pinwheel`, then
   `/effects` again).

## After the drills

- [ ] `/repeal` one of the drill effects to prove the exit path.
- [ ] Amend a live proposal (`/amend`) and verify the *amended* text is what
      enacts (this was broken until tonight — watch it specifically).
- [ ] Check the eval dashboard (`/admin/evals`) recorded the cycle.
- [ ] Reset pace: `{"pace":"normal"}` for launch week.

## Monitoring during launch week

Watch for these three signals (`flyctl logs`):

| Signal | Meaning | Response |
|---|---|---|
| `is_mock_fallback=True` on interpretations | Anthropic API failing — proposals get keyword-matched, not understood | Check API key/quota; re-run interpretation via deferred queue |
| `deferred_interpretation_error` / tick exceptions | Background interpreter stuck | `flyctl logs` for traceback; proposals stay pending, not lost |
| Passed tier-3+ proposal with no `effect.registered` within a round | Enactment pipeline regression | Check `proposal.enactment_held` (awaiting your approval?) before assuming a bug |
