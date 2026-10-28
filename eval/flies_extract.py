"""Caltech Fly vs. Fly (Aggression) label and ground-truth track loading (docs/EVAL_PLAN.md section 14).

Dataset: Fly vs. Fly, Aggression subset, CaltechDATA zrznw-w7386, DOI 10.22002/D1.1893, CC0.
Paper: Eyjolfsdottir et al., "Detecting Social Actions of Fruit Flies", ECCV 2014.
Only eval/ code reads these labels and tracks. Product code (etho/) must never import this module.

File format (learned from train movie1-5; test movies 6-10 have NOT been opened, see "Test seal" below)
- `movieN_actions.mat`: `behs` 1x6 cell = lunge, wing_threat, charge, hold, tussle, unknown;
  `bouts` 2x6 cell, row = fly (0, 1), column = behavior. Each cell is an [n, 3] array:
  `[start, end, flag]`. `start`/`end` are **MATLAB 1-based, inclusive** frame numbers: python frames
  `[start - 1, end)` (half-open). Evidence (train movies 1-5, measured on the GT track, whose row i is decoded
  video frame i, motion cross-correlation peak at lag 0 on movies 1-2): wing-angle onset is at python frame
  start-1 in 50 of 63 wing-threat bouts, the last wings-up frame is python end-1 in the modal case (24 of 61),
  and the lunge's peak displacement lands on python frame end-1 (897 of 1,530 bouts). `charge` has bouts with
  end == start (1 frame), which only makes sense if `end` is inclusive.
  `flag` (third column) is 0 or 2 on train; its meaning is undocumented. It is recorded and ignored (the bout
  counts as a positive either way).
  `tussle` is annotated identically on both flies (a pair action).
- `unknown` is a per-fly "annotator could not tell" label. Pair-level and per-fly scoring **exclude** every
  frame where either fly is `unknown` (one exclusion mask per movie window), even when a scored behavior is
  also annotated there (train: 0-55 such frames per full movie, 0 in the dev windows).
- `movieN_track.mat`: struct `trk` with `data` [2 flies, 54000, 17], `names` (pos x, pos y, ori, major axis len,
  minor axis len, wing l/r x/y, body area, fg area, img contrast, min fg dist, wing l/r ang, wing l/r len) and
  `flag_frames` [1, 54000]. Positions are pixels in the 144x144 frame. Fly index f in `trk.data` is fly f in
  `bouts` (same tool chain; assumed, not independently verified).
- `traintest.mat`: `trainids` = 1..5, `testids` = 6..10, `n_flies` = 2, same `behs` without `unknown`.
- movie6 also has `movie6_second_actions.mat` (a second annotation of a test movie, presumably a second
  annotator; not opened). It is read only in the test run, for an inter-annotator reference.

Windows (ADR-002, fixed by rule before any test label is read), native 30 fps python frames, half-open:
- `mid5`  = [22500, 31500): middle 5 min, lunge-go case (30 fps tracking, both behaviors).
- `mid10` = [18000, 36000): middle 10 min, wing_threat-only case (10 fps tracking).

Test seal: every loader refuses test movies (6-10) unless called with `allow_test=True`, which only
`eval/flies_score.py` does, after its test-run guard and after the predictions hash is logged.
sha256 hashing (`file_manifest`) reads bytes only and does not parse anything.

CLI (train only, $0, no network):
    uv run python -m eval.flies_extract summary   # train-window counts + random references -> eval/results/
    uv run python -m eval.flies_extract manifest  # sha256 of every file used -> eval/flies_manifest.json
    uv run python -m eval.flies_extract verify    # re-hash local files against the manifest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from eval import common

DATA_ROOT = Path("data/flyvsfly/Aggression")
ARCHIVE = Path("data/flyvsfly/Aggression.zip")
MANIFEST = Path("eval/flies_manifest.json")
SUMMARY_OUT = Path("eval/results/flies_train_summary.json")
DOI = "10.22002/D1.1893"
RECORD = "https://data.caltech.edu/records/zrznw-w7386"

NATIVE_FPS = 30
N_FRAMES = 54000
N_FLIES = 2
ACTION_BEHS = ("lunge", "wing_threat", "charge", "hold", "tussle", "unknown")
UNKNOWN = "unknown"
SCORED = ("wing_threat", "lunge")

TRAIN_MOVIES = (1, 2, 3, 4, 5)
TEST_MOVIES = (6, 7, 8, 9, 10)
SPLITS = {"dev": (1, 2), "spare": (3, 4, 5), "train": TRAIN_MOVIES, "test": TEST_MOVIES}
WINDOWS = {"mid5": (22500, 31500), "mid10": (18000, 36000)}
TRACK_FIELDS = ("pos x", "pos y", "ori", "major axis len", "minor axis len", "wing l x", "wing l y", "wing r x",
                "wing r y", "body area", "fg area", "img contrast", "min fg dist", "wing l ang", "wing r ang",
                "wing l len", "wing r len")


class TestSealed(PermissionError):
    """Raised when a test movie's labels/tracks/features are requested outside the guarded test run."""


