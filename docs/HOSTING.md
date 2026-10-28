# Hosting the Etho web app during judging (ADR-003, approved 2026-10-28)

Aniket runs the sudo commands below; the code that makes them work is on branch `worktree-agent-ab0d7665ca99e742a`
(merge it first). Nothing here was run with sudo by the agent that wrote it: **the commands are untested as written**,
so the checks in section 7 matter. The app itself was run in public mode against temp directories (no keys, no
provider calls) to confirm that it starts, reports its mode, and refuses uploads without the code.

What the visitor sees:

| Mode | How you get there | What works |
|---|---|---|
| `read-only` | `ETHO_APP_READ_ONLY=1`, or the file `ops/app-readonly`, or (public only) no usable access code, or the rule sandbox is not bubblewrap | Browse precomputed showcases, reports, audit trails. Uploads and revisions (a showcase revision too, it spends money) are refused with 403. |
| `live` | none of the above | Everything above, plus uploads and revisions for a visitor who entered the access code. |
| `killed` | the file `ops/app-kill` exists | Like read-only, and the running job is cancelled at its next model call and the queue is cancelled. |
| `expired` | after `ETHO_APP_LIVE_UNTIL` (default 2026-11-10 23:59:59 America/New_York) | Like killed. Read-only browsing stays up. |

`GET /api/health` shows the mode, why, the sandbox mode, the caps left, the queue and the last retention run. It
contains no secrets and no visitor data.

## 1. Before you start

On this VM (as `ubuntu`), after merging the branch:

```
cd /home/ubuntu/work/etho
git log --oneline -1            # confirm the merge is there
uv sync                         # the service runs .venv directly; nothing is installed at start
make check
```

`setfacl` is not installed on this VM (checked), and neither is `tailscale`. Install ACL tools now:

```
sudo apt-get update && sudo apt-get install -y acl
```

## 2. The unix user and its directories

```
sudo useradd --system --user-group --create-home --home-dir /var/lib/etho-app --shell /usr/sbin/nologin etho-app
sudo install -d -o etho-app -g etho-app -m 0700 /var/lib/etho-app/data
sudo install -d -o root -g etho-app -m 0750 /etc/etho-app
```

`/var/lib/etho-app/data` holds everything the app writes: uploads (`jobs/`), its own gateway response cache
(`gateway_cache/`), its own chunk cache (`work/`), temp files (`tmp/`), the detector cache and
`meta_files_undeleted.jsonl`. All of it is deleted by the retention timer after 24 h, except the detector cache (code
only, no video or user data) and the id list of Meta files.

## 3. The shared ledger (ACLs) and what the app can see of `/home/ubuntu`

The app has to append to the same ledger as everything else, so there is one set of caps. Only these four things are
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

The code, the venv and the showcases are not granted by ACL. `/home/ubuntu` is mode 750, so `etho-app` cannot enter it
at all, and the unit (section 5) gives the service a private, empty `/home` with only these paths bind-mounted into it:
read-only `etho/`, `configs/`, `.venv/`, `data/showcase/` of the checkout, and read-write `ops/` (where DAC, i.e. the
ACLs above, decides which files it may touch). Everything else under `/home/ubuntu` (`~/.config`, `~/.claude`,
`~/.codex`, `~/.ssh`, the git credentials, `.git`, `eval/`, `docs/`) does not exist for the service. Showcases are read
in place on purpose: a packaged showcase stores the absolute path of its own `source.mp4` in `tracking.json`, so it
cannot be moved.

## 4. Secrets: you fill these in yourself

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

The access code for live use (this is what goes in the submission notes; it is never on the public page). Generate it
and read it yourself; at least 12 characters are required, 16 or more random ones are recommended:

```
sudo sh -c 'umask 077; head -c 24 /dev/urandom | base64 | tr -d "=+/" | cut -c1-24 > /etc/etho-app/access_code; chown root:etho-app /etc/etho-app/access_code; chmod 0640 /etc/etho-app/access_code'
sudo cat /etc/etho-app/access_code
```

