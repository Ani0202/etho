# Offline replay sample (`make demo`)

| File | What it is |
|---|---|
| `calms21_dev_60s.mp4` | A 60 s clip (270 s to 330 s of `mouse008_task1_annotator1`, re-encoded without audio) from the CalMS21 **development** split. Nothing from the test split is in this directory. |
| `clip.json` | The clip's provenance (sequence, split, source offset, SHA-256, license). |
| `replay.jsonl.gz` | The replay index: 56 recorded Meta model responses (Muse Spark, SAM 3.1, Muse Spark Open) from one real run of the web app on this clip, each under a semantic key (`etho/replay/keys.py`), with the cost it was billed at the time. No API keys, no request payload bytes (images and videos are placeholders). |
| `expected.json` | What that live run produced (bouts, metrics, label hashes). `make demo-test` checks the replay reproduces it exactly. |

## Credit and license

Clip: **CalMS21, Sun et al. 2021**, licensed **CC BY 4.0**.

- Dataset: <https://data.caltech.edu/records/s0vdx-0k302>
- Paper: Sun, J. J. et al., "The Multi-Agent Behavior Dataset: Mouse Dyadic Social Interactions", NeurIPS Datasets and Benchmarks 2021 (arXiv:2104.02710)
- License: <https://creativecommons.org/licenses/by/4.0/>

The clip was trimmed and re-encoded by this project; the annotations are not included.
The recorded model responses in `replay.jsonl.gz` are Etho's own outputs on that clip.

## Regenerating the index

The index is built from a finished app run by re-running the same job through a recorder that answers from that run's
gateway cache and refuses any cache miss before it can reach the network, so it costs nothing:

```bash
uv run python -m etho.replay.record --cache <run's gateway cache> --job-dir <run's job dir> \
    --ledger ops/spend.jsonl --clip demo/sample/calms21_dev_60s.mp4 \
    --out demo/sample/replay.jsonl.gz --expected demo/sample/expected.json
```