def _guard(movie: int, allow_test: bool) -> None:
    if movie in TEST_MOVIES and not allow_test:
        raise TestSealed(f"movie{movie} is a test movie: its labels, tracks and features are sealed until the "
                         "flies test run (eval/flies_score.py --split test)")
    if movie not in TRAIN_MOVIES and movie not in TEST_MOVIES:
        raise ValueError(f"movie{movie} is not in the official split (1-10)")


def split_movies(split: str) -> tuple[int, ...]:
    try:
        return SPLITS[split]
    except KeyError:
        raise SystemExit(f"unknown flies split {split!r}; use one of {sorted(SPLITS)}") from None


def movie_paths(movie: int, root: Path = DATA_ROOT) -> dict[str, Path]:
    d = Path(root) / f"movie{movie}"
    p = {"video": d / f"movie{movie}.mp4", "actions": d / f"movie{movie}_actions.mat",
         "feat": d / f"movie{movie}_feat.mat", "track": d / f"movie{movie}_track.mat"}
    second = d / f"movie{movie}_second_actions.mat"
    if second.exists():
        p["second_actions"] = second
    return p


def _loadmat(path: Path) -> dict:
    import scipy.io as sio  # local import: tests that never touch .mat files don't need scipy

    return sio.loadmat(str(path))


def _cellstr(cell) -> list[str]:
    return [str(np.asarray(c).ravel()[0]) for c in np.asarray(cell).ravel()]


# --------------------------------------------------------------------------- split file

def read_traintest(root: Path = DATA_ROOT) -> dict:
    d = _loadmat(Path(root) / "traintest.mat")
    return {"train": [int(x) for x in np.asarray(d["trainids"]).ravel()],
            "test": [int(x) for x in np.asarray(d["testids"]).ravel()],
            "n_flies": int(np.asarray(d["n_flies"]).ravel()[0]),
            "behs": _cellstr(d["behs"]), "movienames": _cellstr(d["movienames"])}


def verify_split(root: Path = DATA_ROOT) -> dict:
    """The hard-coded split must equal traintest.mat (which holds only ids and names, no labels)."""
    tt = read_traintest(root)
    if tuple(tt["train"]) != TRAIN_MOVIES or tuple(tt["test"]) != TEST_MOVIES or tt["n_flies"] != N_FLIES:
        raise ValueError(f"traintest.mat split {tt['train']} / {tt['test']} (n_flies {tt['n_flies']}) != "
                         f"{TRAIN_MOVIES} / {TEST_MOVIES}")
    return tt


# --------------------------------------------------------------------------- actions