To rotate it later: write a new value to the same file. No restart is needed, and every cookie signed with the old code
stops working at once. For a read-only-only deployment (section 6, step A) you can skip `keys.env` and the code file.

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
ETHO_APP_DAILY_CAP_USD=3
ETHO_APP_TOTAL_CAP_USD=20
ETHO_APP_LIVE_UNTIL=2026-11-10T23:59:59-05:00
ETHO_APP_RETENTION_H=24
ETHO_APP_MAX_UPLOAD_MB=50
ETHO_APP_MAX_DURATION_S=120
ETHO_APP_RATE_JOBS=3
ETHO_APP_READ_ONLY=1
EOF
sudo chown root:etho-app /etc/etho-app/app.env && sudo chmod 0640 /etc/etho-app/app.env
```

`ETHO_APP_READ_ONLY=1` is deliberate: the first start is read-only (section 6). Every value above is also the
default for `ETHO_APP_PUBLIC=1`; they are written out so that what runs is visible. `demo-reserve` is locked by the
gateway until Nov 3 05:00 UTC (`DEMO_RESERVE_OPENS_UTC` in `etho/gateway/prices.py`), so before then live jobs fail at the gateway
without spending anything.

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
# what it can write: its own state directory (and ops/ above, as far as the ACLs allow)
ProtectSystem=strict
ReadWritePaths=/var/lib/etho-app
# hardening
NoNewPrivileges=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
ProtectClock=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
LockPersonality=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
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
sudo systemctl enable --now etho-app
sudo systemctl status etho-app --no-pager
sudo journalctl -u etho-app -n 30 --no-pager
curl -s http://127.0.0.1:8000/api/health
```

If the unit fails with a mount error, `journalctl -u etho-app` names the path. A missing source path (for example
`data/showcase` before any showcase is packaged) is the usual cause: create the directory, or prefix that entry with `-`
in `BindReadOnlyPaths=` (`-/home/ubuntu/...`), which systemd documents as "ignore if missing".
Fallback if `ProtectHome=tmpfs` with binds is refused on this systemd (255): use `ProtectHome=read-only`, delete the
`BindReadOnlyPaths=` and `BindPaths=` lines, add `/home/ubuntu/work/etho/ops` to `ReadWritePaths=`, and give the app read
access with ACLs instead: `sudo setfacl -m u:etho-app:--x /home/ubuntu /home/ubuntu/work /home/ubuntu/work/etho
/home/ubuntu/work/etho/data /home/ubuntu/work/etho/ops` and `sudo setfacl -R -m u:etho-app:rX
/home/ubuntu/work/etho/etho /home/ubuntu/work/etho/configs /home/ubuntu/work/etho/.venv
/home/ubuntu/work/etho/data/showcase`. In that mode `~/.config` and `~/.claude` are mode 775 on this VM, so you must also
deny them: `sudo setfacl -m u:etho-app:--- /home/ubuntu/.config /home/ubuntu/.claude /home/ubuntu/.local
/home/ubuntu/work/etho/.git /home/ubuntu/work/etho/.claude /home/ubuntu/work/etho/eval /home/ubuntu/work/etho/docs`
(and anything else readable that you find with `sudo -u etho-app find /home/ubuntu -readable 2>/dev/null | head`).
The tmpfs variant above is the default because it needs none of that: what is not bound does not exist.

`Restart=on-failure` with `StartLimitIntervalSec=0` restarts it forever, five seconds after any crash. A restart marks
jobs that were running as failed (it never resumes and re-spends), and signs every visitor out of live mode (the cookie
key is per process).

The app listens on 127.0.0.1:8000 only. `--forwarded-allow-ips 127.0.0.1` makes uvicorn believe `X-Forwarded-For` only
from the proxy on this machine. Never widen it to `*`: the rate limit and the code throttle key on that address.

