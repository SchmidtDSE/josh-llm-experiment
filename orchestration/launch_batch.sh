#!/usr/bin/env bash
# Fan out N concurrent experimental cells (agent + scorer + report) on
# the local host. Each cell is one MODEL × RUNG × TARGET tuple realised
# as a separate RUN_ID, executed by orchestration/launch_cell.sh.
#
# Two CLI forms:
#   launch_batch.sh --model M --rung R --target T --runs N [...]
#       Single cell × N replicates. Matches the IMPLEMENTATION_PLAN.md
#       phase-4c validation gate.
#   launch_batch.sh --cells cells.csv [...]
#       Matrix from CSV. Header row `model,rung,target,replicates`;
#       comment lines starting `#` and blank lines skipped. Each row
#       expands to `replicates` entries in the worklist.
#
# Shared flags:
#   --jobs N        Concurrent slots. Defaults to $BATCH_CONCURRENCY or 4.
#   --batch-tag TAG Batch identifier (default batch-<UTC ISO timestamp>).
#                   Becomes runs/<TAG>/, the sibling dir holding
#                   worklist.tsv, joblog.tsv, manifest.jsonl, summary.txt.
#
# Per-run dirs stay at runs/<RUN_ID>/ (existing convention); the batch
# dir is sibling and metadata-only, so the eventual Phase 4d uploader
# can target either a single run or a whole batch.
#
# Concurrency model:
#   - GNU parallel drives the fan-out; --joblog gives per-cell exit
#     codes + wall time and supports --retry-failed.
#   - Each child (launch_cell.sh → launch_run.sh) owns its own bridge
#     network and dnsmasq sidecar, named by RUN_ID, with idempotent
#     teardown traps. Concurrent runs never share mutable docker state;
#     the only shared mount is data/ as read-only.
#   - Pre-sweep cleanup at the top removes any fortree-run-* networks
#     or dnsmasq-* containers leaked by a prior SIGKILL'd batch.
#
# Exit codes:
#   0  parallel reports all cells exited 0
#   1  at least one cell exited non-zero (per-cell detail in joblog.tsv)
#   2  argument error before fan-out
#   5  GNU parallel not installed
set -euo pipefail

# Debian/Ubuntu ships *two* binaries named `parallel`: GNU parallel (from
# the `parallel` package — what we need) and moreutils-parallel (from the
# `moreutils` package — a much simpler tool with an incompatible CLI).
# `command -v` can't tell them apart, so verify by --version output.
if ! command -v parallel >/dev/null 2>&1 \
   || ! parallel --version 2>/dev/null | head -1 | grep -q "^GNU parallel"; then
  echo "Need GNU parallel — got something else (likely moreutils-parallel)." >&2
  echo "Install: apt-get install parallel  /  brew install parallel" >&2
  echo "If both are installed, ensure GNU parallel comes first in PATH." >&2
  exit 5
fi
if ! command -v jq >/dev/null 2>&1; then
  echo "Install jq (apt-get install jq; brew install jq)" >&2
  exit 5
fi

# Prefer uuidgen (from uuid-runtime); fall back to the kernel's UUID
# generator on Linux so an SSH host without uuid-runtime installed
# still works.
gen_uuid() {
  if command -v uuidgen >/dev/null 2>&1; then
    uuidgen
  elif [ -r /proc/sys/kernel/random/uuid ]; then
    cat /proc/sys/kernel/random/uuid
  else
    echo "Need uuidgen (apt-get install uuid-runtime) or /proc/sys/kernel/random/uuid" >&2
    return 1
  fi
}

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# Path to the per-cell driver. Defaults to the script next to this one;
# overridable via LAUNCH_CELL_PATH so tests can swap in a stub that
# exercises the fan-out / aggregation paths without spinning Docker.
LAUNCH_CELL_PATH="${LAUNCH_CELL_PATH:-$REPO_ROOT/orchestration/launch_cell.sh}"