def parse_actions(d: dict, n_frames: int = N_FRAMES, name: str = "") -> dict:
    """Parse a loaded actions.mat dict. Returns {"behs", "bouts": {beh: [arrays per fly, python half-open
    [n, 2] int64]}, "flags": {beh: [per-fly flag arrays]}}. Raw MATLAB [s, e] inclusive -> [s - 1, e)."""
    behs = _cellstr(d["behs"])
    cells = np.asarray(d["bouts"], dtype=object)
    if cells.ndim != 2 or cells.shape[0] != N_FLIES or cells.shape[1] != len(behs):
        raise ValueError(f"{name}: bouts cell shape {cells.shape}, expected ({N_FLIES}, {len(behs)})")
    bouts, flags = {}, {}
    for j, b in enumerate(behs):
        bouts[b], flags[b] = [], []
        for f in range(N_FLIES):
            a = np.asarray(cells[f, j])
            if a.size == 0:
                bouts[b].append(np.zeros((0, 2), np.int64))
                flags[b].append(np.zeros(0, np.int64))
                continue
            a = a.astype(np.int64).reshape(-1, a.shape[-1])
            if a.shape[1] < 2:
                raise ValueError(f"{name}: {b} fly {f}: bout rows have {a.shape[1]} columns, need >= 2")
            s, e = a[:, 0], a[:, 1]
            if (s < 1).any() or (e < s).any() or (e > n_frames).any():
                bad = a[(s < 1) | (e < s) | (e > n_frames)][:3].tolist()
                raise ValueError(f"{name}: {b} fly {f}: invalid 1-based inclusive bouts {bad} (n_frames {n_frames})")
            bouts[b].append(np.stack([s - 1, e], axis=1))
            flags[b].append(a[:, 2] if a.shape[1] > 2 else np.zeros(len(a), np.int64))
    return {"behs": behs, "bouts": bouts, "flags": flags}


def masks_from_actions(actions: dict, n_frames: int = N_FRAMES) -> dict[str, np.ndarray]:
    """{beh: bool[N_FLIES, n_frames]} for every behavior in the file (unknown included)."""
    out = {}
    for b, per_fly in actions["bouts"].items():
        m = np.zeros((N_FLIES, n_frames), dtype=bool)
        for f, arr in enumerate(per_fly):
            for s, e in arr:
                m[f, s:e] = True
        out[b] = m
    return out


def window_labels(actions: dict, window: tuple[int, int], n_frames: int = N_FRAMES,
                  behaviors=SCORED) -> dict:
    """Labels for one movie window at native 30 fps.

    Returns {"window": (s, e), "per_fly": {beh: bool[2, W]}, "pair": {beh: bool[W]} (union of both flies),
    "excluded": bool[W] (either fly `unknown`), "behs_in_file": [...]}."""
    s, e = window
    if not 0 <= s < e <= n_frames:
        raise ValueError(f"window {window} outside [0, {n_frames})")
    m = masks_from_actions(actions, n_frames)
    missing = [b for b in behaviors if b not in m]
    if missing:
        raise ValueError(f"behaviors {missing} not in actions file ({actions['behs']})")
    per_fly = {b: m[b][:, s:e].copy() for b in behaviors}
    unk = m[UNKNOWN][:, s:e].any(axis=0) if UNKNOWN in m else np.zeros(e - s, dtype=bool)
    return {"window": (s, e), "per_fly": per_fly, "pair": {b: per_fly[b].any(axis=0) for b in behaviors},
            "excluded": unk, "behs_in_file": list(actions["behs"])}


def load_actions(movie: int, root: Path = DATA_ROOT, allow_test: bool = False, second: bool = False) -> dict:
    _guard(movie, allow_test)
    p = movie_paths(movie, root)
    key = "second_actions" if second else "actions"
    if key not in p:
        raise FileNotFoundError(f"movie{movie}: no {key} file")
    return parse_actions(_loadmat(p[key]), N_FRAMES, p[key].name)


def load_window_labels(movie: int, window: str | tuple[int, int], root: Path = DATA_ROOT,
                       allow_test: bool = False, behaviors=SCORED, second: bool = False) -> dict:
    w = WINDOWS[window] if isinstance(window, str) else tuple(window)
    return window_labels(load_actions(movie, root, allow_test, second), w, N_FRAMES, behaviors)