## 6. Going public, in this order

**A. Read-only floor (now, any day).** Keep `ETHO_APP_READ_ONLY=1` in `app.env`. This needs no keys at all and can
spend nothing: even if someone forges a cookie, every spending route answers 403 first.

**B. Tailscale Funnel for a stable URL.** Tailscale is not installed here; installing it, and enabling HTTPS and
Funnel in your tailnet, are your decisions (admin console: DNS > HTTPS certificates; Access controls: a `funnel` node
attribute for this machine).

```
curl -fsSL https://tailscale.com/install.sh | sudo sh      # the official installer; read it first if you prefer
sudo tailscale up
sudo tailscale funnel --bg 8000
tailscale funnel status                                    # prints https://<machine>.<tailnet>.ts.net
```

To stop publishing: `sudo tailscale funnel reset`. (CLI syntax is from Tailscale's docs as recalled on Oct 28 and
was not run here. If `--bg` is not accepted, `tailscale version` and `tailscale funnel --help` show the syntax for
your release.)

Two things that must be checked once the URL is up, because they were unverifiable in advance:

1. **Real client IP.** From a phone on mobile data, open `https://<url>/api/health` and read `client_seen_as`. If it
   shows the phone's address, the per-client rate limit and the code throttle work per visitor. If it shows
   `127.0.0.1` or a `100.x` address, every visitor shares one bucket: three jobs an hour in total and five wrong
   codes lock everyone out for 15 minutes. That is safe for spend but bad for access; raise `ETHO_APP_RATE_JOBS` in
   that case. The caps do not depend on the client address.
2. **Server-sent events** (the job progress page). Start a short live job from the public URL and watch the stage list
   advance. The app sends an event every second and sets `X-Accel-Buffering: no`. If the proxy buffers them, the
   page still works: the progress bar stalls, and reloading the page shows the finished result. Do not use Cloudflare
   Quick Tunnels: they do not carry SSE.

Funnel's bandwidth limits are not published, which is why uploads are capped at 50 MB.

**C. Live mode, once `demo-reserve` opens (Nov 3 05:00 UTC) and the demo is recorded.** Create the keys and code
file (section 4), then:

```
sudo sed -i '/^ETHO_APP_READ_ONLY=/d' /etc/etho-app/app.env
sudo systemctl restart etho-app
curl -s http://127.0.0.1:8000/api/health        # mode must say "live"
```

Live mode needs all of: a code of at least 12 characters in the code file, bubblewrap active as the service user
(health shows `"sandbox": {"mode": "bwrap"}`), and none of the switches below. If any one is missing the app stays
read-only and `reasons` in health says which. Put the access code in the submission notes by hand.

## 7. Checks to run once (each states what you should see)

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
```

What the same user can do outside the service (raw file permissions; all must be denied):

```
sudo -u etho-app ls /home/ubuntu                        # Permission denied
sudo -u etho-app test -r /home/ubuntu/work/etho/.git/HEAD && echo "NOT OK" || echo "denied"
```

Bubblewrap, the endpoints and the exposure score:

```
curl -s http://127.0.0.1:8000/api/health            # "sandbox": {"mode": "bwrap", ...}   <- the definitive bwrap check
systemd-analyze security etho-app --no-pager | tail -3
curl -s -X POST -H 'Content-Type: application/json' -d '{"code":"wrong-wrong-wrong"}' http://127.0.0.1:8000/api/access   # 401 wrong_code
curl -s -X POST http://127.0.0.1:8000/api/jobs -H 'Content-Length: 1' -d x       # 403 read_only (or 401 access_required in live mode)
```

If an "ok" line is missing or a "NOT OK" line prints (or `nsenter`/`setpriv` is not available), stop and fix before publishing. If bubblewrap is refused for the
service user, `/api/health` shows `read-only` with a reason and nothing can be uploaded; check
`sudo journalctl -u etho-app | grep sandbox`.

Through the public URL once Funnel is on (section 6): `curl -s https://<url>/api/health`.

