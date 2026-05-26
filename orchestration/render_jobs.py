#!/usr/bin/env python3
"""Render per-cell k8s Job + ConfigMap manifests from the matrix.

Each "cell" in this experiment is a (model, target) combination, run as
one k8s Job. Per cell we produce a multi-document YAML:

    ConfigMap  — rendered prompt_body.md + opencode.json + PLAN.md
    Job        — initContainers [setup, agent] + container [scorer],
                 sharing an emptyDir for workspace + agent metadata.

Prompt + opencode-config rendering happens here (Python + Jinja2),
then the Job manifest is applied via `orchestration/k8s_apply.sh`.

Usage examples:

    # Single-cell smoke
    pixi run render -- \\
        --batch-tag pr5-smoke \\
        --image-agent  ghcr.io/schmidtdse/josh-llm-experiment/fortree-agent:sha-abcdef \\
        --image-scorer ghcr.io/schmidtdse/josh-llm-experiment/fortree-scorer:sha-abcdef \\
        --single-cell model=claude,target=josh

    # Full panel (matrix CSV with `model,target` columns)
    uv run orchestration/render_jobs.py \\
        --batch-tag headline-2026-05 \\
        --image-agent  …:sha-abcdef \\
        --image-scorer …:sha-abcdef \\
        --matrix orchestration/matrix.csv

Invoked from `orchestration/k8s_apply.sh`, which wraps the render +
kubectl apply two-step under `uv run` automatically.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_NAMESPACE = "joshsim"
DEFAULT_SERVICE_ACCOUNT = "joshsim-batch"
DEFAULT_MINIO_SECRET = "minio-creds"
DEFAULT_OPENROUTER_SECRET = "openrouter-creds"
# Judge model for the in-Pod fuzzy judge (run-judge.sh). Short name from
# config/models.yaml; resolved to its OpenRouter slug inside the scorer
# container. Pinned to `codex` (openai/gpt-5-codex) so no agent in the
# panel self-judges. Override with --judge-model.
DEFAULT_JUDGE_MODEL = "codex"
# GKE Autopilot compute class. Default Balanced lets the cluster
# autoscaler consolidate nodes when mesa cells finish, which silently
# evicts long-running josh cells (~80 % loss on mini-headline 20260521,
# see KUBE_WATCH.md §9). Pin to Performance — dedicated nodes per Pod,
# not subject to autoscaler consolidation, costs more but actually runs
# to completion. The `safe-to-evict: false` annotation stays as a
# secondary defense against maintenance / drain events.
DEFAULT_COMPUTE_CLASS = "Performance"
# Performance class requires a machine-family selector or Autopilot
# refuses to schedule ("node(s) didn't match Pod's node affinity").
# n2 is the workhorse Intel Cascade Lake / Ice Lake series — broad
# us-west1 availability, balanced cost, more than enough CPU for our
# LLM-bound agent + Josh/Python scorer. c3 / c4 are faster latest-gen
# Intel; c2d / t2d / n2d are AMD EPYC. Ignored when compute-class is
# Balanced or Scale-Out (the template only emits machine-family for
# Performance). Override with --machine-family.
DEFAULT_MACHINE_FAMILY = "n2"

# Pod-level liveness ceiling. The pr5-smoke (sonnet+josh) completed in
# 68 min wall-clock; the first mini-headline lost all 100 cells at
# exactly the 3600s mark before mc mirror could fire. 7200s (2h) gives
# ~2× headroom over sonnet's observed time and lets slower-to-engage
# models (gemma, mistral) finish. Tune via --active-deadline-seconds.
DEFAULT_ACTIVE_DEADLINE_SECONDS = 7200
DEFAULT_TTL_SECONDS_AFTER_FINISHED = 86400  # 24h — long enough for log fetch
# These two env vars are inherited from the local-orchestration era;
# nothing inside the agent container reads them under k8s (the SIGTERM
# handler exists in agent-entrypoint.sh but no external watcher fires
# the signal). Left in place as inert injection so the headline
# manifest still records the *intent* — see open question #3 in
# IMPLEMENTATION_PLAN.md before relying on them as a backstop.
DEFAULT_WALL_CLOCK_BACKSTOP_SEC = 1800
DEFAULT_IDLE_THRESHOLD_SEC = 120

# Request vs limit are now asymmetric: requests stay at 8 CPU / 16 GiB
# (sized for the typical happy-path observed across sonnet-mesa /
# claude-mesa / claude-josh — peak <9 GiB working set) and limits go
# up to 16 CPU / 32 GiB to give memory-hungry models burst headroom.
# Burstable QoS (request<limit) means Autopilot reserves the request
# only, so we don't pay for the burst envelope unless the cell uses it.
# JVM heap cap (MaxRAMPercentage=50) auto-scales to 16 GiB max heap
# under the new limit — double the prior cap for josh validate/preprocess.
# minimax-josh peaked at 10.4 GiB working set with the prior 16 GiB
# limit at the moment of OOM, so this should give the ~5+ GiB headroom
# needed past that point.
DEFAULT_AGENT_CPU_REQUEST = "8"
DEFAULT_AGENT_CPU_LIMIT = "16"
DEFAULT_AGENT_MEMORY_REQUEST = "16Gi"
DEFAULT_AGENT_MEMORY_LIMIT = "32Gi"
# Scorer matches the agent exactly — no per-cell sim time / OOM
# differences from machine-shape mismatch when the sim is what we're
# actually measuring (sim_wall_seconds is a headline metric per
# SCORING.md). Also gives the in-Pod fuzzy judge (run-judge.sh) room
# alongside the Josh JVM + 100×100 sim.
DEFAULT_SCORER_CPU_REQUEST = "8"
DEFAULT_SCORER_CPU_LIMIT = "16"
DEFAULT_SCORER_MEMORY_REQUEST = "16Gi"
DEFAULT_SCORER_MEMORY_LIMIT = "32Gi"

VALID_TARGETS = ("josh", "mesa")


def _slugify(s: str) -> str:
    """K8s-safe lowercase slug."""
    s = re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-")
    return re.sub(r"-+", "-", s)


def _resolve_model(short_name: str) -> str:
    models_yaml = REPO_ROOT / "config" / "models.yaml"
    models = yaml.safe_load(models_yaml.read_text(encoding="utf-8"))
    slug = models.get(short_name)
    if not slug:
        valid = ", ".join(sorted(models.keys()))
        raise SystemExit(
            f"model short-name {short_name!r} not in {models_yaml}. valid: {valid}"
        )
    return slug


def _render_prompt_body(target: str) -> str:
    if target not in VALID_TARGETS:
        raise SystemExit(f"target must be one of {VALID_TARGETS}; got {target!r}")
    rung = (REPO_ROOT / "prompts" / "BASE_PROMPT.md").read_text(encoding="utf-8")
    target_directive = (REPO_ROOT / "prompts" / "targets" / f"{target}.md").read_text(encoding="utf-8")
    sidecar = (REPO_ROOT / "prompts" / "SIDECAR.md").read_text(encoding="utf-8")
    return (
        f"{rung}\n\n## Implementation directive\n\n{target_directive}\n\n---\n\n{sidecar}"
    )


def _render_opencode_json(model_slug: str) -> str:
    template = (REPO_ROOT / "config" / "opencode.template.json").read_text(encoding="utf-8")
    rendered = template.replace("${RESOLVED_MODEL_ID}", model_slug)
    # Re-emit canonical JSON so the ConfigMap doesn't carry whitespace-only diffs.
    return json.dumps(json.loads(rendered), indent=2) + "\n"


def _render_plan_md() -> str:
    return (REPO_ROOT / "prompts" / "PLAN_TEMPLATE.md").read_text(encoding="utf-8")


def _parse_single_cell(spec: str) -> dict:
    """Parse `model=X,target=Y` into a dict."""
    out: dict = {}
    for kv in spec.split(","):
        if "=" not in kv:
            raise SystemExit(f"--single-cell entry must be key=value; got {kv!r}")
        k, v = kv.split("=", 1)
        out[k.strip()] = v.strip()
    for required in ("model", "target"):
        if required not in out:
            raise SystemExit(f"--single-cell missing required key: {required}")
    return out


def _parse_matrix(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if not row.get("model") or not row.get("target"):
                raise SystemExit(f"matrix row missing model/target: {row}")
            rows.append({"model": row["model"].strip(), "target": row["target"].strip()})
    if not rows:
        raise SystemExit(f"matrix at {path} produced 0 cells")
    return rows


def _cell_id(batch_tag: str, model: str, target: str, rep_idx: int = 0, rep_count: int = 1) -> str:
    """Compose a k8s-safe cell ID.

    If (model, target) appears more than once in the matrix, the suffix
    `-rN` (N = rep_idx) is appended to disambiguate. Single-rep cells
    keep the clean `<batch>-<model>-<target>` form for backward compat
    with the pr5 smoke.
    """
    base = f"{batch_tag}-{model}-{target}"
    if rep_count > 1:
        base = f"{base}-r{rep_idx}"
    cid = _slugify(base)
    if len(cid) > 63:
        raise SystemExit(f"cell_id exceeds 63 chars: {cid!r}")
    return cid


def _render_one(env, args, model: str, target: str, rep_idx: int, rep_count: int) -> tuple[str, str]:
    model_slug = _resolve_model(model)
    prompt_body = _render_prompt_body(target)
    opencode_json = _render_opencode_json(model_slug)
    plan_md = _render_plan_md()
    cell_id = _cell_id(args.batch_tag, model, target, rep_idx=rep_idx, rep_count=rep_count)
    configmap_name = f"cell-config-{cell_id}"

    template = env.get_template("job.yaml.j2")
    rendered = template.render(
        batch_tag=args.batch_tag,
        cell_id=cell_id,
        configmap_name=configmap_name,
        namespace=args.namespace,
        service_account=args.service_account,
        model=model,
        target=target,
        agent_image=args.image_agent,
        scorer_image=args.image_scorer,
        minio_secret=args.minio_secret,
        minio_prefix=args.minio_prefix or args.batch_tag,
        openrouter_secret=args.openrouter_secret,
        judge_model=args.judge_model,
        compute_class=args.compute_class,
        machine_family=args.machine_family,
        active_deadline_seconds=args.active_deadline_seconds,
        ttl_seconds_after_finished=args.ttl_seconds_after_finished,
        wall_clock_backstop_sec=args.wall_clock_backstop_sec,
        idle_threshold_sec=args.idle_threshold_sec,
        fail_fast_on_step_error=str(args.fail_fast_on_step_error).lower(),
        agent_cpu_request=args.agent_cpu_request,
        agent_cpu_limit=args.agent_cpu_limit,
        agent_memory_request=args.agent_memory_request,
        agent_memory_limit=args.agent_memory_limit,
        scorer_cpu_request=args.scorer_cpu_request,
        scorer_cpu_limit=args.scorer_cpu_limit,
        scorer_memory_request=args.scorer_memory_request,
        scorer_memory_limit=args.scorer_memory_limit,
        prompt_body=prompt_body,
        opencode_json=opencode_json,
        plan_md=plan_md,
    )
    return cell_id, rendered


def main() -> int:
    parser = argparse.ArgumentParser(prog="render_jobs.py", description=__doc__)
    parser.add_argument("--batch-tag", required=True, help="Batch identifier (used in object key + label).")
    parser.add_argument("--image-agent", required=True, help="Full image ref for fortree:agent.")
    parser.add_argument("--image-scorer", required=True, help="Full image ref for fortree:scorer.")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="Output directory (default: orchestration/rendered/<batch-tag>/).")

    cells = parser.add_mutually_exclusive_group(required=True)
    cells.add_argument("--single-cell", help="Inline single cell: model=X,target=Y")
    cells.add_argument("--matrix", type=Path, help="CSV with columns 'model','target'.")

    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--service-account", default=DEFAULT_SERVICE_ACCOUNT)
    parser.add_argument("--minio-secret", default=DEFAULT_MINIO_SECRET)
    parser.add_argument("--openrouter-secret", default=DEFAULT_OPENROUTER_SECRET)
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL,
                        help="Short name (config/models.yaml) for the in-Pod fuzzy "
                             f"judge model. Default: {DEFAULT_JUDGE_MODEL}.")
    parser.add_argument("--compute-class", default=DEFAULT_COMPUTE_CLASS,
                        choices=["Performance", "Balanced", "Scale-Out"],
                        help="GKE Autopilot compute class (nodeSelector "
                             "cloud.google.com/compute-class). "
                             f"Default: {DEFAULT_COMPUTE_CLASS} — dedicated nodes, "
                             "no autoscaler consolidation. Drop to Balanced for "
                             "cheap smoke tests where eviction risk is acceptable.")
    parser.add_argument("--machine-family", default=DEFAULT_MACHINE_FAMILY,
                        help="GKE machine family for Performance class "
                             "(nodeSelector cloud.google.com/machine-family). "
                             f"Default: {DEFAULT_MACHINE_FAMILY}. Required when "
                             "compute-class=Performance; ignored otherwise. "
                             "Supported: c4, c4a, c4d, c3, c3d, c2, c2d, h3, "
                             "h4d, t2a, t2d, e2, n1, n2, n2d, n4, n4d, z3.")
    parser.add_argument("--minio-prefix", default="",
                        help="Object-key prefix (default: same as --batch-tag).")

    parser.add_argument("--active-deadline-seconds", type=int, default=DEFAULT_ACTIVE_DEADLINE_SECONDS)
    parser.add_argument("--ttl-seconds-after-finished", type=int, default=DEFAULT_TTL_SECONDS_AFTER_FINISHED)
    parser.add_argument("--wall-clock-backstop-sec", type=int, default=DEFAULT_WALL_CLOCK_BACKSTOP_SEC)
    parser.add_argument("--idle-threshold-sec", type=int, default=DEFAULT_IDLE_THRESHOLD_SEC)
    parser.add_argument("--fail-fast-on-step-error", action="store_true")

    parser.add_argument("--agent-cpu-request", default=DEFAULT_AGENT_CPU_REQUEST)
    parser.add_argument("--agent-cpu-limit", default=DEFAULT_AGENT_CPU_LIMIT)
    parser.add_argument("--agent-memory-request", default=DEFAULT_AGENT_MEMORY_REQUEST)
    parser.add_argument("--agent-memory-limit", default=DEFAULT_AGENT_MEMORY_LIMIT)
    parser.add_argument("--scorer-cpu-request", default=DEFAULT_SCORER_CPU_REQUEST)
    parser.add_argument("--scorer-cpu-limit", default=DEFAULT_SCORER_CPU_LIMIT)
    parser.add_argument("--scorer-memory-request", default=DEFAULT_SCORER_MEMORY_REQUEST)
    parser.add_argument("--scorer-memory-limit", default=DEFAULT_SCORER_MEMORY_LIMIT)

    args = parser.parse_args()

    if args.single_cell:
        cells_list = [_parse_single_cell(args.single_cell)]
    else:
        cells_list = _parse_matrix(args.matrix)

    out_dir = args.out_dir or REPO_ROOT / "orchestration" / "rendered" / _slugify(args.batch_tag)
    out_dir.mkdir(parents=True, exist_ok=True)

    env = Environment(
        loader=FileSystemLoader(REPO_ROOT / "orchestration" / "templates"),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )

    # Count how many times each (model, target) appears so we can disambiguate
    # duplicate rows with a `-rN` suffix on the cell_id.
    from collections import Counter
    pair_counts = Counter((c["model"], c["target"]) for c in cells_list)
    pair_seen: dict[tuple[str, str], int] = {}

    print(f"▶ Rendering {len(cells_list)} cell(s) into {out_dir}")
    for cell in cells_list:
        key = (cell["model"], cell["target"])
        rep_idx = pair_seen.get(key, 0)
        pair_seen[key] = rep_idx + 1
        rep_count = pair_counts[key]
        cell_id, rendered = _render_one(env, args, cell["model"], cell["target"], rep_idx, rep_count)
        path = out_dir / f"{cell_id}.yaml"
        path.write_text(rendered, encoding="utf-8")
        print(f"  ✔ {path.relative_to(REPO_ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
