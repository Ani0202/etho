#!/usr/bin/env python3
"""PreToolUse guard for Bash: everything model-related goes through etho/gateway.

Blocks (exit 2, reason on stderr):
  - commands that mention a model-provider host directly (curl/wget/python -c ... to the API),
    except read-only searches (grep/rg/git grep|log|diff|show) over the codebase;
  - force pushes, in any spelling the permission rules can miss (git -C . push -f, +refspec, ...);
  - reading the secrets or Codex config directories from the shell.
"""
import json
import re
import shlex
import sys

HOSTS = re.compile(r"api\.meta\.ai|inference\.baseten\.co|api\.fireworks\.ai|api\.together\.(xyz|ai)", re.I)
READ_ONLY_FIRST = {"grep", "rg", "egrep", "fgrep"}
READ_ONLY_GIT = {"grep", "log", "diff", "show", "blame"}
SECRET_PATHS = re.compile(r"\.config/etho|\.codex/(auth|config)|secrets\.env")


def block(msg: str) -> None:
    print(f"Blocked by .claude/hooks/guard-bash.py: {msg}", file=sys.stderr)
    sys.exit(2)


def segments(cmd: str):
    for seg in re.split(r"&&|\|\||[;|&\n]|\$\(|`", cmd):
        seg = seg.strip()
        if not seg:
            continue
        try:
            yield shlex.split(seg)
        except ValueError:
            yield seg.split()


def main() -> None:
    call = json.load(sys.stdin)
    if call.get("tool_name") != "Bash":
        return
    cmd = (call.get("tool_input") or {}).get("command", "")

    for toks in segments(cmd):
        if not toks:
            continue
        text = " ".join(toks)
        if HOSTS.search(text):
            first = toks[0].rsplit("/", 1)[-1]
            ok = first in READ_ONLY_FIRST or (first == "git" and len(toks) > 1 and toks[1] in READ_ONLY_GIT)
            if not ok:
                block("direct calls to model providers are not allowed; use etho/gateway or bin/{muse,open,fw,sam}.")
        if SECRET_PATHS.search(text) and not (toks[0] in READ_ONLY_FIRST and "etho/" not in text):
            block("the secrets file and ~/.codex are off-limits to the shell; the gateway loads keys itself.")
        if "git" in toks:
            rest = toks[toks.index("git"):]
            if "push" in rest:
                after = rest[rest.index("push") + 1:]
                if any(t in ("-f", "--force") or t.startswith("--force") or t.startswith("+")
                       or (re.fullmatch(r"-[a-zA-Z]*f[a-zA-Z]*", t) is not None) for t in after):
                    block("force push is never allowed on Etho.")


if __name__ == "__main__":
    main()
