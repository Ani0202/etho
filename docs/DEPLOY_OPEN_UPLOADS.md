# Deploying open live uploads (branch `open-uploads`, written Nov 4 for the afternoon deploy)

What it is: `ETHO_APP_OPEN=1` makes uploads and revisions on the hosted app work with **no access code and no login**.
Everything else about the hosted app is as in `docs/HOSTING.md`. The default is unchanged: a public app with no
usable code file and without `ETHO_APP_OPEN` stays read-only.

What stays in force in open mode: the kill switch (`ops/app-kill`), the read-only file (`ops/app-readonly`), the
live-until date (Nov 10 23:59:59 ET), the bubblewrap requirement, the 24 h deletion, the privacy notice, the showcase
(open to everyone, zero spend), the sample-clip download on the home page.

## 0. How it behaves (so you can check it against what you see)

| Item | Value |
|---|---|
| Who may upload | anyone, no code; the upload form must carry `consent=yes`, else HTTP 400 `consent_required` (checked on the server before the file is read) |
| Consent text on the page | "I have the right to upload this video, and it contains no people" |
| Per visitor | 10 runs per rolling 24 h per client IP. A run is an analysis job; a revision is a run too (each one counts as one; a showcase revision too). A rejected upload (bad file, no consent, cap, queue full) is not a run |
| Jobs at once | at most 2 queued or running (`ETHO_APP_MAX_IN_FLIGHT`); the worker runs **one** job at a time, so the second waits with a visible position ("Queued, position 1") |
| Upload | mp4, mov, avi; 50 MB; 120 s; ffprobe check before anything is spent |
| Daily cap | $8 per rolling 24 h before Nov 4 ET, **$20 inside Nov 4-9 ET** (judging window), $8 again on Nov 10 |
| Total cap | $60 over all time, counting only this instance's own ledger rows (`pub:job:`) |
| When a limit is hit | a plain message that points to the showcase on the site and to `make demo` plus the README, and says when it resets: tomorrow for the daily and per-visitor limits, "live use is over for this demo" for the total cap, "a few minutes" for a full queue |

Per-visitor counters are in memory: a restart of the app resets them (the daily and total caps come from the ledger and
survive restarts). Per-visitor keys are the client IP as uvicorn sees it (`--forwarded-allow-ips 127.0.0.1` makes it
the real public IP through Funnel; an IPv6 visitor is keyed on the /64).

## 1. The `app.env` lines (Aniket)

Nothing is removed except, only if it is there, the one line named in 1.3. The block in 1.2 sets each key in place if it
already exists and appends it if it does not; it never deletes a line. It refuses a value with odd characters.

### 1.1 Look at what is there now (changes nothing)
```
sudo grep -E '^ETHO_APP_(OPEN|RATE_JOBS|RATE_WINDOW_S|DAILY_CAP_USD|JUDGING_DAILY_CAP_USD|TOTAL_CAP_USD|MAX_UPLOAD_MB|MAX_DURATION_S|MAX_IN_FLIGHT|READ_ONLY|LIVE_UNTIL)=' /etc/etho-app/app.env; ls -l /etc/etho-app/app.env
```