# --------------------------------------------------------------------------- ground-truth tracks (eval only)

def parse_track(d: dict, name: str = "") -> dict:
    trk = d["trk"][0, 0]
    names = _cellstr(trk["names"])
    data = np.asarray(trk["data"], dtype=np.float64)
    if data.ndim != 3 or data.shape[0] != N_FLIES or data.shape[2] != len(names):
        raise ValueError(f"{name}: trk.data shape {data.shape} vs {len(names)} names")
    col = {n: i for i, n in enumerate(names)}
    for need in ("pos x", "pos y", "major axis len"):
        if need not in col:
            raise ValueError(f"{name}: track field {need!r} missing ({names})")
    return {"names": names, "data": data, "x": data[:, :, col["pos x"]], "y": data[:, :, col["pos y"]],
            "major_axis": data[:, :, col["major axis len"]],
            "flag_frames": np.asarray(trk["flag_frames"]).ravel().astype(bool)}


def load_gt_tracks(movie: int, root: Path = DATA_ROOT, allow_test: bool = False) -> dict:
    """GT tracker output (Eyjolfsdottir et al. tracker): row i of `x`/`y` [2, 54000] is native frame i, pixels in
    the 144x144 frame. Used only for the identity metrics; product code never reads it (ADR-002 c)."""
    _guard(movie, allow_test)
    p = movie_paths(movie, root)["track"]
    return parse_track(_loadmat(p), p.name)


# --------------------------------------------------------------------------- summaries

def runs(mask: np.ndarray) -> np.ndarray:
    """Maximal runs of True as [n, 2] (start, end exclusive)."""
    d = np.diff(np.concatenate([[0], np.asarray(mask, dtype=np.int8), [0]]))
    return np.stack([np.flatnonzero(d == 1), np.flatnonzero(d == -1)], axis=1)


def bout_stats(mask: np.ndarray, fps: int = NATIVE_FPS) -> dict:
    r = runs(mask)
    L = r[:, 1] - r[:, 0] if len(r) else np.zeros(0, np.int64)
    return {"n_bouts": int(len(r)), "frames": int(mask.sum()),
            "dur_frames_min": int(L.min()) if len(L) else None,
            "dur_frames_median": float(np.median(L)) if len(L) else None,
            "dur_frames_max": int(L.max()) if len(L) else None,
            "dur_s_mean": float(L.mean() / fps) if len(L) else None,
            "n_bouts_le_3_frames": int((L <= 3).sum())}


def random_reference(gt_by_movie: dict, keep_by_movie: dict, reps: int = 200, seed: int = common.SEED) -> dict:
    """Prevalence-matched random references for one behavior over a set of movie windows.

    - iid: each kept frame is positive with probability p = pooled prevalence of the kept frames. Expected
      precision = recall = F1 = p (analytic); `iid_sim_mean_f1` checks it by simulation.
    - bout_shuffle: each movie's GT bouts (same count and durations) are placed at uniform random starts in
      its window; mean pooled frame F1 and mean one-to-one any-overlap bout F1 over `reps` draws. This is the
      reference for bout-level metrics (an iid predictor fragments into 1-frame bouts and is not one)."""
    from eval import flies_score  # metrics live with the scorer

    rng = np.random.default_rng(seed)
    movies = list(gt_by_movie)
    kept = np.concatenate([gt_by_movie[m][keep_by_movie[m]] for m in movies])
    p = float(kept.mean()) if len(kept) else float("nan")
    sim = []
    for _ in range(reps):
        pr = rng.random(len(kept)) < p
        sim.append(flies_score.prf(int((kept & pr).sum()), int((~kept & pr).sum()), int((kept & ~pr).sum()))[2])
    fr, bt = [], []
    for _ in range(reps):
        c = np.zeros(3, np.int64)
        b = np.zeros(3, np.int64)
        for m in movies:
            g, keep = gt_by_movie[m], keep_by_movie[m]
            W = len(g)
            pr = np.zeros(W, bool)
            for s, e in runs(g):
                d = e - s
                st = int(rng.integers(0, W - d + 1))
                pr[st:st + d] = True
            gk, pk = g[keep], pr[keep]
            c += (np.sum(gk & pk), np.sum(~gk & pk), np.sum(gk & ~pk))
            gb, pb = g & keep, pr & keep
            b += flies_score.bout_counts_any_overlap(gb, pb)
        fr.append(flies_score.prf(*c)[2])
        n_gt, n_pr, mt = b
        bp, br = (mt / n_pr if n_pr else 0.0), (mt / n_gt if n_gt else 0.0)
        bt.append(2 * bp * br / (bp + br) if bp + br else 0.0)
    return {"n_frames_kept": int(len(kept)), "prevalence": p, "iid_expected_f1": p,
            "iid_sim_mean_f1": float(np.mean(sim)) if sim else None,
            "bout_shuffle_mean_frame_f1": float(np.mean(fr)), "bout_shuffle_mean_bout_f1": float(np.mean(bt)),
            "bout_shuffle_bout_f1_p95": float(np.percentile(bt, 95)), "reps": reps, "seed": seed}


