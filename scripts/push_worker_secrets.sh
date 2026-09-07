#!/usr/bin/env bash
#
# Upload worker/.dev.vars to Cloudflare as Worker secrets.
#
# Why a script rather than four `wrangler secret put` invocations: each one
# prompts, so the values get typed by hand four times, and the first thing
# this project learned about secrets is that a mistyped one fails silently
# and much later. `USER_TIMEZONE` set wrong makes the phone ask for the
# wrong day; `MERCURY_API_TOKEN` set to a truncated value makes every route
# 503. One file, reviewed once, uploaded verbatim.
#
# Values still at their <placeholder> are skipped, not uploaded. The Worker
# already handles "missing" well — it answers 503 naming the variable — and
# handles "present but wrong" badly, because that failure surfaces as a 401
# from GitHub inside a 03:40 cron tick nobody is watching.
#
# Nothing here prints a secret value.

set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
vars_file="${1:-$repo_root/worker/.dev.vars}"

if [[ ! -f "$vars_file" ]]; then
  echo "No $vars_file." >&2
  echo "Copy worker/.dev.vars.example to it and fill in the real values." >&2
  exit 1
fi

# Plain strings, not arrays: macOS ships bash 3.2, where expanding an empty
# array under `set -u` is an unbound-variable error.
pushed=""
skipped=""

while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line%$'\r'}"                       # tolerate CRLF
  [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
  [[ "$line" != *=* ]] && continue

  key="${line%%=*}"
  value="${line#*=}"
  # Trim, not squeeze. This used to be `tr -d '[:space:]'`, which also ate
  # whitespace INSIDE the name — so `MY KEY=v` became a plausible-looking
  # `MYKEY` instead of an error. Let wrangler reject a malformed name out
  # loud; the only thing worth silently fixing here is indentation.
  key="${key#"${key%%[![:space:]]*}"}"
  key="${key%"${key##*[![:space:]]}"}"

  # Trim outside whitespace BEFORE looking at quotes, so `KEY = "a b"` keeps
  # its inner spaces and loses its outer ones. Not cosmetic: the key is
  # whitespace-stripped just above, so without this a hand-edited
  # `GITHUB_DISPATCH_TOKEN = github_pat_...` is ACCEPTED and uploaded with a
  # leading space, and GitHub answers 401 at 03:40. Dropping the line would
  # be better than that; keeping the value intact is better still.
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"

  # Strip one matching pair of surrounding quotes, if present.
  if [[ "$value" == \"*\" || "$value" == \'*\' ]]; then
    value="${value:1:${#value}-2}"
  fi

  if [[ -z "$value" || "$value" == \<*\> ]]; then
    skipped="$skipped $key"
    continue
  fi

  # The Worker's own floor. Catching it here names the problem; letting it
  # through means finding out from a 503 on the phone.
  if [[ "$key" == "MERCURY_API_TOKEN" && ${#value} -lt 32 ]]; then
    echo "MERCURY_API_TOKEN is ${#value} characters; the Worker requires 32." >&2
    exit 1
  fi

  # A zone the Worker cannot parse does not fail — it falls back to UTC and
  # writes one line to a log nobody reads, and the phone then asks for the
  # wrong day's briefing. That is the failure this check exists for; it is
  # unreachable from the Worker side, because by then the damage is silent.
  if [[ "$key" == "USER_TIMEZONE" ]]; then
    if ! command -v python3 >/dev/null 2>&1; then
      # Say so. A check that quietly skips itself is worse than no check —
      # it leaves the operator believing the zone was validated.
      echo "WARNING: no python3, so USER_TIMEZONE '$value' was NOT checked." >&2
    elif ! python3 -c 'import sys,zoneinfo; zoneinfo.ZoneInfo(sys.argv[1])' "$value" \
         >/dev/null 2>&1; then
      echo "USER_TIMEZONE '$value' is not an IANA zone (want e.g. Europe/Lisbon)." >&2
      exit 1
    fi
  fi

  echo "→ $key"
  # No trailing newline: wrangler stores stdin as given, and a secret with a
  # stray \n compares unequal to the same value in backend/.env.
  printf '%s' "$value" | (cd "$repo_root/worker" && npx wrangler secret put "$key")
  pushed="$pushed $key"
done < "$vars_file"

echo
[[ -z "$pushed" ]]  || echo "Uploaded:$pushed"
[[ -z "$skipped" ]] || echo "Skipped (still placeholders):$skipped"
echo "Now: cd worker && npx wrangler deploy"
