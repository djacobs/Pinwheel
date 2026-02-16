# Pinwheel Fates — Teleprompter Script

---

## HOOK (0:00–0:25)

Pinwheel Fates — a basketball simulation game where players choose teams and govern the rules together. Sports drives fierce opinions and loyalty — the perfect arena to test whether AI can help groups make better decisions together. They play through Discord and on the web. After each round of games, players propose and vote on rule changes — and Opus interprets the proposals, simulates consequences, and transparently shares what it sees.

---

## WHY A GAME (0:25–0:55)

Games are where humanity prototypes its next societies — low stakes, high reps, fast feedback. Coalition detection, power concentration, participation gaps — these are the same patterns that matter in newsrooms, neighborhood associations, and city councils.

Pinwheel is a place to experiment with direct democracy and understand what AI-augmented decision-making can actually do. Not a finished handbook — a step.

---

## DEMO: PROPOSE (0:55–1:05)

Here, a player proposes a rule change. Opus interprets the proposal, and confirms with the player. The community votes on rules between rounds.

---

## DEMO: SIMULATE + REFLECT (1:05–1:30)

The rule proposed at noon impacts the next round of games, starting at 1pm. Opus reports feedback to the league about the impact of the rules, and gives direct, private feedback to players — visible only to them — surfacing patterns in their governance behavior. The shared report surfaces league-wide dynamics: coalitions forming, power concentrating, voices going silent.

---

## IMPACT (1:30–1:40)

Opus helps to illuminate hidden dynamics, amplifying human judgment by making collective decisions legible. Human players always have the last word.

---

## WHY DISCORD (1:40–1:55)

On top of a basketball simulator and Opus-powered rules engine, I chose Discord for user interaction. Any chat app with persistent memory can sit on the same stack. Discord is only the proof of concept, and different communities will choose different tools.

---

## OPUS: FOUR ROLES (1:55–2:10)

Opus played four roles. First, build partner — 200 commits over six days. Constitutional interpreter, social reporter — behavioral profiling, coalition detection, private reflections, and broadcaster — game commentary woven with league context.

---

## OPUS: AGENT-NATIVE (2:10–2:30)

Nearly 2,000 tests measure how much code exists — and that is too many. The vision: ship narrative, not code. Replace classes and validators with a product document describing input and output. The dev environment looks less like an IDE and more like the narrative design tools game makers use.

With the model of six months from now, each component shrinks from hundreds of lines to a prompt.

---

## DEPTH: MAKE IT TAKE IT (2:30–2:55)

Our biggest challenge was convincing Opus to expand its scope: a player proposed "make it take it" — a real basketball rule meaning the scoring team keeps possession. Opus knows this, and in open conversation, it explains the rule perfectly. But our structured interpreter was unable to modify the game, because the pipeline was optimized for schema-compatible fields. The model knew the answer. Our code prevented it from using what it knew.

---

## CLOSE (2:55–3:05)

Pinwheel is my expression of Opus helping groups make better decisions together by amplifying what we already do well: negotiate, change minds, and form coalitions.

**pinwheel.fly.dev**
