# Hosting the Etho web app during judging (ADR-003, approved 2026-10-28)

Aniket runs the sudo commands below; the code that makes them work is on branch `worktree-agent-ab0d7665ca99e742a`
(merge it first). Nothing here was run with sudo by the agent that wrote it: **the commands are untested as written**,
so the checks in sections 7 and 8 matter. The app itself was run in public mode against temp directories (no keys, no
provider calls) to confirm that it starts, reports its mode and refuses uploads without the code, and the unit files
below were syntax-checked with `systemd-analyze verify`.

The window: the site must stay up from submission (Nov 4, 9 PM ET) through the finals on **Nov 9**. Live mode switches
itself off at Nov 10 23:59:59 ET; read-only browsing can stay up after that. The optional stop timer (section 12) never
stops the service before Nov 11 00:05 ET.

What the visitor sees:

| Mode | How you get there | What works |
|---|---|---|
| `live` | none of the below | Showcases, plus uploads and revisions for a visitor who entered the access code. |
| `live` but over budget | the daily cap ($3 per 24 h, $8 in the Nov 4-9 ET judging window) or the $30 total cap has no room for the smallest job | Showcases; the page says the live budget is used up and uploads reopen as the window moves on (daily) or are closed (total). |
| `read-only` | `ETHO_APP_READ_ONLY=1`, the file `ops/app-readonly`, or (public only) no usable access code, or the rule sandbox is not bubblewrap | Showcases, reports, audit trails. Uploads and revisions (a showcase revision too: it spends money) are refused. |
| `killed` | the file `ops/app-kill` exists | Like read-only, and the running job is cancelled at its next model call and the queue is cancelled. |
| `expired` | after `ETHO_APP_LIVE_UNTIL` (default 2026-11-10 23:59:59 America/New_York) | Like killed. |

