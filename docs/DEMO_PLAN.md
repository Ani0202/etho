# Demo video plan (v2: APPROVED by Aniket 2026-10-28 with changes; script is a draft for his review)

- **Format:** about 3:00 (the maximum length in the Official Rules is still to be confirmed). Aniket records the
  voice-over, and captions are burned in.
- **Schedule:** rough cut Nov 2, using up to $5 of `demo-reserve`, which Aniket unlocked for it. Final recording
  Nov 3.
- **Sources:** every factual claim in the voice-over has a source, listed in the "Sources" section below.
- **Numbers:** they come only from committed files, mainly `eval/results/test1_report.md` section 11.

## Script (draft, ~419 words with the GO fly line)

Pending items are marked ⟨…⟩: the chosen test clip and its traced error (the evaluator is selecting them), and
the fly result (go/no-go by Oct 31).

**0:00-0:20, hook** (on screen: two mice; title "Etho · Push the Frontier")
> Labs that study behavior still score it by hand. Someone watches every second of video and writes down what
> the animals do. For social behavior that takes three to four times the length of the video. This is Etho, for
> Push the Frontier. It is training-free, and on a public benchmark it already reaches 0.58 F1.

**0:20-0:35, who it's for and the gap** (on screen: a real CalMS21 human annotation timeline)
> Behavior scoring runs through neuroscience labs and regulated drug-safety testing. The options today are a
> person with a stopwatch, or software that needs hand-labeled examples of every behavior before it can
> score it. Etho needs a sentence.

**0:35-1:15, live run** (on screen: the app, with a clock visible; a test video ⟨clip⟩ that Etho was never tuned on)
> This is a test video Etho was never tuned on. I paste the dataset authors' own definitions of attack,
> investigation and mount. Muse Spark looks at sample frames and decides what to track. SAM 3.1 follows both mice
> through the whole video, even when one climbs on top of the other. Then Muse Spark turns each definition into
> a short rule in Python, over the tracks: code you can read. For borderline moments, Muse Spark Open looks at a
> strip of frames and confirms or rejects. The clock is real: the video is scored in about its own length.

**1:15-1:45, results and audit trail, with one real error** (on screen: Etho's ethogram over the expert's annotation)
> Here is the result, next to the expert's own annotation. Every label can be traced. Here, Etho called attack
> where the annotator saw investigation. Click it, and you see the rule that fired, the measurements it used, and
> ⟨the frame-strip check and what the model answered⟩, with its cost. Nothing is a black box.

**1:45-2:00, correction loop, about 15 s, no gain claim**
> If the researcher disagrees, they mark a few bouts. Etho rewrites the rule and shows exactly what changed. The
> researcher stays in control.

**2:00-2:30, evidence card** (on screen: per-behavior F1 attack 0.449, investigation 0.635, mount 0.656;
baselines .793, .829 and .864; speed 0.88×, worst 1.54×; $11.25 per hour of video; and the slide footnote)
> We measured it on the 19 official test videos of the CalMS21 benchmark, scored once. With no training, Etho
> reaches 0.58 F1 across attack, investigation and mount. Supervised methods reach 0.79 to 0.86. It scores a
> video unattended in about its own length: 0.88 times real time, 1.54 at worst. Manual scoring takes three to
> four times. And it costs about eleven dollars per hour of video.

**2:30-2:45, second species** (two versions; which one is used depends on the pre-registered Oct 31 go/no-go)
> GO: Same pipeline, new animal. Fruit flies from Caltech's Fly vs. Fly dataset, with wing threat defined in a
> sentence from the literature. No retraining. ⟨F1 vs random reference⟩.
> NO-GO: Same pipeline, new animal: fruit flies, scored from a one-sentence definition with no retraining. This
> one is a qualitative demo; we haven't measured it to our standard yet.

**2:45-3:00, close** (on screen: Meta model stack, who it's for, repo and live link)
> Etho runs on Meta's models: SAM 3.1, Muse Spark and Muse Spark Open. It's for every lab that still scores
> behavior by hand. The code is open, and you can try it at the link below.

Captions: the script text above, one caption per sentence. Exact timings are set from the recorded voice-over
(SRT file generated on Nov 2).

## Sources for every claim (all in the repo or cited)
| Claim | Source |
|---|---|
| Scored by hand; "generally done through manual annotation, a highly time-consuming and tedious process" | Segalin et al. 2021, eLife, MARS, doi:10.7554/eLife.63720, Abstract |
| 3-4× the video's length | Same paper, Introduction: "Scoring for social behaviors often takes human annotators 3–4× the video's duration to annotate" |
| Regulated drug-safety testing evaluates behavior | ICH S7A (2000), §2.7.1: "Motor activity, behavioral changes, coordination, sensory/motor reflex responses and body temperature should be evaluated. For example, a functional observation battery (FOB), modified Irwin's, or other appropriate test can be used." OECD TG 424 (1997) covers neurobehavioural effects in rodents |
| Supervised software needs hand-labeled examples | MARS: "We trained classifiers on 6.95 hr of video annotated by a single individual" (Results). DeepEthogram (Bohnslav et al. 2021, eLife, doi:10.7554/eLife.63377): >90% accuracy "with only 80 example frames of a given behavior". "Thousands of frames" is not claimed |
| SAM follows both mice through mounts | docs/SPIKE.md; DECISIONS 2026-10-28 chunk-length table (100% two-animal coverage during mount at 60 s chunks) |
| 0.58 F1; 0.79-0.86; per behavior; speed; $/h | eval/results/test1_report.md §11.1, §11.9 (Sun et al. 2021, CalMS21, arXiv:2104.02710, Tables 1, 2, 8) |
| "about its own length" | test1_report §11.3 (0.88× pooled, worst 1.54×) |
| Fly definitions from the literature | configs/flies_definitions.json (Chen et al. 2002, PNAS, Table 1) |

## Production
- **Live run:** a test video with clear attack and mount (⟨clip⟩, chosen by the evaluator, all 19 per-video
  results published so the choice is transparent). Recorded live with a visible clock. If it fails on the day,
  use the precomputed run, labeled "precomputed" on screen.
- **Human annotation timeline:** the real CalMS21 labels for the same clip, rendered like Etho's ethogram (no
  mock spreadsheet).
- **Before export:**
  - confirm the maximum length in the Official Rules (Aniket has the participants page);
  - make sure a LICENSE file exists (proposed: Apache-2.0, needs Aniket's decision);
  - every on-screen number matches a repo file;
  - "training-free" always comes with the footnote;
  - nothing says "beats supervised" or "N× faster";
  - the per-behavior F1s appear next to 0.58.