### 1.2 Set the lines (paste the whole block)
```
set_env() {
  f=/etc/etho-app/app.env
  case "$2" in ''|*[!0-9A-Za-z._:+-]*) echo "refusing odd value for $1: $2"; return 1;; esac
  if sudo grep -q "^$1=" "$f"; then sudo sed -i "s|^$1=.*|$1=$2|" "$f"; else echo "$1=$2" | sudo tee -a "$f" >/dev/null; fi
}
set_env ETHO_APP_OPEN 1
set_env ETHO_APP_RATE_JOBS 10
set_env ETHO_APP_RATE_WINDOW_S 86400
set_env ETHO_APP_DAILY_CAP_USD 8
set_env ETHO_APP_JUDGING_DAILY_CAP_USD 20
set_env ETHO_APP_TOTAL_CAP_USD 60
set_env ETHO_APP_MAX_UPLOAD_MB 50
set_env ETHO_APP_MAX_DURATION_S 120
set_env ETHO_APP_MAX_IN_FLIGHT 2
unset -f set_env
```
The lines this leaves in the file, exactly:
```
ETHO_APP_OPEN=1
ETHO_APP_RATE_JOBS=10
ETHO_APP_RATE_WINDOW_S=86400
ETHO_APP_DAILY_CAP_USD=8
ETHO_APP_JUDGING_DAILY_CAP_USD=20
ETHO_APP_TOTAL_CAP_USD=60
ETHO_APP_MAX_UPLOAD_MB=50
ETHO_APP_MAX_DURATION_S=120
ETHO_APP_MAX_IN_FLIGHT=2
```
Notes. `ETHO_APP_TOTAL_CAP_USD` was 30. `ETHO_APP_DAILY_CAP_USD` is set on purpose together with
`ETHO_APP_JUDGING_DAILY_CAP_USD`: an explicit daily cap is never raised in the judging window unless the judging value is
set too. `ETHO_APP_RATE_JOBS`, `_RATE_WINDOW_S` and `_MAX_IN_FLIGHT` are also the open-mode defaults; they are written out
so that what runs is visible. Leave `ETHO_APP_ACCESS_CODE_FILE` and `ETHO_APP_LIVE_UNTIL` as they are (the code file is
then only the operator's key for the admin health view; it is not asked of visitors).

### 1.3 Check, and the one possible removal
```
sudo grep -E '^ETHO_APP_(OPEN|RATE_JOBS|RATE_WINDOW_S|DAILY_CAP_USD|JUDGING_DAILY_CAP_USD|TOTAL_CAP_USD|MAX_UPLOAD_MB|MAX_DURATION_S|MAX_IN_FLIGHT|READ_ONLY|LIVE_UNTIL)=' /etc/etho-app/app.env; ls -l /etc/etho-app/app.env
```
Expect each key once, the nine values above, `ETHO_APP_LIVE_UNTIL=2026-11-10T23:59:59-05:00`, and the file still
`-rw-r----- 1 root etho-app`. If `ETHO_APP_READ_ONLY=1` is listed, the site stays read-only whatever else is set. Only
then, run this and nothing else (it is the one line this deploy may delete; HOSTING.md section 7C):

**WARNING: this deletes a line from the config file. Run it on its own.**
```
sudo sed -i '/^ETHO_APP_READ_ONLY=/d' /etc/etho-app/app.env
```

## 2. Merge and restart (lead)

The service runs the code in `/home/ubuntu/work/etho`, so: merge `open-uploads` into `main` there (after the reviewer
pass), then, with Aniket notified first (Nov 4-9 ET rule, `docs/HOSTING.md` section 14):
```
sudo systemctl restart etho-app
```
While `ops/app-readonly` still exists the site is open-mode but read-only; nothing can spend. Checks:
```
systemctl is-active etho-app
curl -s http://127.0.0.1:8000/api/health | jq .
curl -s http://127.0.0.1:8000/api/config | jq '.limits, .cap, .live'
sudo sh -c 'curl -s -H "X-Etho-Admin: $(cat /etc/etho-app/access_code)" http://127.0.0.1:8000/api/health' | jq '{mode, open, reasons, sandbox, caps, queue, client_seen_as, shared_client_bucket}'
```
Expect: `active`; health `"open": true` and `"mode": "read-only"`; `limits`: `rate_limit_jobs` 10, `rate_window_s` 86400,
`max_in_flight` 2, `max_upload_mb` 50, `max_duration_s` 120, `consent_required` true; `cap.cap_usd` 20 (Nov 4-9 ET) and
`total_cap_usd` 60; admin view: `reasons` is `["the read-only flag file is present"]`, `sandbox.mode` is `bwrap`.
The public view must not show `reasons`, `sandbox`, `client_seen_as` (open mode is not an operator view):
```
curl -s https://$(sed 's|https://||' /home/ubuntu/work/etho/ops/public-url)/api/health | jq 'keys'
```
Expect only `caps`, `live_until`, `mode`, `ok`, `open`.

## 3. Open the site (own block, reversible)

This is the moment uploads become public. Run the drills straight after, and put the file back if one fails.
```
rm /home/ubuntu/work/etho/ops/app-readonly
```
Undo, instantly, no restart: `touch /home/ubuntu/work/etho/ops/app-readonly`. The kill switch is
`touch /home/ubuntu/work/etho/ops/app-kill`.

## 4. Drills through the public URL (lead; about $0.4 to $0.6 expected, under $1)

All requests go to the public IP with `--resolve`, so they take the same path as a judge (Funnel), not the tailnet. The
host is the one in `ops/public-url`. Setup (creates test files in a temp dir; nothing in the repo):
```
HOST=$(sed 's|https://||' /home/ubuntu/work/etho/ops/public-url); IP=199.38.181.54
BASE=https://$HOST; RS="--resolve $HOST:443:$IP"; CONSENT="-F consent=yes"
W=$(mktemp -d); echo "$W"
ffmpeg -y -loglevel error -f lavfi -i testsrc=size=640x480:rate=10 -t 20 -pix_fmt yuv420p $W/noanimal.mp4
ffmpeg -y -loglevel error -f lavfi -i testsrc=size=320x240:rate=10 -t 2 -pix_fmt yuv420p $W/tiny.mp4
printf 'not a video' > $W/fake.mp4; echo hello > $W/notes.txt
curl -sS $RS $BASE/api/sample/clip -o $W/sample.mp4 && ls -l $W/sample.mp4
curl -sS $RS $BASE/api/sample | jq -c .definitions > $W/sample_defs.json
echo '{"investigation":"The resident mouse is close to the other mouse and sniffing it."}' > $W/one_def.json
up() { curl -sS $RS -w '\nHTTP %{http_code}\n' -F "video=@$1" -F "definitions=<$2" -F "subject=the resident" -F "partner=the other animal" $3 $BASE/api/jobs; }
job() { curl -sS $RS $BASE/api/jobs/$1 | jq -c '{status, stage, queue_position, cost_usd, error_code, friendly}'; }
adm() { sudo sh -c 'curl -s -H "X-Etho-Admin: $(cat /etc/etho-app/access_code)" http://127.0.0.1:8000/api/health' | jq -c '.caps | {spent_24h_usd, spent_total_usd, reserved_usd}'; }
adm
```
Expect the sample download to be about 60 s of mp4 (the size the home page shows) and `adm` to print the spend so far
(note it; it is the "before" figure). Also open `$BASE` on a phone once: the "Download the sample clip" button must be
visible without any code, the upload form shows the consent box above "Score video", and no "Unlock" card.

**Drill 1. Upload of the sample clip.** About **$0.29** (measured on the Nov 3 recording run of this clip, 109 s; the
reserved ceiling is $0.89 and the job cannot spend past it).
```
R1=$(up $W/sample.mp4 $W/sample_defs.json "$CONSENT"); echo "$R1"; J1=$(echo "$R1" | head -1 | jq -r .id); job $J1
```
Expect `HTTP 200`, a body with `"id":"j..."`, `"url":"/p/j..."`, `"est_cost_usd"` about 0.59 and `"queue_position"` 1
(the worker has not picked it up yet) or 0 (it has); `job` shows `running` within a few seconds, then `done` in about
110 s, cost about 0.3. In the browser the job page counts the elapsed time and shows the five phases.

**Drill 2. A second concurrent upload queues** (do this within a minute of drill 1, while it is still running). $0.
```
R2=$(up $W/tiny.mp4 $W/one_def.json "$CONSENT"); echo "$R2"; J2=$(echo "$R2" | head -1 | jq -r .id); job $J2
```
Expect `HTTP 200`, `queue_position` 1, and `job` showing `"status":"queued","queue_position":1` (the page says
"Queued, position 1. It starts as soon as the job ahead finishes."). Now a third while two are in flight:
```
up $W/tiny.mp4 $W/one_def.json "$CONSENT"
```
Expect `HTTP 503`, `"error":"queue_full"` and a `friendly` sentence that mentions the showcase, `make demo`, the README
link and "try again in a few minutes". Nothing is created. Then drop the queued one (a cancelled queued job costs
nothing; it still counts as one of your 10 runs):
```
curl -sS $RS -X POST $BASE/api/jobs/$J2/cancel
```
Expect `{"cancelled":true}`.

**Drill 3. The consent field missing is a 400.** $0. (Run after the cancel above; with two jobs in flight you would get
the queue message first, because that check runs before the body is read.)
```
up $W/tiny.mp4 $W/one_def.json ""
```
Expect `HTTP 400`, `"error":"consent_required"`, and a `friendly` text that quotes the checkbox sentence and ends "Nothing
was kept or spent." Also try `-F consent=no` (as the third argument, `"-F consent=no"`): the same 400.

**Drill 4. A file that is not a video is rejected.** $0.
```
up $W/fake.mp4 $W/one_def.json "$CONSENT"
up $W/notes.txt $W/one_def.json "$CONSENT"
```
Expect `HTTP 400 "error":"unreadable"` (message "file is not a readable video") and `HTTP 415 "error":"bad_type"`, each with
a `friendly` sentence saying nothing was kept or spent and pointing to the sample clip. Check no spend: `adm` is unchanged by
these two (it moves only for drill 1 and drill 6).

**Drill 5. A forced cap hit via a temporary low cap.** $0 (refused before any model call). Two ways; (a) is the one to
run, because it does not touch the live service.

(a) A throwaway instance on port 8011 with a $0.05 cap (its own data dir and caller prefix, so it neither reads nor
writes the public instance's budget). Run from the main checkout after the merge:
```
cd /home/ubuntu/work/etho
ETHO_APP_PUBLIC=1 ETHO_APP_OPEN=1 ETHO_SANDBOX=bwrap ETHO_APP_TAG=dev ETHO_APP_CALLER_PREFIX=drill:job: ETHO_APP_DATA_DIR=$W/appdata ETHO_APP_SHOWCASE_DIR=/home/ubuntu/work/etho/data/showcase ETHO_APP_DAILY_CAP_USD=0.05 ETHO_APP_JUDGING_DAILY_CAP_USD=0.05 ETHO_APP_KILL_FILE=$W/nokill ETHO_APP_READONLY_FILE=$W/noro bin/app --port 8011 > $W/cap.log 2>&1 &
sleep 15; curl -s http://127.0.0.1:8011/api/health | jq -c .
curl -s -w '\nHTTP %{http_code}\n' -F "video=@$W/tiny.mp4" -F "definitions=<$W/one_def.json" -F consent=yes http://127.0.0.1:8011/api/jobs
curl -s http://127.0.0.1:8011/api/config | jq '.live | {mode, capped, can_submit, message}'
```
Expect health `"mode":"live","open":true`; the upload `HTTP 429 "error":"daily_cap"` with a `friendly` text that says
today's budget is used up, the precomputed showcase stays open on the site, `make demo` with the README link, and "try again
tomorrow"; `/api/config` shows `"capped":"daily_cap","can_submit":false` and the same message (the home page then shows
it in the note at the top, with the links, and hides the form). If the mode says `read-only` instead, bubblewrap is not
usable for your shell user; use (b). Stop the throwaway instance (own block; it matches only the process started with `--port 8011`, never the service on 8000):
```
kill $(pgrep -f 'etho.app.server --port 8011')
```
(b) On the public instance: `set_env`-style edit (section 1.2) `ETHO_APP_DAILY_CAP_USD 0.05` and
`ETHO_APP_JUDGING_DAILY_CAP_USD 0.05`, restart, run the third command of (a) against `$BASE` with `$RS`, then set `8` and `20`
back and restart again. Two restarts during judging hours; only if (a) is not possible.

For the total-cap wording: the same with `ETHO_APP_TOTAL_CAP_USD=0.05` in (a); the message then says live use is over for
this demo and does not say "tomorrow".

**Drill 6. A clip with no animal.** `testsrc` is a test pattern. Expected cost at most **$0.48** (the 20 s, one
behavior estimate is $0.23 and the job's reserved ceiling is $0.48); likely $0.1 to $0.25. Start it after drill 1 has
finished (wait for `job $J1` to say `done`), so it starts at once:
```
R6=$(up $W/noanimal.mp4 $W/one_def.json "$CONSENT"); echo "$R6"; J6=$(echo "$R6" | head -1 | jq -r .id); job $J6
```
Poll `job $J6` every 10 s until it is `done`, `failed` or `cancelled`; if it runs past 5 minutes or `cost_usd` passes 0.48, cancel
it with `curl -sS $RS -X POST $BASE/api/jobs/$J6/cancel`. **What to expect, honestly:** the code does not refuse a
no-animal clip at the planner. The planner always returns a plan (it falls back to its best phrase), the job then runs
SAM tracking, and a clip where both animals are found in under 30% of samples only gets a warning ("Both animals were
tracked in only N% of samples; scores may be unreliable"). So the likely outcomes are: the job ends with zero or few bouts and
that warning, or it fails in a later stage and the page shows "Scoring this video did not finish..." with the
showcase link. Either way it is bounded by the ceiling above. Record what you see and the cost (`job $J6`, and `adm`
afterwards). If this is not acceptable for the demo ("stops early and cheaply at the planner" was the plan), the
fix is a small planner check (expected count 0 or no SAM hits in the two image checks stops the job before tracking); it
is not in this branch.

**Drill 7. The per-visitor limit.** $0 (every upload is cancelled while it is still queued). It needs exactly one job
running, so start it while drill 6's job is running (not queued behind another: with two in flight the queue message comes
first). Runs so far from this address: drills 1, 2 and 6 = 3. The loop makes seven more, each cancelled at once, then the
eighth is the 11th run:
```
for i in $(seq 1 12); do out=$(up $W/tiny.mp4 $W/one_def.json "$CONSENT"); code=$(echo "$out" | tail -1); echo "run $i: $code"; case "$code" in "HTTP 200") id=$(echo "$out" | head -1 | jq -r .id); curl -sS $RS -X POST $BASE/api/jobs/$id/cancel; echo;; *) echo "$out" | head -1 | jq .; break;; esac; done
```
Expect `run 1` to `run 7`: `HTTP 200` and `{"cancelled":true}`; `run 8: HTTP 429` with `"error":"rate_limited"` and a
`friendly` text: "You have used your 10 live runs for the last 24 hours (a revision counts as a run)... please try again
tomorrow", the showcase, `make demo` and the README link. A `Retry-After` header is set. If `run 1` returns 503
`queue_full`, two jobs were in flight: wait for one to end and rerun. If a tiny upload starts running before you cancel it
(the running job just ended), it costs at most its ceiling, $0.37, normally about $0.1; it still stops at the next stage.
This address then cannot upload for 24 h (other visitors are unaffected; test from a phone, not this VM, after the
drills). The browser form shows "Your runs in the last 24 hours: 10 of 10" for it.

**After the drills.** The cost: `adm` again; the difference from the "before" figure is the drill spend (expect about 0.4 to
0.6; stop and tell Aniket if it passes 1.0). Then clean up the temp files (own block, a temp dir made above):
```
rm -rf "$W"
```
The uploaded jobs delete themselves 24 h after upload.

## 5. If a drill fails

`touch /home/ubuntu/work/etho/ops/app-readonly` closes uploads at once (no restart). `touch .../ops/app-kill` also
cancels the running job. To go back to the code-gated or read-only state, delete `ETHO_APP_OPEN=1` from `app.env` and restart
(own block, WARNING, it deletes a line):
```
sudo sed -i '/^ETHO_APP_OPEN=/d' /etc/etho-app/app.env
```
