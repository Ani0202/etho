# Demo video plan (PROPOSED, needs Aniket's approval)

The roughly 3-minute video is the primary judging artifact: demo plus video is 25% of the score, and it carries most of
the other 75%. It must show only work done during the event, name "Push the Frontier" in the first 15 s, and
put the accuracy number and the time/cost comparison on screen (`docs/PROBLEM_STATEMENTS.md`).

Numbers in `{braces}` are filled from committed results only: the test-run report and the manifests in
`data/runs/T1`. No number goes on screen that isn't in the repo.

## Script (about 3:00)

| Time | Beat | On screen | Voice-over (draft) |
|---|---|---|---|
| 0:00-0:15 | Hook + statement | Two mice in a CalMS21 clip; title card "Etho · Push the Frontier" | "Behavior scoring is the slowest step in social-behavior experiments: experts take 3-4× the video's length to label it by hand. Etho does it from plain English." |
| 0:15-0:35 | The problem | MARS citation card; a researcher's spreadsheet of hand labels (mock, labeled as illustration) | "Supervised tools need thousands of labeled frames per lab and behavior. Etho needs a sentence." |
| 0:35-1:15 | Live run | App: upload a 2-5 min clip, type/paste definitions, progress stream (planner → SAM 3.1 → rules → strip check) | "Muse Spark picks what to track. SAM 3.1 follows both animals. Muse Spark turns each definition into code we can read, and Muse Spark Open double-checks the borderline moments from frame strips." |
| 1:15-1:45 | Results + audit | Ethogram over the overlay video, per-behavior metrics, the generated detector code, the audit trail (models, effort, cost per stage) | "Every label traces back to a rule and a model call. The researcher can see exactly why a frame was called an attack." |
| 1:45-2:10 | Correction loop | Fix 3-4 bouts → revised rule → before/after shown | "When it's wrong, you fix a few bouts and Etho rewrites the rule." Shown as a feature; no accuracy-gain claim unless test shows one. |
| 2:10-2:35 | Evidence | One results card: test F1 per behavior next to supervised .793 and random .134; `{share}` of supervised; time `{x}`× real time vs 3-4×; `{$/h}` per hour of video | "On 19 untouched test videos, training-free Etho reaches {share} of a supervised model's F1, at {x}× real time instead of 3-4×, for {$/h} per hour of video." Footnote on screen: the dev-tuning disclosure. |
| 2:35-2:50 | Second species | Fly vs. Fly clip with Etho's ethogram (F1 only if ADR-002's Oct 31 bar is met; otherwise labeled "qualitative") | "Same pipeline, new animal, no retraining." |
| 2:50-3:00 | Close | Meta model stack card (SAM 3.1, Muse Spark, Muse Spark Open), repo URL | "Built on Meta's models, open source." |

## Production
- **Recording:** Nov 3 on localhost, using the `demo-reserve` tag (it unlocks Nov 3). The live-run segment is
  recorded live once. If the run is slow, cut the waits and show the real elapsed time on screen.
  The results segment uses that live job, not a precomputed showcase.
- **Fallback:** if the live run fails on the day, use a precomputed showcase run, labeled "precomputed" on screen.
- **Tools:** screen capture (OBS or similar) plus Aniket's voice-over. I can draft the captions, title cards and the
  results card as HTML, rendered from the committed numbers.
- **Honesty checks before export:**
  - every on-screen number matches a file in the repo;
  - "training-free" always comes with the footnote;
  - nothing says "beats supervised";
  - the hand-label spreadsheet is marked as an illustration.

## Questions for Aniket
1. Is this story order right? Problem → live run → audit → correction → evidence → second species.
2. Live recording or precomputed for the main run? I recommend live, with cuts and a visible clock.
3. Will you do the voice-over yourself? Your voice and narration also show the work is yours.
4. Which clip for the live run? I recommend 2-5 minutes of a test video, so the viewer sees Etho on a video it
   was never tuned on. The app run doesn't feed back into the reported test numbers, which are already
   final by then.
