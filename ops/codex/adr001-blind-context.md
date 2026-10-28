# Decision needed: how Etho's tracking stage gets per-animal identities from SAM 3.1

Context: Etho scores mouse social behavior (CalMS21: black resident + white intruder, grayscale
top-down video 1024x570, 30 fps, subsampled to 10 fps) from SAM 3.1 masks via Meta's hosted API
($0.20 per 1k video frames, min 50 per request, up to ~15k frames/request (unverified), concept
prompts are short noun phrases; box/point prompts also exist with propagation). Budget for SAM in
eval: $40. Test set: 19 videos, 87,374 frames at 10 fps; dev: 12,329; dev-holdout: 9,639.

Options:
A. One generic concept call per video ("mouse"); keep the two most persistent tracks inside an
   arena ROI; label tracks resident/intruder by mean mask intensity; bridge short gaps.
B. One concept call per animal with attribute phrases ("black mouse", "white mouse").
C. A or B in overlapping chunks (e.g. 60 s with 1 s overlap), stitching identity across chunks.
D. Self-host open SAM 3.1 weights on a rented GPU (~$8/hr H100; needs owner approval).
E. Hybrid: A, plus a second SAM call with a box/point object prompt for the intruder seeded at the
   last frame it was seen, to recover it through occlusions (untested).

Evidence (spike, 2 train clips x 200 frames; see docs/SPIKE.md and data/spike/out/*.json):
- "mouse": clip A (investigation + a 2.7 s mount) object 0 = resident in 200/200 frames, object 1 =
  intruder in 160/200; the 40 missing intruder frames are clip frames 0-39 and include all 27 mount
  frames (intruder hidden under the resident). Clip B (attack): resident 196/200, intruder 190/200,
  4 intruder frames nearer the resident's reference centroid; two spurious objects (cage-wall
  reflection, frame edge) in 33 and 16 frames. Mean mask intensity ~51 (black) vs ~143 (white).
  Identity scored by nearest MARS-pose-estimator keypoint centroid, majority per track (not a
  temporal swap count).
- "black mouse"/"white mouse": clip B returned nothing for all 200 frames; clip A only frames
  154-199. The image endpoint did find "black mouse" on clip A frame 0.
- Cost at 10 fps for dev x2 + holdout + test: one phrase $24.34, two phrases $48.67.
- Latency: 7-21 s per 200-frame request; mask decode (pure Python) ~0.35 s/mask serially.
- Long videos (minutes) have not been tested at all.