usage() {
  cat <<USAGE >&2
Usage:
  $(basename "$0") --model M --rung R --target T --runs N [--jobs J] [--batch-tag TAG]
  $(basename "$0") --cells cells.csv [--jobs J] [--batch-tag TAG]
USAGE
}

MODEL=""
RUNG=""
TARGET=""
RUNS=""
CELLS_FILE=""
JOBS="${BATCH_CONCURRENCY:-4}"
BATCH_TAG=""

while [ $# -gt 0 ]; do
  case "$1" in
    --model)     MODEL="$2";      shift 2 ;;
    --rung)      RUNG="$2";       shift 2 ;;
    --target)    TARGET="$2";     shift 2 ;;
    --runs)      RUNS="$2";       shift 2 ;;
    --cells)     CELLS_FILE="$2"; shift 2 ;;
    --jobs)      JOBS="$2";       shift 2 ;;
    --batch-tag) BATCH_TAG="$2";  shift 2 ;;
    -h|--help)   usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [ -n "$CELLS_FILE" ]; then
  if [ -n "$MODEL" ] || [ -n "$RUNG" ] || [ -n "$TARGET" ] || [ -n "$RUNS" ]; then
    echo "--cells is mutually exclusive with --model/--rung/--target/--runs" >&2
    exit 2
  fi
  if [ ! -f "$CELLS_FILE" ]; then
    echo "--cells file not found: $CELLS_FILE" >&2
    exit 2
  fi
else
  if [ -z "$MODEL" ] || [ -z "$RUNG" ] || [ -z "$TARGET" ] || [ -z "$RUNS" ]; then
    echo "Either --cells FILE or all of --model/--rung/--target/--runs required" >&2
    usage
    exit 2
  fi
  case "$RUNS" in (''|*[!0-9]*) echo "--runs must be a positive integer" >&2; exit 2 ;; esac
  if [ "$RUNS" -lt 1 ]; then echo "--runs must be >= 1" >&2; exit 2; fi
fi

case "$JOBS" in (''|*[!0-9]*) echo "--jobs must be a positive integer" >&2; exit 2 ;; esac
if [ "$JOBS" -lt 1 ]; then echo "--jobs must be >= 1" >&2; exit 2; fi

if [ -z "$BATCH_TAG" ]; then
  BATCH_TAG="batch-$(date -u +%Y%m%dT%H%M%SZ)"
fi
case "$BATCH_TAG" in
  batch-*) ;;
  *) BATCH_TAG="batch-$BATCH_TAG" ;;
esac

BATCH_DIR="$REPO_ROOT/runs/$BATCH_TAG"
if [ -e "$BATCH_DIR" ]; then
  echo "Batch dir already exists: $BATCH_DIR — refusing to overwrite" >&2
  exit 2
fi
mkdir -p "$BATCH_DIR"
WORKLIST="$BATCH_DIR/worklist.tsv"
JOBLOG="$BATCH_DIR/joblog.tsv"
MANIFEST="$BATCH_DIR/manifest.jsonl"
SUMMARY="$BATCH_DIR/summary.txt"

# Remove the (empty) batch dir if we fail out before fan-out starts —
# validation rejections shouldn't leave orphan batch-* directories
# polluting runs/. The trap is cancelled (`trap - EXIT`) just before
# `parallel` is invoked; from that point onward the dir is real and
# should be retained for joblog/manifest inspection.
# shellcheck disable=SC2317  # invoked indirectly via trap
_cleanup_on_fail() {
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    rm -rf "$BATCH_DIR" 2>/dev/null || true
  fi
}
trap _cleanup_on_fail EXIT

