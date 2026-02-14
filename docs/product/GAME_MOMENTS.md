# Game Moments — Dramatic Context Checklist

Every player-facing output should reference these contexts when applicable. Before shipping any output system change, check this list.

## Contexts

- **Playoff phase** — semifinal, finals, elimination game
- **Win/loss streaks** — team on 3+ game streak (winning or losing)
- **Comeback narratives** — team was down big but rallied to win
- **Underdog upsets** — low seed beating high seed in playoffs
- **Blowouts** — margin of 15+ points
- **Rule change effects** — "since three-pointers became worth 5, scoring has exploded"
- **Individual dominance** — hooper with 20+ points in consecutive games
- **Rivalry rematches** — teams that split the regular season series
- **Milestone games** — first game, last regular season game, clinch/elimination
- **Season arc position** — early season, playoff race, postseason
- **Governance narrative** — rule just changed, voting window open, controversial proposal

## How to Use

When building or modifying an output system (commentary, reports, embeds, Discord messages, web pages), pass a `NarrativeContext` object computed by `core/narrative.py`. The `NarrativeContext` contains pre-computed versions of all the above contexts for the current round.

Each output system decides which contexts are relevant and how to surface them. Not every context applies to every output — a box score embed doesn't need governance narrative, but a round summary report should mention it.