def train_summary(root: Path = DATA_ROOT, reps: int = 200) -> dict:
    """Train movies 1-5 only: per-window bout counts/durations, other actions, unknown frames, and random refs."""
    tt = verify_split(root)
    out = {"dataset": "Fly vs. Fly Aggression", "doi": DOI, "split_from_traintest_mat": tt,
           "index_convention": "MATLAB 1-based inclusive [s, e] -> python [s-1, e)", "windows": {}}
    acts = {m: load_actions(m, root) for m in TRAIN_MOVIES}
    out["behs_in_actions_files"] = {f"movie{m}": a["behs"] for m, a in acts.items()}
    out["flag_column_values"] = {f"movie{m}": {b: sorted({int(x) for fl in a["flags"][b] for x in fl})
                                               for b in a["behs"]} for m, a in acts.items()}
    for wname, w in WINDOWS.items():
        wd = {"frames": list(w), "per_movie": {}, "pooled": {}, "random_reference": {}}
        for split in ("dev", "spare", "train"):
            wd["random_reference"][split] = {}
        labs = {m: window_labels(acts[m], w, N_FRAMES, ACTION_BEHS[:-1]) for m in TRAIN_MOVIES}
        for m, L in labs.items():
            pm = {"excluded_unknown_frames": int(L["excluded"].sum())}
            for b in ACTION_BEHS[:-1]:
                pm[b] = {"pair": bout_stats(L["pair"][b]),
                         "fly0": bout_stats(L["per_fly"][b][0]), "fly1": bout_stats(L["per_fly"][b][1]),
                         "pos_frames_in_excluded": int((L["pair"][b] & L["excluded"]).sum())}
            wd["per_movie"][f"movie{m}"] = pm
        for b in ACTION_BEHS[:-1]:
            n_b = sum(wd["per_movie"][f"movie{m}"][b]["pair"]["n_bouts"] for m in TRAIN_MOVIES)
            fr = sum(wd["per_movie"][f"movie{m}"][b]["pair"]["frames"] for m in TRAIN_MOVIES)
            with_b = [m for m in TRAIN_MOVIES if wd["per_movie"][f"movie{m}"][b]["pair"]["n_bouts"]]
            wd["pooled"][b] = {"pair_bouts": n_b, "pair_frames": fr, "movies_with_behavior": with_b}
        for split in ("dev", "spare", "train"):
            ms = SPLITS[split]
            for b in SCORED:
                wd["random_reference"][split][b] = random_reference(
                    {m: labs[m]["pair"][b] for m in ms}, {m: ~labs[m]["excluded"] for m in ms}, reps)
        out["windows"][wname] = wd
    return out


