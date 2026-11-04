# Deploying open live uploads (branch `open-uploads`, written Nov 4 for the afternoon deploy)

What it is: `ETHO_APP_OPEN=1` makes uploads and revisions on the hosted app work with **no access code and no login**.
Everything else about the hosted app is as in `docs/HOSTING.md`. The default is unchanged: a public app with no
usable code file and without `ETHO_APP_OPEN` stays read-only.

What stays in force in open mode: the kill switch (`ops/app-kill`), the read-only file (`ops/app-readonly`), the
live-until date (Nov 10 23:59:59 ET), the bubblewrap requirement, the 24 h deletion, the privacy notice, the showcase
(open to everyone, zero spend), the sample-clip download on the home page.

**Budget note.** The Meta tag budgets moved (DECISIONS 2026-11-04): `eval-llm` 30 to 1, `eval-sam` 40 to 25,
`demo-reserve` 35 to 79 (sum still 140, no provider cap changed). `eval-sam` has spent $24.58, so it has about **$0.42**
of headroom left: no further SAM evaluation run is possible without a new budget move.

## 0. How it behaves (so you can check it against what you see)

| Item | Value |
|---|---|
| Who may upload | anyone, no code; the upload form must carry `consent=yes`, else HTTP 400 `consent_required` (checked on the server before the file is read) |
| Consent text on the page | "I have the right to upload this video, and it contains no people" |
| Per visitor | 10 runs per rolling 24 h per client IP. A run is an analysis job; a revision is a run too (each counts as one; a showcase revision too). A rejected upload is not a run |
| Per client, at once | 1 upload in progress, and 1 job queued or running (a revision counts as that job); a second gets 429 `client_busy` ("you already have a run in progress; wait for it to finish") |
| Upload body deadline | 120 s for the whole body (`ETHO_APP_UPLOAD_DEADLINE_S`), then 408 `upload_timeout` and the slot is free again |
| Rejected uploads | 10 per client per hour (bad file, missing consent, too large), then 429 `too_many_rejected` for the rest of the hour |
| Jobs at once | at most 2 queued or running across all visitors (`ETHO_APP_MAX_IN_FLIGHT`); the worker runs **one** job at a time, so the second waits with a visible position ("Queued, position 1"). Because one client may hold only one job, a second visitor is needed to see the queue |
| Upload | mp4, mov, avi; 50 MB; 120 s; ffprobe check before anything is spent |
| Daily cap | $8 per rolling 24 h before Nov 4 ET, **$20 inside Nov 4-9 ET** (judging window), $8 again on Nov 10 |
| Total cap | $60 over all time, counting only this instance's own ledger rows (`pub:job:`) |
| When a limit is hit | a plain message that points to the showcase on the site and says an offline replay demo (`make demo`) is described in the project README (with the link), and says when it resets: tomorrow for the daily and per-visitor limits, "live use is over for this demo" for the total cap, "a few minutes" for a full queue, "about an hour" for the rejection limit |

Per-visitor and availability counters are in memory: a restart of the app resets them (the daily and total caps come
from the ledger and survive restarts). Client keys are the client IP as uvicorn sees it (`--forwarded-allow-ips 127.0.0.1`
makes it the real public IP through Funnel; an IPv6 visitor is keyed on the /64). Section 4.0 checks that a visitor
cannot choose their own key with an `X-Forwarded-For` header.

## 1. The `app.env` lines (Aniket)

Nothing is removed except, only if it is there, the one line named in 1.3. The block in 1.2 sets each key in place if it
already exists and appends it if it does not (adding a final newline first if the file lacks one); it never deletes a
line, and it refuses a value with odd characters.

### 1.1 Look at what is there now (changes nothing)
```
sudo grep -E '^ETHO_APP_(OPEN|RATE_JOBS|RATE_WINDOW_S|DAILY_CAP_USD|JUDGING_DAILY_CAP_USD|TOTAL_CAP_USD|MAX_UPLOAD_MB|MAX_DURATION_S|MAX_IN_FLIGHT|MAX_UPLOADS_PER_CLIENT|MAX_JOBS_PER_CLIENT|MAX_REJECTS_PER_HOUR|UPLOAD_DEADLINE_S|READ_ONLY|LIVE_UNTIL)=' /etc/etho-app/app.env; ls -l /etc/etho-app/app.env
sudo grep -E '^ETHO_APP_(KILL_FILE|READONLY_FILE)=' /etc/etho-app/app.env
```
The second command must print exactly these two lines; they are what makes `ops/app-kill` and `ops/app-readonly` work
(sections 3 and 5). If either is missing or points elsewhere, 1.2 sets them.
```
ETHO_APP_KILL_FILE=/home/ubuntu/work/etho/ops/app-kill
ETHO_APP_READONLY_FILE=/home/ubuntu/work/etho/ops/app-readonly
```

