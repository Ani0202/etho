"""bin/install-autostart replaces only its own two cron lines and keeps every other line (notably the lead-watchdog
entries) exactly as it was. Runs the real script against a fake `crontab` that stores the table in a file."""
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "install-autostart"
UPTIME = f"*/15 * * * * {ROOT}/bin/uptime-check >> {ROOT}/ops/uptime.cron.log 2>&1"
FUNNEL = f"*/5 * * * * {ROOT}/bin/funnel-keepalive >> {ROOT}/ops/funnel-keepalive.log 2>&1"
FAKE = """#!/usr/bin/env bash
# fake crontab: -l prints the table (or the real "no crontab" error), - replaces it from stdin
tab="$FAKE_CRONTAB_FILE"
case "$1" in
  -l) [ -n "${FAKE_CRONTAB_FAIL:-}" ] && { echo "crontab: cannot open: Permission denied" >&2; exit 1; }
      [ -f "$tab" ] || { echo "no crontab for tester" >&2; exit 1; }
      cat "$tab" ;;
  -)  [ -n "${FAKE_CRONTAB_WRITE_FAIL:-}" ] && { cat >/dev/null; echo "crontab: errors in crontab file" >&2; exit 1; }
      cat > "$tab" ;;
  *)  exit 2 ;;
esac
"""
OTHER = [
    "*/5 * * * * /home/ubuntu/work/etho/bin/lead-watchdog >/dev/null 2>&1",
    "@reboot sleep 90 && /home/ubuntu/work/etho/bin/lead-watchdog >/dev/null 2>&1",
    "# a comment that mentions bin/uptime-check and bin/funnel-keepalive",
    f"# */15 * * * * {ROOT}/bin/uptime-check   (disabled by hand: comments are never ours)",
    "",
    "*/15 * * * * /srv/other-checkout/bin/uptime-check >> /tmp/x.log 2>&1",
    "@reboot sleep 60; cd /home/ubuntu/work/gemma && bin/start # gemma-autostart",
    "MAILTO=\"\"",
    # near misses: they mention our scripts' paths but are not our lines, so they must survive byte for byte
    f"*/15 * * * * /mnt{ROOT}/bin/uptime-check >> /tmp/m.log 2>&1",
    f"*/15 * * * * {ROOT}/bin/uptime-check.bak-run",
    f"*/5 * * * * {ROOT}/bin/uptime-check && {ROOT}/bin/funnel-keepalive",
    f"*/5 * * * * {ROOT}/bin/funnel-keepalive; echo done",
    f"@daily echo {ROOT}/bin/uptime-check",
    f"0 3 * * * cp {ROOT}/bin/funnel-keepalive /tmp/",
]


@pytest.fixture
def cron(tmp_path):
    fake = tmp_path / "crontab"
    fake.write_text(FAKE)
    fake.chmod(0o755)
    tab = tmp_path / "table"

    def run(**env):
        e = {**os.environ, "ETHO_CRONTAB": str(fake), "FAKE_CRONTAB_FILE": str(tab),
             "ETHO_CRONTAB_BACKUP": str(tmp_path / "crontab.bak"), **env}
        return subprocess.run([str(SCRIPT)], capture_output=True, text=True, env=e)

    return tab, run


def test_keeps_every_line_it_does_not_own_in_order_and_replaces_its_own(cron):
    tab, run = cron
    stale = f"*/30 * * * * {ROOT}/bin/uptime-check >> /old.log 2>&1"   # an older version of our own line
    tab.write_text("\n".join(OTHER[:3] + [stale] + OTHER[3:] + [FUNNEL]) + "\n\n")
    r = run()
    assert r.returncode == 0, r.stderr
    # our lines replace the first old one in place; the duplicate further down is dropped; nothing else moves
    assert tab.read_text() == "\n".join(OTHER[:3] + [UPTIME, FUNNEL] + OTHER[3:]) + "\n\n"
    assert f"kept {len(OTHER) + 1} other line(s)" in r.stdout


def test_rerunning_never_removes_or_duplicates_anything(cron):
    tab, run = cron
    tab.write_text("\n".join(OTHER) + "\n")
    assert run().returncode == 0
    first = tab.read_text()
    for _ in range(3):
        assert run().returncode == 0
    assert tab.read_text() == first
    lines = first.splitlines()
    assert lines[:len(OTHER)] == OTHER and lines.count(UPTIME) == 1 and lines.count(FUNNEL) == 1
    assert sum("bin/lead-watchdog" in x for x in lines) == 2


def test_up_to_date_crontab_is_left_byte_identical(cron):
    tab, run = cron
    text = "\n".join(OTHER[:2] + [UPTIME, FUNNEL] + OTHER[2:]) + "\n"
    tab.write_text(text)
    assert run().returncode == 0
    assert tab.read_text() == text


def test_no_crontab_yet_installs_just_the_two_lines(cron):
    tab, run = cron
    r = run()
    assert r.returncode == 0, r.stderr
    assert tab.read_text() == UPTIME + "\n" + FUNNEL + "\n"


def test_unreadable_crontab_changes_nothing(cron):
    tab, run = cron
    tab.write_text("\n".join(OTHER) + "\n")
    before = tab.read_text()
    r = run(FAKE_CRONTAB_FAIL="1")
    assert r.returncode == 1 and "nothing changed" in r.stderr
    assert tab.read_text() == before


def test_failed_write_exits_nonzero_and_leaves_the_table(cron):
    tab, run = cron
    tab.write_text("\n".join(OTHER) + "\n")
    before = tab.read_text()
    r = run(FAKE_CRONTAB_WRITE_FAIL="1")
    assert r.returncode != 0 and tab.read_text() == before


def test_empty_but_present_crontab(cron):
    tab, run = cron
    tab.write_text("")
    assert run().returncode == 0
    assert tab.read_text() == UPTIME + "\n" + FUNNEL + "\n"


def test_missing_final_newline_keeps_the_last_line(cron):
    tab, run = cron
    tab.write_text("\n".join(OTHER[:2]))           # no newline after the last line
    assert run().returncode == 0
    assert tab.read_text() == "\n".join(OTHER[:2]) + "\n" + UPTIME + "\n" + FUNNEL + "\n"


def test_backup_holds_the_previous_table(cron, tmp_path):
    tab, run = cron
    tab.write_text("\n".join(OTHER) + "\n")
    before = tab.read_text()
    assert run().returncode == 0
    assert (tmp_path / "crontab.bak").read_text() == before