# --- Validation helpers ---
# Resolve a MODEL through the same code path launch_run.sh uses so an
# unknown short name fails fast, before any docker resources spin up.
validate_model() {
  local m="$1"
  if ! "$REPO_ROOT/orchestration/resolve_model.py" "$m" > /dev/null; then
    echo "Unknown MODEL: $m (see config/models.yaml)" >&2
    return 1
  fi
}
validate_rung()   { case "$1" in 1|5) return 0 ;; *) echo "RUNG must be 1 or 5, got: $1" >&2; return 1 ;; esac; }
validate_target() { case "$1" in josh|mesa) return 0 ;; *) echo "TARGET must be josh or mesa, got: $1" >&2; return 1 ;; esac; }

# --- Build the worklist (TSV: model rung target run_id) ---
build_worklist_single() {
  validate_model  "$MODEL"
  validate_rung   "$RUNG"
  validate_target "$TARGET"
  local _i
  for _i in $(seq 1 "$RUNS"); do
    printf '%s\t%s\t%s\t%s\n' "$MODEL" "$RUNG" "$TARGET" "$(gen_uuid)" >> "$WORKLIST"
  done
}

build_worklist_cells() {
  local lineno=0
  local saw_header=0
  while IFS= read -r line || [ -n "$line" ]; do
    lineno=$((lineno + 1))
    # Strip CR and trailing whitespace
    line="${line%$'\r'}"
    case "$line" in
      ''|'#'*) continue ;;
    esac
    if [ "$saw_header" -eq 0 ]; then
      if [ "$line" != "model,rung,target,replicates" ]; then
        echo "$CELLS_FILE:$lineno: expected header 'model,rung,target,replicates', got: $line" >&2
        return 1
      fi
      saw_header=1
      continue
    fi
    local m r t n
    IFS=',' read -r m r t n <<< "$line"
    # Trim surrounding whitespace
    m="${m## }"; m="${m%% }"
    r="${r## }"; r="${r%% }"
    t="${t## }"; t="${t%% }"
    n="${n## }"; n="${n%% }"
    if [ -z "$m" ] || [ -z "$r" ] || [ -z "$t" ] || [ -z "$n" ]; then
      echo "$CELLS_FILE:$lineno: missing column(s) in: $line" >&2
      return 1
    fi
    case "$n" in (''|*[!0-9]*) echo "$CELLS_FILE:$lineno: replicates must be integer, got: $n" >&2; return 1 ;; esac
    if [ "$n" -lt 1 ]; then
      echo "$CELLS_FILE:$lineno: replicates must be >= 1" >&2
      return 1
    fi
    validate_model  "$m" || { echo "  at $CELLS_FILE:$lineno" >&2; return 1; }
    validate_rung   "$r" || { echo "  at $CELLS_FILE:$lineno" >&2; return 1; }
    validate_target "$t" || { echo "  at $CELLS_FILE:$lineno" >&2; return 1; }
    local _i
    for _i in $(seq 1 "$n"); do
      printf '%s\t%s\t%s\t%s\n' "$m" "$r" "$t" "$(gen_uuid)" >> "$WORKLIST"
    done
  done < "$CELLS_FILE"
  if [ "$saw_header" -eq 0 ]; then
    echo "$CELLS_FILE: no data rows (header line required)" >&2
    return 1
  fi
}

# Idempotent cleanup of any docker resources left behind by a SIGKILL'd
# prior batch. The per-run EXIT traps inside launch_run.sh handle the
# clean-exit case; this sweep catches the rest. Safe to run with live
# batches in flight — `docker network rm` declines to remove networks
# that still have attached containers, so an in-flight run is left alone.
presweep_cleanup() {
  local nets containers
  nets="$(docker network ls --filter name=fortree-run- --filter driver=bridge -q 2>/dev/null || true)"
  if [ -n "$nets" ]; then
    echo "$nets" | xargs -r docker network rm > /dev/null 2>&1 || true
  fi
  containers="$(docker ps -aq --filter name=dnsmasq- 2>/dev/null || true)"
  if [ -n "$containers" ]; then
    echo "$containers" | xargs -r docker rm -f > /dev/null 2>&1 || true
  fi
}

