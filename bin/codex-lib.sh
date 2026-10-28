# shellcheck shell=bash
# Shared Codex runner for bin/codex-review, bin/codex-ask, bin/codex-build. Source it; don't run it.
#
#   codex_run <default_model> <fallback_model|-> <default_effort> <sandbox> <out_file> <log_file> <prompt>
#
# - CODEX_MODEL / CODEX_EFFORT override the defaults (an explicit CODEX_MODEL disables the fallback).
# - If the default model fails with a model error (unavailable, unknown, no access), retry once with
#   the fallback model at the same effort.
# - The first line of <out_file> records the model and effort that actually ran (read from Codex's
#   own stderr header), e.g. "codex model: gpt-6-astra | reasoning effort: xhigh".
# - Effort "ultra" (automatic task delegation) is never allowed on Etho. "max" is reserved for three
#   gates (see CLAUDE.md, Development).
codex_run() {
  local def_model=$1 fb_model=$2 def_effort=$3 sandbox=$4 out=$5 log=$6 prompt=$7
  local effort=${CODEX_EFFORT:-$def_effort}
  if [ "$effort" = "ultra" ]; then
    echo "codex_run: reasoning effort 'ultra' is not allowed on Etho" >&2
    return 2
  fi
  local models=("${CODEX_MODEL:-$def_model}")
  if [ -z "${CODEX_MODEL:-}" ] && [ "$fb_model" != "-" ] && [ -n "$fb_model" ]; then
    models+=("$fb_model")
  fi
  local m rc=0 note="" body="$out.body.$$"
  : > "$log"
  for m in "${models[@]}"; do
    rc=0
    echo "=== codex exec -m $m -c model_reasoning_effort=$effort ($(date -u +%FT%TZ))" >> "$log"
    codex exec --sandbox "$sandbox" -m "$m" -c model_reasoning_effort="$effort" "$prompt" \
      </dev/null > "$body" 2>> "$log" || rc=$?
    [ "$rc" -eq 0 ] && break
    # Fall back only on a model error: judge Codex's own final "ERROR:" line from this attempt,
    # never the transcript (prompts and tool output can mention model errors).
    local last_err
    last_err=$(sed -n "/^=== codex exec -m $m /,\$p" "$log" | grep '^ERROR:' | tail -1 || true)
    if printf '%s' "$last_err" | grep -qiE \
      "model_not_found|unknown model|unsupported model|invalid model|model .*(not (found|supported|available|exist)|does not exist|unavailable|no access|not allowed)|(do not|don't) have access to .*model"; then
      if [ "$m" != "${models[-1]}" ]; then
        note=" (fallback: $m failed with a model error)"
        echo "codex_run: $m failed with a model error; retrying once with ${models[-1]}" >&2
        continue
      fi
    fi
    break
  done
  local ran_model ran_effort
  ran_model=$(grep '^model:' "$log" | tail -1 | cut -d' ' -f2- || true)
  ran_effort=$(grep '^reasoning effort:' "$log" | tail -1 | cut -d' ' -f3- || true)
  {
    echo "codex model: ${ran_model:-$m (no header; requested)} | reasoning effort: ${ran_effort:-$effort (requested)}${note}$([ "$rc" -ne 0 ] && echo " | FAILED exit $rc, see $log")"
    echo
    cat "$body" 2>/dev/null || true
  } > "$out"
  rm -f "$body"
  return "$rc"
}
