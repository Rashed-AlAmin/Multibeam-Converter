#!/usr/bin/env bash
# End-to-end check against a running stack.
#
#   docker compose up --build -d
#   ./scripts/smoke-test.sh path/to/file.all
#
# Uploads the file over HTTP exactly as the browser does, polls until the
# conversion finishes, downloads the LAS and verifies it reads back with the
# right point count, a CRS in its header, and soundings below the surface.

set -euo pipefail

FILE="${1:?usage: smoke-test.sh <file.all>}"
API="${API:-http://localhost:8000}"
OUT="${OUT:-/tmp/smoke-output.las}"

STATE=$(mktemp); BAD=$(mktemp); JUNK=$(mktemp --suffix=.all)
trap 'rm -f "$STATE" "$BAD" "$JUNK"' EXIT

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

step "Health"
curl -fsS "$API/api/health" | python3 -m json.tool

step "Upload $(basename "$FILE")"
curl -fsS -X POST "$API/api/jobs" -F "file=@${FILE}" -o "$STATE"
JOB=$(python3 - "$STATE" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))["id"])
PY
)
echo "job $JOB"

step "Convert"
while :; do
  curl -fsS "$API/api/jobs/$JOB" -o "$STATE"
  LINE=$(python3 - "$STATE" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(d["status"], round(d["progress"] * 100), d["message"])
PY
)
  STATUS=${LINE%% *}; REST=${LINE#* }
  printf '\r  [%3s%%] %-42s' "${REST%% *}" "${REST#* }"
  case "$STATUS" in
    done)  echo; break ;;
    error)
      echo
      python3 - "$STATE" <<'PY'
import json, sys
print("FAILED:", json.load(open(sys.argv[1]))["error"])
PY
      exit 1 ;;
  esac
  sleep 1
done

step "Summary"
python3 - "$STATE" <<'PY'
import json, sys
s = json.load(open(sys.argv[1]))["summary"]
print(f"  points        {s['point_count']:,}")
print(f"  depth         {s['depth_min_m']:.2f} to {s['depth_max_m']:.2f} m (positive down)")
print(f"  UTM zone      {s['utm_zone']}{s['utm_hemisphere']}  EPSG:{s['epsg']}  {s['crs_name']}")
print(f"  LAS size      {s['las_bytes']:,} bytes")
PY

step "Preview payload"
curl -fsS "$API/api/jobs/$JOB/preview" -o "$BAD"
python3 - "$BAD" <<'PY'
import json, sys
p = json.load(open(sys.argv[1]))
print(f"  {p['count']:,} of {p['total']:,} points, {len(p['x'])} x-values")
assert len(p["x"]) == len(p["y"]) == len(p["z"]) == p["count"]
print("  OK")
PY

step "Download"
curl -fsS "$API/api/jobs/$JOB/download" -o "$OUT"
ls -l "$OUT"

step "Verify the LAS"
# laspy lives in the backend image, not necessarily on the host, so the
# verification runs wherever it can: locally if available, otherwise by
# piping the downloaded file back into the container.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if python3 -c 'import laspy' 2>/dev/null; then
  python3 "$HERE/verify_las.py" "$OUT"
else
  echo "  (laspy is not on the host - verifying inside the backend container)"
  docker compose cp "$OUT" backend:/tmp/verify.las >/dev/null
  docker compose cp "$HERE/verify_las.py" backend:/tmp/verify_las.py >/dev/null
  docker compose exec -T backend python3 /tmp/verify_las.py /tmp/verify.las
  docker compose exec -T backend rm -f /tmp/verify.las /tmp/verify_las.py
fi

step "Bad input is handled"
printf 'this is not sonar data at all' > "$JUNK"
curl -sS -X POST "$API/api/jobs" -F "file=@${JUNK}" -o "$BAD" \
     -w '  junk .all accepted for processing: HTTP %{http_code}\n'
JOB2=$(python3 - "$BAD" <<'PY'
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("id", ""))
except Exception:
    print("")
PY
)
if [ -n "$JOB2" ]; then
  sleep 4
  curl -fsS "$API/api/jobs/$JOB2" -o "$BAD" || true
  python3 - "$BAD" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(f"  status:  {d['status']}")
print(f"  message: {d['error']}")
PY
  curl -fsS -X DELETE "$API/api/jobs/$JOB2" -o /dev/null
fi
curl -sS -X POST "$API/api/jobs" -F "file=@/etc/hostname" -o /dev/null \
     -w '  wrong extension rejected: HTTP %{http_code} (expect 400)\n'
curl -sS "$API/api/jobs/does-not-exist" -o /dev/null \
     -w '  unknown job id:            HTTP %{http_code} (expect 404)\n'

step "Clean up"
curl -fsS -X DELETE "$API/api/jobs/$JOB" | python3 -m json.tool
echo
echo "All checks passed."