# Per-run manifest line. Tolerates missing files (a hard-crashed cell may
# have no scorer.json). Skipped entirely if run_meta.json is missing —
# that means launch_run.sh failed before workspace setup, which the
# joblog already records.
emit_manifest_line() {
  local run_id="$1"
  local run_dir="$REPO_ROOT/runs/$run_id"
  local meta="$run_dir/run_meta.json"
  if [ ! -f "$meta" ]; then
    return 0
  fi
  local cell="$run_dir/run_meta.cell.json"
  local scorer="$run_dir/scorer.json"
  local cell_arg=()
  local scorer_arg=()
  if [ -s "$cell" ];   then cell_arg=(--slurpfile cell "$cell");       else cell_arg=(--argjson cell "[null]");     fi
  if [ -s "$scorer" ]; then scorer_arg=(--slurpfile scorer "$scorer"); else scorer_arg=(--argjson scorer "[null]"); fi
  jq -c -n \
    --slurpfile meta "$meta" \
    "${cell_arg[@]}" \
    "${scorer_arg[@]}" \
    '{
       run_id:    $meta[0].run_id,
       model:     $meta[0].model,
       rung:      $meta[0].rung,
       target:    $meta[0].target,
       cell:      $cell[0],
       scorer:    $scorer[0],
       run_meta:  $meta[0]
     }' >> "$MANIFEST"
}

# --- Build worklist, then fan out ---
if [ -n "$CELLS_FILE" ]; then
  build_worklist_cells
else
  build_worklist_single
fi

TOTAL="$(wc -l < "$WORKLIST")"
echo "▶ Batch: $BATCH_TAG"
echo "  Worklist:    $WORKLIST  ($TOTAL cells)"
echo "  Joblog:      $JOBLOG"
echo "  Manifest:    $MANIFEST"
echo "  Concurrency: $JOBS"

echo "▶ Pre-sweep: cleaning orphan fortree networks / dnsmasq containers"
presweep_cleanup

# Past validation; from here on the batch dir is a real artifact and
# should survive non-zero exits so the user can inspect joblog/manifest.
trap - EXIT

echo "▶ Running cells (this can take a while; tail $JOBLOG for live progress)"
PARALLEL_EXIT=0
set +e
parallel --colsep '\t' --jobs "$JOBS" --joblog "$JOBLOG" --line-buffer \
  "MODEL={1} RUNG={2} TARGET={3} RUN_ID={4} '$LAUNCH_CELL_PATH'" \
  :::: "$WORKLIST"
PARALLEL_EXIT=$?
set -e

echo "▶ Aggregating manifest"
: > "$MANIFEST"
while IFS=$'\t' read -r _m _r _t run_id; do
  emit_manifest_line "$run_id"
done < "$WORKLIST"

# Skip the joblog header (1 line) when counting outcomes.
SUCCEEDED=$(awk 'NR>1 && $7==0' "$JOBLOG" 2>/dev/null | wc -l)
FAILED=$(awk 'NR>1 && $7!=0' "$JOBLOG" 2>/dev/null | wc -l)

{
  echo "batch_tag=$BATCH_TAG"
  echo "total=$TOTAL"
  echo "succeeded=$SUCCEEDED"
  echo "failed=$FAILED"
  echo "parallel_exit=$PARALLEL_EXIT"
  echo "concurrency=$JOBS"
} > "$SUMMARY"

echo ""
echo "✔ Batch done"
echo "  Total:     $TOTAL"
echo "  Succeeded: $SUCCEEDED"
echo "  Failed:    $FAILED"
echo "  Summary:   $SUMMARY"
echo "  Manifest:  $MANIFEST"
echo ""
if [ "$FAILED" -gt 0 ] || [ "$PARALLEL_EXIT" -ne 0 ]; then
  echo "  Re-run failed cells with:"
  echo "    parallel --retry-failed --joblog $JOBLOG"
  exit 1
fi
exit 0