# --------------------------------------------------------------------------- manifest

def file_manifest(root: Path = DATA_ROOT, archive: Path = ARCHIVE) -> dict:
    """sha256 of every file used (bytes only; nothing is parsed, so test files stay sealed)."""
    files = {}
    for m in TRAIN_MOVIES + TEST_MOVIES:
        for kind, p in movie_paths(m, root).items():
            files[str(p)] = {"movie": m, "kind": kind, "split": "train" if m in TRAIN_MOVIES else "test",
                             "sha256": common.sha256_file(p)}
    tt = Path(root) / "traintest.mat"
    files[str(tt)] = {"kind": "split", "sha256": common.sha256_file(tt)}
    return {
        "dataset": "Caltech Fly vs. Fly, Aggression subset", "record": RECORD, "doi": DOI, "license": "CC0 1.0",
        "paper": "Eyjolfsdottir, Branson, Burgos-Artizzu, Hoopfer, Schor, Anderson, Perona. Detecting Social "
                 "Actions of Fruit Flies. ECCV 2014, LNCS 8690, doi:10.1007/978-3-319-10605-2_50",
        "archive": {"path": str(archive), "sha256": common.sha256_file(archive) if Path(archive).exists() else None},
        "split": {"source": "traintest.mat (trainids / testids)", "train": list(TRAIN_MOVIES),
                  "test": list(TEST_MOVIES), "dev": list(SPLITS["dev"]), "spare_dev": list(SPLITS["spare"])},
        "native": {"fps": NATIVE_FPS, "frames_per_movie": N_FRAMES, "resolution": [144, 144]},
        "windows": {k: {"frames_half_open": list(v), "minutes": (v[1] - v[0]) / NATIVE_FPS / 60}
                    for k, v in WINDOWS.items()},
        "index_convention": "actions.mat bouts are MATLAB 1-based inclusive [s, e]; python frames [s-1, e)",
        "scored_behaviors": list(SCORED), "excluded_label": UNKNOWN,
        "test_seal": "test-movie actions/track/feat/second_actions were hashed (bytes) but not parsed before the "
                     "flies test run",
        "not_in_git": "raw video, .mat files and frames are never committed (data/ is gitignored)",
        "files": files,
    }


def verify_manifest(manifest_path: Path = MANIFEST) -> list[str]:
    man = json.loads(Path(manifest_path).read_text())
    bad = []
    for p, info in man["files"].items():
        actual = common.sha256_file(Path(p)) if Path(p).exists() else None
        if actual != info["sha256"]:
            bad.append(f"{p}: manifest {info['sha256'][:12]}, local {actual and actual[:12]}")
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("summary", "manifest", "verify"))
    ap.add_argument("--root", type=Path, default=DATA_ROOT)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--reps", type=int, default=200)
    a = ap.parse_args(argv)
    if a.cmd == "manifest":
        out = a.out or MANIFEST
        out.write_text(json.dumps(file_manifest(a.root), indent=1) + "\n")
        print(f"wrote {out}")
        return 0
    if a.cmd == "verify":
        bad = verify_manifest(a.out or MANIFEST)
        print("\n".join(bad) if bad else "all files match the manifest")
        return 1 if bad else 0
    out = a.out or SUMMARY_OUT
    summ = train_summary(a.root, a.reps)
    summ["generated_utc"] = common.now_utc()
    summ.update({k: v for k, v in common.git_info().items() if k != "git_dirty_paths"})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summ, indent=1) + "\n")
    common.append_run({"run_id": common.new_run_id("flies-train-summary"), "kind": "flies-train-summary",
                       "dataset": "flyvsfly", "split": "flies-train", "purpose": "plan", "status": "ok",
                       "time_utc": common.now_utc(), **common.git_info(), "cost": {"usd": 0.0},
                       "labels_loaded": True, "movies": list(TRAIN_MOVIES), "output": str(out),
                       "output_sha256": common.sha256_file(out), "reps": a.reps})
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