## 8. Running it (operations)

| I want to | Do |
|---|---|
| stop all live work now, keep the showcase up | `touch /home/ubuntu/work/etho/ops/app-kill` (no sudo, no restart; cancels the running job at its next model call and the queue). Undo: `rm` it. |
| refuse new work but let admitted jobs finish | `touch /home/ubuntu/work/etho/ops/app-readonly`. Undo: `rm` it. |
| see mode, caps, queue, last retention run | `curl -s http://127.0.0.1:8000/api/health` |
| see spend by tag | `bin/spend` (the app's rows are in the shared ledger, caller `job:<id>\|...`, tag `demo-reserve`) |
| change the access code | write the new value to `/etc/etho-app/access_code` |
| change caps or the end date | edit `/etc/etho-app/app.env`, then `sudo systemctl restart etho-app` |
| delete every upload right now | `sudo systemctl stop etho-app && sudo rm -rf /var/lib/etho-app/data/jobs /var/lib/etho-app/data/gateway_cache /var/lib/etho-app/data/work /var/lib/etho-app/data/tmp && sudo systemctl start etho-app` |
| take it offline | `sudo systemctl disable --now etho-app && sudo tailscale funnel reset` |
| read the log | `sudo journalctl -u etho-app -f` |
| deploy new code | the unit runs the checkout in place. Pull or merge, `uv sync`, then `sudo systemctl restart etho-app`. Do not edit tracked files in the checkout while it serves (modules loaded lazily on the first job would pick the edit up). |

The caps are computed from the ledger, so they persist across restarts, and each admitted job reserves its ceiling.
At $3 per rolling 24 hours a 60 s upload (estimate about $0.60, ceiling about $1.20) leaves room for two jobs in the
queue at a time; the measured cost of a 60 s clip is $0.23 to $0.36.

## 9. What is and is not enforced

Enforced (each has a test in `tests/test_app_hosting.py`): the access code on every spending route (any POST not on a
short safe list is closed by default); read-only, kill switch and expiry; constant-time code comparison and per-client
and global throttling of wrong codes; signed, expiring, code-bound cookies; a $3 rolling-24 h cap and a $20 total cap
over the app's own ledger rows, with the per-job ceiling reserved at admission and again per call in flight; 24 h
retention by a timer; no other visitor's job id in any page.

Not deleted or not guaranteed, in full:

* **Meta Files API copies.** The gateway has no delete call, and its surface was not extended (no direct HTTP). Each
  SAM clip uploaded for a job stays on Meta's side under Meta's retention. The ids are appended to
  `/var/lib/etho-app/data/meta_files_undeleted.jsonl` (ids only) at deletion time, so they can be deleted by hand if
  Meta's API has a delete call. The privacy notice on the page says this.
* **Fireworks and Meta logs** of the frame strips and prompts sent to them are theirs.
* **Ledger rows** in `ops/spend.jsonl` (job id, time, model, tokens, cost, SAM phrase, request hash; no video or text) are
  kept: the caps are computed from them.
* **Detector cache**: code written for a definition, no video or user data. It is shared between visitors on purpose,
  and the job id stored inside an entry is never shown.
* **Capability links.** A project URL is an unguessable 48-bit id; anyone who has it can view and cancel that job.
  There is no listing to find them by.
* **Abandoned reservations.** If the process dies mid-call, the gateway books the call's worst-case estimate under
  its tag with no caller. The app counts those on its own tag toward its caps (safe side), including the ones that
  belong to your own jobs on `demo-reserve`.
* **Shared bucket without a real client IP** (section 6, check 1).
* **ffmpeg/ffprobe** on uploaded video run as the service user with hardened flags but outside bubblewrap; the user can
  write only its own directories.