### 1.2 Set the lines (paste the whole block)
```
set_env() {
  f=/etc/etho-app/app.env
  case "$2" in ''|*[!0-9A-Za-z._:+/-]*) echo "refusing odd value for $1: $2"; return 1;; esac
  if [ -n "$(sudo tail -c1 "$f")" ]; then echo | sudo tee -a "$f" >/dev/null; fi
  if sudo grep -q "^$1=" "$f"; then sudo sed -i "s|^$1=.*|$1=$2|" "$f"; else echo "$1=$2" | sudo tee -a "$f" >/dev/null; fi
}
set_env ETHO_APP_KILL_FILE /home/ubuntu/work/etho/ops/app-kill
set_env ETHO_APP_READONLY_FILE /home/ubuntu/work/etho/ops/app-readonly
set_env ETHO_APP_OPEN 1
set_env ETHO_APP_RATE_JOBS 10
set_env ETHO_APP_RATE_WINDOW_S 86400
set_env ETHO_APP_DAILY_CAP_USD 8
set_env ETHO_APP_JUDGING_DAILY_CAP_USD 20
set_env ETHO_APP_TOTAL_CAP_USD 60
set_env ETHO_APP_MAX_UPLOAD_MB 50
set_env ETHO_APP_MAX_DURATION_S 120
set_env ETHO_APP_MAX_IN_FLIGHT 2
set_env ETHO_APP_MAX_UPLOADS_PER_CLIENT 1
set_env ETHO_APP_MAX_JOBS_PER_CLIENT 1
set_env ETHO_APP_MAX_REJECTS_PER_HOUR 10
set_env ETHO_APP_UPLOAD_DEADLINE_S 120
unset -f set_env
```
The lines this leaves in the file, exactly:
```
ETHO_APP_KILL_FILE=/home/ubuntu/work/etho/ops/app-kill
ETHO_APP_READONLY_FILE=/home/ubuntu/work/etho/ops/app-readonly
ETHO_APP_OPEN=1
ETHO_APP_RATE_JOBS=10
ETHO_APP_RATE_WINDOW_S=86400
ETHO_APP_DAILY_CAP_USD=8
ETHO_APP_JUDGING_DAILY_CAP_USD=20
ETHO_APP_TOTAL_CAP_USD=60
ETHO_APP_MAX_UPLOAD_MB=50
ETHO_APP_MAX_DURATION_S=120
ETHO_APP_MAX_IN_FLIGHT=2
ETHO_APP_MAX_UPLOADS_PER_CLIENT=1
ETHO_APP_MAX_JOBS_PER_CLIENT=1
ETHO_APP_MAX_REJECTS_PER_HOUR=10
ETHO_APP_UPLOAD_DEADLINE_S=120
```
Notes. `ETHO_APP_TOTAL_CAP_USD` was 30. `ETHO_APP_DAILY_CAP_USD` is set on purpose together with
`ETHO_APP_JUDGING_DAILY_CAP_USD`: an explicit daily cap is never raised in the judging window unless the judging value is
set too. The rate, in-flight and per-client lines are also the open-mode defaults; they are written out so that what
runs is visible. Leave `ETHO_APP_ACCESS_CODE_FILE` and `ETHO_APP_LIVE_UNTIL` as they are (the code file is then only the
operator's key for the admin health view; it is not asked of visitors).

### 1.3 Check the file, and the one possible removal
```
sudo grep -E '^ETHO_APP_(OPEN|RATE_JOBS|RATE_WINDOW_S|DAILY_CAP_USD|JUDGING_DAILY_CAP_USD|TOTAL_CAP_USD|MAX_UPLOAD_MB|MAX_DURATION_S|MAX_IN_FLIGHT|MAX_UPLOADS_PER_CLIENT|MAX_JOBS_PER_CLIENT|MAX_REJECTS_PER_HOUR|UPLOAD_DEADLINE_S|KILL_FILE|READONLY_FILE|READ_ONLY|LIVE_UNTIL)=' /etc/etho-app/app.env; ls -l /etc/etho-app/app.env
sudo awk -F= 'NF>2 {print "JOINED LINE: " $0}' /etc/etho-app/app.env
sudo tail -c 150 /etc/etho-app/app.env | cat -A
```
Expect each key once with the values above, `ETHO_APP_LIVE_UNTIL=2026-11-10T23:59:59-05:00`, and the file still
`-rw-r----- 1 root etho-app`. The `awk` line must print nothing (a line that printed would be two lines glued together
because the file had no final newline). The last command shows each line ending in `$` and the very last line
ending in `$` too. If `ETHO_APP_READ_ONLY=1` is listed, the site stays read-only whatever else is set. Only then,
run this and nothing else (it is the one line this deploy may delete; HOSTING.md section 7C):

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
The public view, through the public URL (the same way a visitor reaches it), must not show `reasons`, `sandbox`,
`client_seen_as` (open mode is not an operator view):
```
HOST=$(sed 's|https://||' /home/ubuntu/work/etho/ops/public-url); IP=199.38.181.54
curl -s --resolve $HOST:443:$IP https://$HOST/api/health | jq 'keys'
```
Expect only `caps`, `live_until`, `mode`, `ok`, `open`.

## 3. Open the site (own block, reversible)

**Run 4.0 (the `X-Forwarded-For` spoof check) first; it works while the site is still read-only. If it fails, do not run
this step.** This is the moment uploads become public. Run the drills straight after, and put the file back if one fails.
```
rm /home/ubuntu/work/etho/ops/app-readonly
```
Undo, instantly, no restart: `touch /home/ubuntu/work/etho/ops/app-readonly`. The kill switch is
`touch /home/ubuntu/work/etho/ops/app-kill`.

## 4. Drills through the public URL (lead)

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

### 4.0 Spoof check: a visitor cannot pick their own client key (run before section 3)
Funnel passes the real client address; the check is that a forged `X-Forwarded-For` does not replace it. Send a request
through the public URL with a forged header and read back what the app saw (admin view, code read by `sudo`, never
printed):
```
sudo sh -c "curl -s --resolve $HOST:443:$IP -H 'X-Forwarded-For: 9.9.9.9' -H \"X-Etho-Admin: \$(cat /etc/etho-app/access_code)\" $BASE/api/health" | jq '{client_seen_as, shared_client_bucket}'
sudo sh -c "curl -s --resolve $HOST:443:$IP -H \"X-Etho-Admin: \$(cat /etc/etho-app/access_code)\" $BASE/api/health" | jq '{client_seen_as, shared_client_bucket}'
```
Expect both to print the same `client_seen_as`: the VM's real egress address, **not** `9.9.9.9`, not `127.0.0.1` and not a
`100.x` address, with `"shared_client_bucket": false`. **If the first one prints `9.9.9.9`** (the header is trusted
as sent), per-visitor limits can be dodged by sending a different header on every request, and the caps are then
the only protection: **keep the site read-only** (leave `ops/app-readonly` in place, do not run section 3) and tell the
lead. If either prints a loopback or `100.x` address, every visitor shares one bucket (HOSTING.md section 7, check 1): also
stay read-only, because the 10-runs limit would then be 10 for the whole site.

### Cost of each drill

| Drill | Expected | Worst case |
|---|---|---|
| 4.0 spoof check | $0 | $0 |
| 1 sample upload | $0.29 (measured on the Nov 3 recording run, 109 s) | $0.89 (the reserved ceiling; the job cannot spend past it) |
| 2 second upload, same IP | $0 | $0 (refused before the body is read) |
| 2b queued upload from a phone | $0 (cancelled while queued) | $0.37 (its ceiling, if it starts before you cancel) |
| 3 consent missing | $0 | $0 |
| 4 not a video | $0 | $0 |
| 5 cap hit (throwaway instance) | $0 | $0 (refused at admission) |
| 6 no-animal clip | $0.10 to $0.25 (not measured) | $0.48 (the 20 s job's ceiling) |
| 7 per-visitor limit (throwaway instance, no keys) | $0 | $0 (no key file: the first model call fails) |
| Total | about $0.4 to $0.6 | $1.74 if every job ran to its ceiling |

The worst case is the sum of the ceilings, about three times what these jobs measure; `adm` after the drills gives the
real figure. Under $1 holds unless a job runs far above its measured cost.

**Drill 1. Upload of the sample clip.**
```
R1=$(up $W/sample.mp4 $W/sample_defs.json "$CONSENT"); echo "$R1"; J1=$(echo "$R1" | head -1 | jq -r .id); job $J1
```
Expect `HTTP 200`, a body with `"id":"j..."`, `"url":"/p/j..."`, `"est_cost_usd"` about 0.59 and `"queue_position"` 1
(the worker has not picked it up yet) or 0 (it has); `job` shows `running` within a few seconds, then `done` in about
110 s, cost about 0.3. In the browser the job page counts the elapsed time and shows the five phases.

**Drill 2. A second concurrent upload.** Do this within a minute of drill 1, while it is still running. From the same
IP it is refused (one job per client), for $0:
```
up $W/tiny.mp4 $W/one_def.json "$CONSENT"
```
Expect `HTTP 429`, `"error":"client_busy"` and a `friendly` sentence: "You already have a run in progress: please wait
for it to finish...". Nothing is created.

**Drill 2b. The queue, from a second visitor.** Open `$BASE` on a phone on mobile data (a different address), upload the
sample clip or a short clip with the consent box ticked while drill 1 is running. Expect the job page to say "Queued,
position 1. It starts as soon as the job ahead finishes." Press **Cancel** on it (a cancelled queued job costs nothing;
it still counts as one of that phone's 10 runs). A third visitor while two jobs are in flight would get 503 `queue_full`;
that case is covered by tests, not drilled.

**Drill 3. The consent field missing is a 400.** $0. Run it when you have no job in flight (the busy check runs before the
body is read, so with a job in flight you would get `client_busy` first).
```
up $W/tiny.mp4 $W/one_def.json ""
```
Expect `HTTP 400`, `"error":"consent_required"`, and a `friendly` text that quotes the checkbox sentence and ends "Nothing
was kept or spent." Also try `"-F consent=no"` as the third argument: the same 400. These rejections count toward the
10 per hour allowed (below).

**Drill 4. A file that is not a video is rejected.** $0.
```
up $W/fake.mp4 $W/one_def.json "$CONSENT"
up $W/notes.txt $W/one_def.json "$CONSENT"
```
Expect `HTTP 400 "error":"unreadable"` (message "file is not a readable video") and `HTTP 415 "error":"bad_type"`, each with
a `friendly` sentence saying nothing was kept or spent and pointing to the sample clip. `adm` is unchanged by these.
Together with drill 3 that is 4 rejections from this address in the hour (the limit is 10, then 429
`too_many_rejected` with a message that says to try again in about an hour; covered by tests, not drilled).

**Drill 5. A forced cap hit via a temporary low cap.** $0 (refused before any model call). A throwaway instance on port
8011 with a $0.05 cap, with its own data dir and caller prefix, so it neither reads nor writes the public instance's
budget. Run from the main checkout after the merge. This function starts a throwaway instance with extra `NAME=value`
settings:
```
throwaway() {
  ( cd /home/ubuntu/work/etho && env ETHO_APP_PUBLIC=1 ETHO_APP_OPEN=1 ETHO_SANDBOX=bwrap ETHO_APP_TAG=dev ETHO_APP_CALLER_PREFIX=drill:job: ETHO_APP_DATA_DIR=$W/appdata$1 ETHO_APP_SHOWCASE_DIR=/home/ubuntu/work/etho/data/showcase ETHO_APP_KILL_FILE=$W/nokill ETHO_APP_READONLY_FILE=$W/noro "${@:2}" bin/app --port $1 > $W/t$1.log 2>&1 & )
  sleep 15; curl -s http://127.0.0.1:$1/api/health | jq -c .
}
throwaway 8011 ETHO_APP_DAILY_CAP_USD=0.05 ETHO_APP_JUDGING_DAILY_CAP_USD=0.05
curl -s -w '\nHTTP %{http_code}\n' -F "video=@$W/tiny.mp4" -F "definitions=<$W/one_def.json" -F consent=yes http://127.0.0.1:8011/api/jobs
curl -s http://127.0.0.1:8011/api/config | jq '.live | {mode, capped, can_submit, message}'
```
Expect health `"mode":"live","open":true`; the upload `HTTP 429 "error":"daily_cap"` with a `friendly` text that says
today's budget is used up, the precomputed showcase stays open on the site, an offline replay demo (`make demo`) is described
in the project README (with the link), and "try again tomorrow"; `/api/config` shows `"capped":"daily_cap","can_submit":false`
and the same message (the home page then shows it in the note at the top, with the links, and hides the form). If the mode
says `read-only` instead, bubblewrap is not usable for your shell user: the cap can then only be drilled on the public
instance (edit `ETHO_APP_DAILY_CAP_USD` and `ETHO_APP_JUDGING_DAILY_CAP_USD` to 0.05 with the 1.2 pattern, restart, drill, set `8` and
`20` back, restart; two restarts during judging hours). Stop the throwaway instance (own block; it matches only the
process started with that port, never the service on 8000):
```
kill $(pgrep -f 'etho.app.server --port 8011')
```
For the total-cap wording: the same with `ETHO_APP_TOTAL_CAP_USD=0.05`; the message then says live use is over for this demo
and does not say "tomorrow".

**Drill 6. A clip with no animal.** `testsrc` is a test pattern. Start it after drill 1 has finished (wait for
`job $J1` to say `done`; one job per client), so it starts at once:
```
R6=$(up $W/noanimal.mp4 $W/one_def.json "$CONSENT"); echo "$R6"; J6=$(echo "$R6" | head -1 | jq -r .id); job $J6
```
Poll `job $J6` every 10 s until it is `done`, `failed` or `cancelled`; if it runs past 5 minutes or `cost_usd` passes 0.48, cancel
it with `curl -sS $RS -X POST $BASE/api/jobs/$J6/cancel`. **What to expect, honestly:** the code does not refuse a
no-animal clip at the planner. The planner always returns a plan (it falls back to its best phrase), the job then runs
SAM tracking, and a clip where both animals are found in under 30% of samples only gets a warning ("Both animals were
tracked in only N% of samples; scores may be unreliable"). So the likely outcomes are: the job ends with zero or few bouts and
that warning, or it fails in a later stage and the page shows "Scoring this video did not finish..." with the
showcase link. Either way it is bounded by the ceiling in the cost table. Record what you see and the cost (`job $J6`, and
`adm` afterwards). A planner check that stops such a clip before tracking is not in this branch (not decided).

**Drill 7. The per-visitor limit, at $0.** Running the real 10 runs would need a job running while eleven are cancelled,
and one job per client now forbids that. Instead use a throwaway instance with a limit of 2 and **no key file**, so
every job it admits fails at its first model call and costs nothing (the ledger gets two rows with `"cost_usd": 0.0` and
the prefix `drill:job:`):
```
throwaway 8012 ETHO_SECRETS=$W/no-such-keys-file ETHO_APP_RATE_JOBS=2
for i in 1 2 3; do out=$(curl -sS -w '\nHTTP %{http_code}\n' -F "video=@$W/tiny.mp4" -F "definitions=<$W/one_def.json" -F consent=yes http://127.0.0.1:8012/api/jobs); code=$(echo "$out" | tail -1); echo "run $i: $code"; case "$code" in "HTTP 200") id=$(echo "$out" | head -1 | jq -r .id); until curl -s http://127.0.0.1:8012/api/jobs/$id | jq -e '.status != "queued" and .status != "running"' >/dev/null; do sleep 1; done; curl -s http://127.0.0.1:8012/api/jobs/$id | jq -c '{status, cost_usd}';; *) echo "$out" | head -1 | jq '{error, friendly}';; esac; done
```
Expect `run 1` and `run 2`: `HTTP 200` and `{"status":"failed","cost_usd":0}`; `run 3: HTTP 429` with `"error":"rate_limited"` and a
`friendly` text: "You have used your 2 live runs for the last 24 hours (a revision counts as a run)... please try again
tomorrow", the showcase, and the README link. (This was run against the branch before it was handed over: the same three
lines.) Stop it:
```
kill $(pgrep -f 'etho.app.server --port 8012')
```

**After the drills.** The cost: `adm` again; the difference from the "before" figure is the drill spend (expect about 0.4 to
0.6; stop and tell Aniket if it passes 1.0). The public instance is untouched by drills 5 and 7. Then clean up the temp files
(own block, a temp dir made above):
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