In every one of these states the browser UI shows a plain sentence ("Today's live budget for this demo is used up ...
the precomputed showcase runs stay open") and the showcase stays browsable; an API client still gets the structured
4xx. A proxy error page or a dropped connection is turned into a sentence too (`static/common.js`).

`GET /api/health` shows anyone the mode and the budget left. The operator (a visitor holding the access cookie, or the
code in an `X-Etho-Admin` header) also gets the reasons, sandbox mode, queue, retention run, ledger status and how the
client address looks. From the shell:

```
sudo sh -c 'curl -s -H "X-Etho-Admin: $(cat /etc/etho-app/access_code)" http://127.0.0.1:8000/api/health'
```

(the code is briefly in that `sh -c` command line, visible to root and `ubuntu` on this box; wrong headers are throttled
like wrong codes). The watchdog and the uptime check use the public view only.

## 1. Before you start

On this VM (as `ubuntu`), after merging the branch:

```
cd /home/ubuntu/work/etho
git log --oneline -1            # confirm the merge is there
uv sync                         # the service runs .venv directly; nothing is installed at start
make check
sudo apt-get update && sudo apt-get install -y acl      # setfacl is not installed on this VM (checked)
```

`tailscale` is not installed either (section 7).

## 2. The unix user and its directories

```
sudo useradd --system --user-group --create-home --home-dir /var/lib/etho-app --shell /usr/sbin/nologin etho-app
sudo install -d -o etho-app -g etho-app -m 0700 /var/lib/etho-app/data
sudo install -d -o root -g etho-app -m 0750 /etc/etho-app
```

`/var/lib/etho-app/data` holds everything the app writes: uploads (`jobs/`), its own gateway response cache
(`gateway_cache/`), its own chunk cache (`work/cache/chunks`, never the repo's `cache/chunks`), temp files (`tmp/`),
the detector cache and `meta_files_undeleted.jsonl` (ids of Meta files that could not be deleted). All of it is deleted
by the retention timer after 24 h, except the detector cache (code only, no video or user data) and that id list.

## 3. The shared ledger

The app has to append to the same ledger as everything else, so there is one set of caps. Only these things are
granted, to the user `etho-app` and nothing else:

```
cd /home/ubuntu/work/etho
touch ops/spend.jsonl ops/.spend.lock ops/alerts.jsonl
mkdir -p ops/inflight
# spend.jsonl (append), .spend.lock (the cross-process flock), alerts.jsonl (written when a cap alert fires)
sudo setfacl -m u:etho-app:rw ops/spend.jsonl ops/.spend.lock ops/alerts.jsonl
# inflight/ holds one reservation file per model call in flight. The default ACL lets your own processes read and
# remove reservation files the app creates, and the other way round.
sudo setfacl -m u:etho-app:rwx,u:ubuntu:rwx,d:u:etho-app:rwx,d:u:ubuntu:rwx ops/inflight
sudo setfacl -R -m u:etho-app:rwX,u:ubuntu:rwX ops/inflight
# the Codex queue and outputs stay closed to the app
sudo setfacl -m u:etho-app:--- ops/codex
# the kill and read-only switches are plain files in ops/; you create and remove them as yourself
```

**Append-only ledger.** So that a compromised app cannot rewrite the spend history the caps are computed from:

```
sudo chattr +a /home/ubuntu/work/etho/ops/spend.jsonl
lsattr /home/ubuntu/work/etho/ops/spend.jsonl           # shows the 'a' flag
```

Appends from every process (the gateway in your sessions, the app) keep working: they open the file for append, which
is all the gateway does (`etho/gateway/ledger.py`). What stops working is anything that truncates, replaces or
deletes the file, **including git**: `ops/spend.jsonl` is tracked in this repo, so a `git merge`, `git pull` or
`git checkout` that changes it fails with "Operation not permitted". Before such an operation run
`sudo chattr -a ops/spend.jsonl`, and set `+a` again afterwards. A bad line the app appends is not a way around the caps:
a row it cannot read counts as more than the cap (`etho/app/jobgw.py`, `BAD_ROW_USD`). Note that because the ledger is
committed, the `pub:job:<id>` rows of visitors' jobs (job id, time, tokens, cost, SAM phrase; no video or text) end up
in the repo's history once you commit it, and the repo becomes public.

The code, the venv and the showcases are not granted by ACL. `/home/ubuntu` is mode 750, so `etho-app` cannot enter it
at all, and the unit (section 5) gives the service a private, empty `/home` with only these paths bind-mounted into it:
read-only `etho/`, `configs/`, `.venv/`, `data/showcase/` of the checkout, and read-write `ops/` (where DAC, i.e. the
ACLs above, decides which files it may touch). Everything else under `/home/ubuntu` (`~/.config`, `~/.claude`,
`~/.codex`, `~/.ssh`, the git credentials, `.git`, `eval/`, `docs/`) does not exist for the service. Showcases are read
in place on purpose: a packaged showcase stores the absolute path of its own `source.mp4` in `tracking.json`, so it
cannot be moved.

## 4. Secrets and settings: you fill these in yourself

The service's secrets file holds only the Meta and Fireworks keys. The agent never saw or wrote real values.

```
sudo install -m 0640 -o root -g etho-app /dev/null /etc/etho-app/keys.env
sudoedit /etc/etho-app/keys.env
```

Put exactly these two lines in it (replace the placeholders; no quotes, no spaces around `=`):

```
META_MODEL_API_KEY=PASTE_META_KEY_HERE
FIREWORKS_API_KEY=PASTE_FIREWORKS_KEY_HERE
```

The access code for live use (this is what goes in the submission notes; it is never on the public page). It must be at
least 16 characters; this generates 24 random base64 characters (about 140 bits). Read it yourself:

```
sudo sh -c 'umask 077; head -c 24 /dev/urandom | base64 | tr -d "=+/" | cut -c1-24 > /etc/etho-app/access_code; chown root:etho-app /etc/etho-app/access_code; chmod 0640 /etc/etho-app/access_code'
sudo cat /etc/etho-app/access_code
```

To rotate it later: write a new value to the same file. No restart is needed, and every cookie signed with the old code
stops working at once. A wrong code is throttled (5 per 15 minutes per client, then 429 and a one second delay; past 600
an hour over the whole server a warning in the log and a delay), but **the right code is never refused**, so nobody can
lock a real visitor out by guessing. For a read-only-only deployment (section 7, step A) you can skip `keys.env` and the
code file.

Non-secret settings live in a second file so you can change them without touching the unit:

```
sudo tee /etc/etho-app/app.env >/dev/null <<'EOF'
ETHO_ROOT=/home/ubuntu/work/etho
HOME=/var/lib/etho-app
ETHO_SECRETS=/etc/etho-app/keys.env
ETHO_SANDBOX=bwrap
ETHO_APP_PUBLIC=1
ETHO_APP_DATA_DIR=/var/lib/etho-app/data
ETHO_APP_ACCESS_CODE_FILE=/etc/etho-app/access_code
ETHO_APP_KILL_FILE=/home/ubuntu/work/etho/ops/app-kill
ETHO_APP_READONLY_FILE=/home/ubuntu/work/etho/ops/app-readonly
ETHO_APP_TAG=demo-reserve
ETHO_APP_CALLER_PREFIX=pub:job:
# Daily cap: leave ETHO_APP_DAILY_CAP_USD unset for the public defaults ($3 per 24 h, $8 inside the Nov 4-9 ET
# judging window). Setting it explicitly means that value is used all the time and is NOT raised in the window,
# unless ETHO_APP_JUDGING_DAILY_CAP_USD is set too (e.g. ETHO_APP_JUDGING_DAILY_CAP_USD=8).
ETHO_APP_TOTAL_CAP_USD=30
ETHO_APP_LIVE_UNTIL=2026-11-10T23:59:59-05:00
ETHO_APP_RETENTION_H=24
ETHO_APP_MAX_UPLOAD_MB=50
ETHO_APP_MAX_DURATION_S=120
ETHO_APP_RATE_JOBS=10
ETHO_APP_READ_ONLY=1
EOF
sudo chown root:etho-app /etc/etho-app/app.env && sudo chmod 0640 /etc/etho-app/app.env
```

* `ETHO_APP_READ_ONLY=1` is deliberate: the first start is read-only (section 7). Every value above is also the default
  for `ETHO_APP_PUBLIC=1`, except `ETHO_APP_RATE_JOBS`; they are written out so that what runs is visible.
* `demo-reserve` is locked by the gateway until Nov 3 05:00 UTC (Nov 3 00:00 ET; `DEMO_RESERVE_OPENS_UTC` in
  `etho/gateway/prices.py`), so before then live jobs fail at the gateway without spending anything.
* **The caps count only this instance's own rows.** Its ledger rows carry the caller prefix `pub:job:`, and only those
  count toward the daily cap ($3 per 24 h, raised to $8 from Nov 4 00:00 ET through Nov 9 ET, the judging window) and
  the $30 total (Aniket, 2026-10-28). Your own localhost app (prefix `job:`) and recording runs on `demo-reserve` do not
  use up the public budget; they do use the gateway's `demo-reserve` tag budget of $35. That tag counts only Meta spend
  (about two thirds of an app job; tier-1 is on Fireworks), so $30 of app spend is about $20 of the tag, leaving room
  for the Nov 2 rough cut ($5) and the Nov 3 recording. If the tag runs out, the gateway refuses and the app falls back
  to the showcase.
* **`ETHO_APP_RATE_JOBS`.** Per client the limit is that many jobs per hour. If the proxy does not pass the visitor's
  address (section 7, check 1), every visitor is one client, and the number is a *total* for the whole site; 10 an hour
  fits under the $3 a day cap (and, in the Nov 4-9 ET judging window, under the $8 cap) (measured cost of a 60 s clip is $0.23 to $0.36).
* **The per-job ceiling and the daily cap ($3, or $8 in the Nov 4-9 ET window).** Admitting a job reserves its ceiling, `max(1.5 x estimate, estimate + $0.25)`
  (public default; localhost keeps 2x and $0.50), and the job cannot spend past it. A 120 s upload with 3 behaviors is
  estimated at about $0.95 and reserves about $1.43, so two such jobs fit at once under $3 (the second one is refused if
  less than its estimate is left), a third waits for one to finish. A 60 s upload estimates $0.59 and reserves $0.89, so
  three fit. The reservation is released when the job ends, and the estimate is about twice the measured cost, so the
  money actually spent is far lower than what is reserved.

## 5. The service

```
sudo tee /etc/systemd/system/etho-app.service >/dev/null <<'EOF'
[Unit]
Description=Etho web app (public read-only showcase, live mode behind an access code)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
User=etho-app
Group=etho-app
WorkingDirectory=/var/lib/etho-app
EnvironmentFile=/etc/etho-app/app.env
Environment=PYTHONPATH=/home/ubuntu/work/etho PYTHONDONTWRITEBYTECODE=1 MPLCONFIGDIR=/var/lib/etho-app/mpl
ExecStart=/home/ubuntu/work/etho/.venv/bin/python -m etho.app.server --host 127.0.0.1 --port 8000 --forwarded-allow-ips 127.0.0.1
Restart=on-failure
RestartSec=5
TimeoutStopSec=20
UMask=0077
# what the service can see: an empty /home with only these paths in it
ProtectHome=tmpfs
BindReadOnlyPaths=/home/ubuntu/work/etho/etho /home/ubuntu/work/etho/configs /home/ubuntu/work/etho/.venv /home/ubuntu/work/etho/data/showcase
BindPaths=/home/ubuntu/work/etho/ops
# what it can write: its own state directory, and ops/ (as far as the ACLs in section 3 allow)
ProtectSystem=strict
ReadWritePaths=/var/lib/etho-app /home/ubuntu/work/etho/ops
# hardening
NoNewPrivileges=yes
PrivateTmp=yes
# =yes overmounts /proc/sys, so bwrap cannot mount a fresh /proc (etho-vm, 2026-10-29)
ProtectKernelTunables=no
ProtectKernelModules=yes
ProtectControlGroups=yes
ProtectClock=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
# AF_NETLINK is needed: bubblewrap's --unshare-all brings up loopback in the new network namespace over NETLINK_ROUTE
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
SystemCallArchitectures=native
MemoryMax=8G
TasksMax=1024
# NOT set on purpose: RestrictNamespaces, PrivateDevices, SystemCallFilter. The rule sandbox (bwrap) creates
# user/mount/pid namespaces and its own /dev; those options would stop it, and the app refuses live use without it.

[Install]
WantedBy=multi-user.target
EOF
sudo systemd-analyze verify /etc/systemd/system/etho-app.service
sudo systemctl daemon-reload
```

**Pre-flight: does bubblewrap work under exactly this hardening?** Run this as root before the first start (and again
if you change a hardening line). It runs `bwrap --unshare-all` as `etho-app` with every sandboxing property of the unit
applied, and prints OK only if the child ran:

```
sudo systemd-run --uid=etho-app --gid=etho-app --wait --pipe --collect \
  -p NoNewPrivileges=yes -p PrivateTmp=yes -p ProtectSystem=strict -p ProtectHome=tmpfs \
  -p ProtectKernelModules=yes -p ProtectControlGroups=yes -p ProtectClock=yes \
  -p RestrictRealtime=yes -p RestrictSUIDSGID=yes -p LockPersonality=yes -p SystemCallArchitectures=native \
  -p "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK" \
  /usr/bin/bwrap --unshare-all --ro-bind /usr /usr --symlink usr/lib /lib --symlink usr/lib64 /lib64 \
  --proc /proc --dev /dev /usr/bin/true && echo "bwrap pre-flight: OK"
```

If it fails, drop properties one at a time (delete one `-p ...`, rerun) until it passes; the usual suspects are
`ProtectKernelTunables` and `ProtectControlGroups` (they can affect mounting `--proc`), and a missing `AF_NETLINK`
gives a "loopback: Failed RTM_NEWADDR" style error. Whatever you drop, delete the same line from the unit. The
On etho-vm (2026-10-29) `ProtectKernelTunables=yes` was the culprit ("Can't mount proc on /newroot/proc: Operation
not permitted"); it is off via the drop-in `/etc/systemd/system/etho-app.service.d/bwrap-proc.conf`. The
definitive check is still `/api/health` showing `"sandbox": {"mode": "bwrap"}` for the running service (section 8).

Then start it:

```
sudo systemctl enable --now etho-app
sudo systemctl status etho-app --no-pager
sudo journalctl -u etho-app -n 30 --no-pager
curl -s http://127.0.0.1:8000/api/health
```

If the unit fails with a mount error, `journalctl -u etho-app` names the path. A missing source path (for example
`data/showcase` before any showcase is packaged) is the usual cause: create the directory, or prefix that entry with `-`
in `BindReadOnlyPaths=` (`-/home/ubuntu/...`), which systemd documents as "ignore if missing". If the log says the
filesystem is read-only when the app writes to `ops/`, the `ReadWritePaths=` entry for `ops` is the one that has to
stay. Fallback if `ProtectHome=tmpfs` with binds is refused on this systemd (255): use `ProtectHome=read-only`, delete
the `BindReadOnlyPaths=` and `BindPaths=` lines, and give the app read access with ACLs instead: `sudo setfacl -m
u:etho-app:--x /home/ubuntu /home/ubuntu/work /home/ubuntu/work/etho /home/ubuntu/work/etho/data
/home/ubuntu/work/etho/ops` and `sudo setfacl -R -m u:etho-app:rX /home/ubuntu/work/etho/etho
/home/ubuntu/work/etho/configs /home/ubuntu/work/etho/.venv /home/ubuntu/work/etho/data/showcase`. In that mode
`~/.config` and `~/.claude` are mode 775 on this VM, so you must also deny them: `sudo setfacl -m u:etho-app:---
/home/ubuntu/.config /home/ubuntu/.claude /home/ubuntu/.local /home/ubuntu/work/etho/.git /home/ubuntu/work/etho/.claude
/home/ubuntu/work/etho/eval /home/ubuntu/work/etho/docs` (and anything else readable that you find with
`sudo -u etho-app find /home/ubuntu -readable 2>/dev/null | head`). The tmpfs variant is the default because it needs
none of that: what is not bound does not exist.

`Restart=on-failure` with `StartLimitIntervalSec=0` restarts it forever, five seconds after any crash. A restart marks
jobs that were running as failed (it never resumes and re-spends), and signs every visitor out of live mode (the cookie
key is per process). The health watchdog (section 9) covers a process that is alive but hung.

The app listens on 127.0.0.1:8000 only. `--forwarded-allow-ips 127.0.0.1` makes uvicorn believe `X-Forwarded-For` only
from the proxy on this machine. Never widen it to `*`: the rate limit and the code throttle key on that address. In
public mode `/docs`, `/redoc` and `/openapi.json` are off.

## 6. How uploads are handled

Media tools: on a public deployment ffprobe and ffmpeg run on uploaded files under bubblewrap too (no network, a
read-only `/usr`, a private `/tmp`, and only that job's directory visible and writable), on top of the forced demuxer and
`-protocol_whitelist file`. If bwrap is missing the upload is refused, never run unconfined.

Uploads are checked (type, size, a probed duration and frame count) before anything is spent, re-encoded to a clean
10 fps mp4, and cut at the duration limit; the original file is dropped. Only the re-encoded clip reaches the pipeline.

## 7. Going public, in this order

**A. Read-only floor (now, any day).** Keep `ETHO_APP_READ_ONLY=1` in `app.env`. This needs no keys at all and can
spend nothing: even if someone forges a cookie, every spending route answers 403 first.

**B. Tailscale Funnel for a stable URL.** Tailscale is not installed here; installing it, and enabling MagicDNS, HTTPS
certificates and Funnel in your tailnet, are your decisions (admin console: DNS > MagicDNS and HTTPS certificates;
Access controls: a `funnel` node attribute for this machine). Tailscale's own requirements for Funnel, from
https://tailscale.com/kb/1223/funnel: Tailscale v1.38.3 or later; MagicDNS; HTTPS certificates; a `funnel` node
attribute; ports 443, 8443 or 10000; "non-configurable bandwidth limits". Install from Tailscale's signed apt repository
(the steps are from https://pkgs.tailscale.com/stable/ for Ubuntu 24.04, read on Oct 28):

```
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list | sudo tee /etc/apt/sources.list.d/tailscale.list
sudo apt-get update && sudo apt-get install tailscale
sudo tailscale up
sudo tailscale funnel --bg 8000
tailscale funnel status                                    # prints https://<machine>.<tailnet>.ts.net
```

Write the URL where the uptime check reads it: `echo https://<machine>.<tailnet>.ts.net > /home/ubuntu/work/etho/ops/public-url`.
Stopping the public URL is in section 15 (Emergency takedown), on purpose kept away from every setup block.

Two things that must be checked once the URL is up, because they were unverifiable in advance (Tailscale's Funnel page
does not say whether HTTPS requests reach the app with `X-Forwarded-For`):

1. **Real client IP.** From a phone on mobile data, open `https://<url>/`, enter the access code, then open
   `https://<url>/api/health` in the same browser and read `client_seen_as` and `shared_client_bucket`. If it shows the
   phone's address, the per-client rate limit and the code throttle work per visitor. If it shows `127.0.0.1` or a
   `100.x` address, every visitor shares one bucket (health says so in `warnings`): `ETHO_APP_RATE_JOBS` is a total,
   and the wrong-code throttle is shared, but a real visitor with the right code is never locked out. The caps do not
   depend on the client address.
2. **Server-sent events** (the job progress page). Start a short live job from the public URL and watch the stage list
   advance. The app sends an event every second and sets `X-Accel-Buffering: no`. If the proxy buffers them, the
   page still works: the progress bar stalls, and reloading the page shows the finished result. Do not use Cloudflare
   Quick Tunnels: they do not carry SSE.

**C. Live mode, once `demo-reserve` opens (Nov 3 05:00 UTC) and the demo is recorded.** Create the keys and code
file (section 4), then:

```
sudo sed -i '/^ETHO_APP_READ_ONLY=/d' /etc/etho-app/app.env
sudo systemctl restart etho-app
curl -s http://127.0.0.1:8000/api/health        # mode must say "live"
```

Live mode needs all of: a code of at least 16 characters in the code file, bubblewrap active as the service user
(admin health shows `"sandbox": {"mode": "bwrap"}`), and none of the switches in the table above. If any one is missing
the app stays read-only; the admin view of health says which in `reasons`. Put the access code in the submission notes
by hand.

## 8. Checks to run once (each states what you should see)

What the service itself sees (its own mount namespace). Every line that starts with "ok" must print; every line that
says "NOT OK" must print nothing:

```
PID=$(systemctl show -p MainPID --value etho-app)
AS="sudo nsenter --target $PID --mount -- setpriv --reuid=etho-app --regid=etho-app --init-groups"
$AS test -r /home/ubuntu/work/etho/etho/app/server.py && echo "ok: code readable"
$AS test -r /home/ubuntu/work/etho/.venv/bin/python   && echo "ok: venv readable"
$AS test -w /home/ubuntu/work/etho/ops/spend.jsonl    && echo "ok: ledger writable"
$AS test -w /home/ubuntu/work/etho/ops/inflight       && echo "ok: inflight writable"
$AS test -w /home/ubuntu/work/etho/etho/app/server.py && echo "NOT OK: code writable"      # must print nothing
$AS test -w /home/ubuntu/work/etho/ops/alerts.jsonl   && echo "ok: alerts writable"
$AS test -w /home/ubuntu/work/etho/ops/.spend.lock    && echo "ok: ledger lock writable"
$AS test -e /home/ubuntu/.claude                      && echo "NOT OK: ~/.claude exists"
$AS test -e /home/ubuntu/.config                      && echo "NOT OK: ~/.config exists"
$AS test -r /home/ubuntu/work/etho/ops/codex          && echo "NOT OK: ops/codex readable"
$AS ls -A /home/ubuntu                                 # must list only: work
$AS ls /home/ubuntu/work/etho                          # must list only: configs  data  etho  ops
# the shared reservation directory works both ways (the service user creates, you read and remove)
$AS sh -c ': > /home/ubuntu/work/etho/ops/inflight/probe.json'
test -r /home/ubuntu/work/etho/ops/inflight/probe.json && rm /home/ubuntu/work/etho/ops/inflight/probe.json && echo "ok: inflight shared both ways"
# the ledger is append-only for everyone, and appending still works (it adds nothing: an empty write)
lsattr /home/ubuntu/work/etho/ops/spend.jsonl | grep -q -- '-a-' && echo "ok: spend.jsonl is append-only"
```

What the same user can do outside the service (raw file permissions; all must be denied):

```
sudo -u etho-app ls /home/ubuntu                        # Permission denied
sudo -u etho-app test -r /home/ubuntu/work/etho/.git/HEAD && echo "NOT OK" || echo "denied"
```

Bubblewrap, the endpoints and the exposure score:

```
sudo sh -c 'curl -s -H "X-Etho-Admin: $(cat /etc/etho-app/access_code)" http://127.0.0.1:8000/api/health'   # "sandbox": {"mode": "bwrap", ...}  <- the definitive bwrap check
systemd-analyze security etho-app --no-pager | tail -3
curl -s -X POST -H 'Content-Type: application/json' -d '{"code":"wrong-wrong-wrong"}' http://127.0.0.1:8000/api/access   # 401 wrong_code
curl -s -X POST http://127.0.0.1:8000/api/jobs -H 'Content-Length: 1' -d x       # 403 read_only (or 401 access_required in live mode)
```

If an "ok" line is missing or a "NOT OK" line prints (or `nsenter`/`setpriv` is not available), stop and fix before
publishing. If bubblewrap is refused for the service user, health shows `read-only` and nothing can be uploaded; check
`sudo journalctl -u etho-app | grep sandbox` and rerun the pre-flight in section 5.

Through the public URL once Funnel is on (section 7): `curl -s https://<url>/api/health`.

## 9. Health watchdog (restarts a hung app)

systemd restarts a crashed process; this also restarts one that is alive but not answering. Every minute a root timer asks
`GET /api/health` on localhost (the public, unauthenticated view). If there is no good answer within 10 s, twice in a
row, it restarts `etho-app`; never more than once per 2 minutes. A service that is stopped (by you, or by the stop timer
in section 12) is left alone. It is modelled on `bin/lead-watchdog`, but it never gives up: the site must stay up.

```
sudo install -m 0755 /home/ubuntu/work/etho/bin/app-watchdog /usr/local/sbin/etho-app-watchdog   # a root-owned copy
sudo tee /etc/systemd/system/etho-app-watchdog.service >/dev/null <<'EOF'
[Unit]
Description=Restart the Etho web app if its health check hangs or fails twice in a row

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/etho-app-watchdog
Environment=ETHO_WATCHDOG_LOG=/home/ubuntu/work/etho/ops/app-watchdog.log
EOF
sudo tee /etc/systemd/system/etho-app-watchdog.timer >/dev/null <<'EOF'
[Unit]
Description=Etho web app health watchdog, every minute

[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
AccuracySec=10s

[Install]
WantedBy=timers.target
EOF
sudo systemd-analyze verify /etc/systemd/system/etho-app-watchdog.service /etc/systemd/system/etho-app-watchdog.timer
sudo systemctl daemon-reload
sudo systemctl enable --now etho-app-watchdog.timer
systemctl list-timers etho-app-watchdog.timer --no-pager
```

Tuning (set `Environment=` lines in the service): `ETHO_WATCHDOG_TIMEOUT` (10), `ETHO_WATCHDOG_FAILS` (2),
`ETHO_WATCHDOG_MIN_GAP` (120), `ETHO_HEALTH_URL`. Logs: `journalctl -t etho-app-watchdog` and
`ops/app-watchdog.log`. Pausing it: section 15. Re-run the `install` line after
changing `bin/app-watchdog`. A read-only, killed or expired app answers health with `ok: true`, so it is not restarted.
`tests/test_app_watchdog_uptime.py` runs the script against a fake server and a fake `systemctl`.

## 10. External uptime check (the public URL, every 15 minutes)

`bin/uptime-check` fetches `<public URL>/api/health` with a 10 s timeout and appends
`{time, url, status, latency_s, mode, ok, path, ip, error}` to `ops/uptime.jsonl`. The URL comes from
`ops/public-url` (or `$ETHO_PUBLIC_URL`). Install the cron entry as `ubuntu`:

```
( crontab -l 2>/dev/null; echo '*/15 * * * * /home/ubuntu/work/etho/bin/uptime-check >> /home/ubuntu/work/etho/ops/uptime.cron.log 2>&1' ) | crontab -
# or, idempotently, together with the keepalive and the other cron entries: bin/install-autostart
bin/uptime-check              # one check now; exit 0 = healthy
bin/uptime-report             # summary for a checkpoint: checks, availability, outages, latency, modes
bin/uptime-report --since 24h
bin/uptime-report --since 2026-11-04T21:00:00-05:00      # from submission
```

**Does it really test the internet path?** A node on the tailnet resolves its own `*.ts.net` name through MagicDNS to
its *tailnet* address, so a plain fetch from this VM would test the VM against itself and never touch Funnel's public
ingress. The check therefore resolves the host name through a public resolver (DNS over HTTPS to 1.1.1.1) and connects
to that public address with the right TLS name (`path: "public-dns"` and the `ip` in each row), using GET.
Verified on this VM on Oct 30: `getent hosts etho-vm.tail9158ab.ts.net` gives `100.79.59.112` (the tailnet address,
the short-circuit the check avoids), while the check row has `ip 199.38.181.54`, `path public-dns`, `ok true`, and
`ip route get 199.38.181.54` answers `via 10.0.0.1 dev enp0s5 src 10.0.0.221`, i.e. it leaves through the VM's normal
interface, not `tailscale0`. Whether the reply really comes back through Funnel's ingress rather than being answered
locally is inferred from that route and the public address, not observed on the wire. An outside watcher is still the
only fully independent check. Free options that need no code from us (I signed up for none; creating an account is your
decision): UptimeRobot's free plan (5 minute checks, alert by email), Healthchecks.io or Better Stack's free tiers, or a
scheduled GitHub Actions workflow once the repo is public (best-effort, every 5 minutes at the fastest). Any of them can
point at `https://<url>/api/health` and treat `"ok":true` as up.

**Down alerts.** After each check `etho.app.uptime` writes to `ops/notify.jsonl` (untracked, one JSON row per line,
`{time, source, kind, message, sent}`): `kind "down"` on the second consecutive failed check (once per outage) and
`kind "up"` on the first ok check after two or more failures. Whoever reads the file flips `sent`.
`ops/uptime.jsonl`, `ops/notify.jsonl`, `ops/funnel.jsonl`, `ops/funnel-off` and the other runtime state here are in
`.gitignore`: they are per-machine logs and markers, not history worth keeping.

**Funnel keepalive** (`bin/funnel-keepalive`, cron every 5 minutes, installed by `bin/install-autostart`). It reads
`tailscale funnel status --json` (no sudo). If Funnel is not publicly serving `/` to `http://127.0.0.1:8000`, it runs
`sudo -n /usr/bin/tailscale funnel --bg 8000` (needs the sudoers rule below), appends to `ops/funnel.jsonl` and writes a
`ops/notify.jsonl` row ("Funnel was off; re-enabled", or "Funnel off and could not re-enable: ..."). At most one
re-enable attempt per 10 minutes (state in `ops/.funnel-state.json`); a failing `sudo -n` is logged and notified, never
retried in a loop. A failing re-enable notifies the first time, when the error changes, then at most every 6 h (state in
`ops/.funnel-state.json`, cleared once Funnel serves again). If the status command itself fails it only logs, and does
not re-enable blindly; three consecutive failures notify (same rate limit).
**`ops/funnel-off` is the operator's offline marker: while it exists the keepalive does nothing.** Every "stop publishing"
instruction (section 15) therefore starts with `touch /home/ubuntu/work/etho/ops/funnel-off`, before the reset.

Root cause of the two outages (Oct 28 22:41 UTC and Oct 29 05:35 UTC): each was a manual `sudo tailscale funnel reset`
typed in an SSH session (the second right after a login), not a tailscaled restart (tailscaled has run since Oct 28
22:31 with 0 restarts, and there was no reboot). Funnel was back on Oct 30 04:03 UTC. Funnel configuration set with
`--bg` persists across tailscaled restarts per Tailscale's documentation; that has not been tested with a reboot here.

Shape of `tailscale funnel status --json` on this machine with Funnel on (the keepalive codes against it):
`{"TCP": {"443": {"HTTPS": true}}, "Web": {"etho-vm.tail9158ab.ts.net:443": {"Handlers": {"/": {"Proxy":
"http://127.0.0.1:8000"}}}}, "AllowFunnel": {"etho-vm.tail9158ab.ts.net:443": true}}`. "Serving" means a host:port that
is `true` under `AllowFunnel` and whose `/` handler proxies to 127.0.0.1:8000. The Funnel-off shape was not captured
(Funnel is live and was not reset to look); the code treats a missing `Web`/`AllowFunnel` as off.

Sudoers rule for the keepalive (Aniket; validated install like section 14, but in a separate file, with exactly one command):

```
f=$(mktemp)
echo 'ubuntu ALL=(root) NOPASSWD: /usr/bin/tailscale funnel --bg 8000' > "$f"
sudo visudo -cf "$f"
sudo install -m 0440 -o root -g root "$f" /etc/sudoers.d/etho-funnel
rm "$f"
sudo visudo -c
```

Verify (Aniket, 2026-10-30). `sudo -n -l <command>` cannot show whether a command needs a password: `ubuntu` is
still permitted everything WITH a password, and once any NOPASSWD rule exists listing needs no password (sudo's
`listpw` default), so it always prints the command. Check instead:

```
sudo -k; sudo -n /usr/bin/true && echo "BAD: no password needed" || echo "GOOD: password required"
sudo -k; sudo -n -l   # expect exactly two NOPASSWD lines (systemctl restart etho-app; tailscale funnel --bg 8000) and no "NOPASSWD: ALL"
```
Expected `sudo -n -l` rules, exactly (Aniket's result on 2026-10-30, with GOOD from the first line):
```
    (ALL : ALL) ALL
    (root) NOPASSWD: /usr/bin/systemctl restart etho-app
    (root) NOPASSWD: /usr/bin/tailscale funnel --bg 8000
```
The first rule still needs your password; anything else marked NOPASSWD, or "NOPASSWD: ALL", is wrong.

Until it exists, the keepalive can only notify. Removing the rule: section 15.

## 11. Running it (operations)

| I want to | Do |
|---|---|
| stop live work, delete uploads, stop the public URL, take it offline, remove a sudo rule | **section 15 (Emergency takedown)**, one command at a time |
| refuse new work but let admitted jobs finish | `touch /home/ubuntu/work/etho/ops/app-readonly`. Undo: `rm` it. |
| see mode, caps, queue, last retention run | the admin health command in the introduction |
| see uptime | `bin/uptime-report --since 24h` |
| see spend by tag | `bin/spend` (the app's rows are in the shared ledger, caller `pub:job:<id>\|...`, tag `demo-reserve`) |
| change the access code | write the new value to `/etc/etho-app/access_code` |
| change caps or the end date | edit `/etc/etho-app/app.env`, then `sudo systemctl restart etho-app` |
| put it back online | `rm /home/ubuntu/work/etho/ops/funnel-off`, start the service, `sudo tailscale funnel --bg 8000` |
| see operator alerts | `tail ops/notify.jsonl` (rows `{time, source, kind, message, sent}`; sources `uptime` and `funnel`) |
| read the log | `sudo journalctl -u etho-app -f` |
| deploy new code | Claude may restart after a merge (sudoers rule below); **Nov 4-9 ET it pushes Aniket a notification before any restart**. The unit runs the checkout in place. Pull or merge (mind the `chattr +a` note in section 3), `uv sync`, then `sudo systemctl restart etho-app`. Do not edit tracked files in the checkout while it serves (modules loaded lazily on the first job would pick the edit up). |

The caps are computed from the ledger, so they persist across restarts, and each admitted job reserves its ceiling.

## 12. Optional: stop everything after the finals

Moved to section 15.7 (it schedules a takedown, so it lives with the destructive commands).

## 13. What is and is not enforced

Enforced (each has a test; see `tests/test_app_hosting.py`, `test_app_ledger_robust.py`, `test_app_retention_races.py`,
`test_app_ui_fallback.py`, `test_app_confine.py`, `test_app_watchdog_uptime.py`): the access code on every spending route
(any POST not on a short safe list is closed by default); read-only, kill switch and expiry; constant-time code
comparison, throttling of wrong codes only; signed, expiring, code-bound cookies; a $3 rolling-24 h cap ($8 in the Nov 4-9 ET judging window) and a $30 total
cap over the app's own ledger rows (prefix `pub:job:`), with the per-job ceiling reserved at admission and again per
call in flight; a ledger row the app cannot read closes the caps instead of being skipped; 24 h retention by a timer
that never deletes a directory another project, a showcase or the benchmark uses; ffprobe/ffmpeg on uploads under
bubblewrap; a friendly message in the UI for every refusal; no other visitor's job id in any page.

Not deleted or not guaranteed, in full:

* **Meta Files API copies** are deleted through the gateway (`Gateway.delete_file`, `DELETE /v1/files/{file_id}`, from
  the public docs at https://dev.meta.ai/docs/file-handling) right after the tracking stage, again when the job ends
  (failed or cancelled jobs), and once more by the 24 h retention pass. Only ids that still fail at retention time are
  listed, ids only, in `/var/lib/etho-app/data/meta_files_undeleted.jsonl`. Files do not expire by default
  ("Uploaded files don't expire, so stored data only grows until you remove it"), so a failed delete leaves the clip on
  Meta's side until someone deletes it. The delete call is untested against the live API. It only runs when the app has
  its own gateway cache (public mode), because forgetting the cached id would otherwise break SAM cache hits.
  A side effect: repeat uploads of the same clip re-upload and re-run SAM (the file id is part of the SAM cache key), so
  they are no longer free cache hits.
* **Meta's and Fireworks' own logs.** Meta's data commitments say "Meta does not use your Content from Standard Services
  to train Meta Models" and "Meta will retain your Content as described in the Terms and Privacy Policy"
  (https://dev.meta.ai/legal/commitments). What is retained after a file delete is not stated there.
* **Ledger rows** in `ops/spend.jsonl` (job id, time, model, tokens, cost, SAM phrase, request hash; no video or text) are
  kept: the caps are computed from them. **Server logs** (`journalctl`) keep request lines with job links.
* **Detector cache**: code written for a definition, no video or user data. It is shared between visitors on purpose
  (the same definition reuses it), and the job id stored inside an entry is never shown.
* **Capability links.** A project URL is an unguessable 48-bit id; anyone who has it can view and cancel that job.
  There is no listing to find them by.
* **Abandoned reservations.** If the process dies mid-call, the gateway books the call's worst-case estimate under
  its tag with no caller. The app counts those on its own tag toward its caps (safe side), including ones that
  belong to your own jobs on `demo-reserve`.
* **Shared bucket without a real client IP** (section 7, check 1).
* **The videos' pixels reach Meta and Fireworks** as the privacy notice says; this site cannot recall them.

## 14. Passwordless restart for the lead session (Aniket, 2026-10-29)
`/etc/sudoers.d/etho-app-restart` lets the `ubuntu` user run exactly `/usr/bin/systemctl restart etho-app` with no
password, and nothing else (no stop, no other unit, no extra arguments). Claude restarts the app after merges. During
the judging window (Nov 4-9 ET) it sends Aniket a push notification before any restart. Verify both passwordless rules with the two commands in section 10 (not with `sudo -n -l <command>`). Removing the rule: section 15.

## 15. Emergency takedown (destructive)

**WARNING: every command in this section stops, unpublishes or deletes something. Run them one at a time, typed or
copied on their own, only when you mean it. Never paste one together with a setup block, and never copy past the end
of a code block.** Both Funnel outages (Oct 28 22:41 and Oct 29 05:35 UTC) were `sudo tailscale funnel reset` pasted
at the end of a setup block. Each command below is in its own block, and nothing here is needed for setup.

### 15.1 Stop all live work now, keep the showcase up
No sudo, no restart; cancels the running job at its next model call, and clears the queue. Undo: `rm` the file.
```
touch /home/ubuntu/work/etho/ops/app-kill
```

### 15.2 Stop publishing the public URL (the app keeps running locally)
The marker comes first, or the keepalive (section 10) turns Funnel back on within 5 minutes.
```
touch /home/ubuntu/work/etho/ops/funnel-off
```
```
sudo tailscale funnel reset
```
Undo: `rm /home/ubuntu/work/etho/ops/funnel-off`, then `sudo tailscale funnel --bg 8000` (or wait for the keepalive).

### 15.3 Take everything offline
```
touch /home/ubuntu/work/etho/ops/funnel-off
```
```
sudo systemctl disable --now etho-app etho-app-watchdog.timer
```
```
sudo tailscale funnel reset
```
Undo: `rm` the marker, `sudo systemctl enable --now etho-app etho-app-watchdog.timer`, `sudo tailscale funnel --bg 8000`.

### 15.4 Delete every upload right now
```
sudo systemctl stop etho-app && sudo rm -rf /var/lib/etho-app/data/jobs /var/lib/etho-app/data/gateway_cache /var/lib/etho-app/data/work /var/lib/etho-app/data/tmp && sudo systemctl start etho-app
```

### 15.5 Pause the health watchdog
```
sudo systemctl stop etho-app-watchdog.timer
```
Undo: `sudo systemctl start etho-app-watchdog.timer`.

### 15.6 Remove a passwordless sudo rule
```
sudo rm /etc/sudoers.d/etho-funnel
```
```
sudo rm /etc/sudoers.d/etho-app-restart
```

### 15.7 Scheduled stop after the finals (optional, off by default)

"Auto-off" (`ETHO_APP_LIVE_UNTIL`) only switches live mode off; the read-only showcase and the tunnel stay up. If you
want the service and the public URL themselves to stop on a date, install this timer. It is not installed by any step
above. Its default time is **Nov 11 00:05 America/New_York**, after the Nov 9 finals and the Nov 10 23:59:59 live
cut-off; change `OnCalendar=` before enabling if you want a different moment, and do not set it earlier than the finals.

```
sudo tee /etc/systemd/system/etho-app-stop.service >/dev/null <<'EOF'
[Unit]
Description=Stop the Etho web app and its public URL (the judging window is over)

[Service]
Type=oneshot
ExecStart=/bin/sh -c '/usr/bin/touch /home/ubuntu/work/etho/ops/funnel-off; /usr/bin/systemctl disable --now etho-app-watchdog.timer etho-app.service; /usr/bin/tailscale funnel reset || true'
EOF
sudo tee /etc/systemd/system/etho-app-stop.timer >/dev/null <<'EOF'
[Unit]
Description=Stop the Etho web app after the finals

[Timer]
OnCalendar=2026-11-11 00:05:00 America/New_York
Persistent=false

[Install]
WantedBy=timers.target
EOF
systemd-analyze calendar "2026-11-11 00:05:00 America/New_York"      # shows the instant in UTC (05:05 UTC) and "From now"
sudo systemctl daemon-reload
sudo systemctl enable --now etho-app-stop.timer
systemctl list-timers etho-app-stop.timer --no-pager
```

Cancel it with `sudo systemctl disable --now etho-app-stop.timer`. The watchdog leaves a stopped service alone, so it
does not undo the stop.
