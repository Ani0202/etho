# Official problem statements (from the participants-only details page, provided by Aniket)

> Your project must build towards one of the following problem statements:

**Build for Your World.** Bring Meta's models into your domain. Solve a problem you know firsthand,
free up time and mental space, and make something possible that wasn't before.

**Push the Frontier.** Use Meta's models to tackle challenges in software, robotics, manufacturing,
energy, materials and beyond. Give a machine a new capability, accelerate a scientific experiment,
or unlock a better way to understand and control a physical system. Show us what becomes possible
when these models meet the real world.

---

## Etho's statement: Push the Frontier (decided by Aniket)

We do **not** claim "Build for Your World": that statement asks for a problem known firsthand, and
Aniket isn't a behavioral-neuroscience researcher.

How Etho maps to the statement's own words. Every claim here must be backed by a measured number
in the demo and README.

| Statement phrase | Etho's evidence (to be measured) |
|---|---|
| "accelerate a scientific experiment" | Time and cost to score one video: manual scoring vs. Etho, with Etho's agreement with expert labels (CalMS21 F1) |
| "unlock a better way to understand ... a physical system" | Animal behavior quantified from raw video as measured motion (trajectories, distances, contact), from plain-English definitions with zero training labels |
| "when these models meet the real world" | Real lab video (CalMS21), plus a second species (MABe22) with no retraining |
| "Give a machine a new capability" | A camera plus Meta models becomes a behavior-scoring instrument |

Requirements this creates:
- The eval plan must measure **wall-clock time and dollar cost per video**, not only accuracy.
  Manual-scoring time comes from a cited source, never from invented numbers.
- The demo video names "Push the Frontier" in its first 15 seconds and shows the time/cost
  comparison and the accuracy number on screen.

---

## Lead's fit note (2026-10-28, Phase 1)

Etho fits **Push the Frontier** via "accelerate a scientific experiment": behavior scoring is the
manual bottleneck of rodent social-behavior experiments. The cited baseline is *"Scoring for social
behaviors often takes human annotators 3–4× the video's duration to annotate"* (Segalin et al.,
MARS, eLife 2021, Introduction; the paper gives no further source for the figure). The spike
measured SAM 3.1 tracking at about 1x real time and $0.04 per 20 s clip (`docs/SPIKE.md`), so the
time and cost comparison can be measured rather than asserted.

Meta models are central at every stage: SAM 3.1 (tracking), Muse Spark (planner, rule synthesis,
report), and Muse Spark Open on Fireworks and Baseten (adjudication). The second statement phrase,
"a better way to understand ... a physical system", is covered by turning raw video into measured
trajectories, distances, and contact from plain-English definitions.

Still to be measured, not claimed: accuracy (CalMS21 test F1), end-to-end time and cost per video,
and the second species.
