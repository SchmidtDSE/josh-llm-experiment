#!/usr/bin/env python3
"""Render per-cell k8s Job + ConfigMap manifests from the matrix.

Each "cell" in this experiment is a (model, target) combination, run as
one k8s Job. Per cell we produce a multi-document YAML:

    ConfigMap  — rendered prompt_body.md + opencode.json + PLAN.md
    Job        — initContainers [setup, agent] + container [scorer],
                 sharing an emptyDir for workspace + agent metadata.

The renderer is the k8s-side replacement for `orchestration/launch_run.sh`
— the prompt + opencode-config rendering moves from a host bash script
to this Python script + a Jinja2 template. The Job manifest is then
applied via `orchestration/k8s_apply.sh`.

Usage examples:

    # Single-cell smoke
    uv run orchestration/render_jobs.py \\
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

DEFAULT_ACTIVE_DEADLINE_SECONDS = 3600
DEFAULT_TTL_SECONDS_AFTER_FINISHED = 86400  # 24h — long enough for log fetch
DEFAULT_WALL_CLOCK_BACKSTOP_SEC = 1800
DEFAULT_IDLE_THRESHOLD_SEC = 120

DEFAULT_AGENT_CPU_REQUEST = "4"
DEFAULT_AGENT_CPU_LIMIT = "4"
# Pod memory must exceed the JVM heap (JAVA_TOOL_OPTIONS=-Xmx16g baked
# into the image) plus opencode/Node + OS overhead, otherwise the kernel
# OOM-kills the Pod when josh fires during the agent's run.sh self-test.
# 24Gi gives the JVM its 16Gi heap and leaves ~8Gi for everything else;
# the first pr5 smoke OOM'd at 16Gi=16Gi. Autopilot caps memory:CPU at
# 6.5:1 GiB:vCPU, so 24Gi at 4 vCPU is at 6:1 — within the envelope.
DEFAULT_AGENT_MEMORY_REQUEST = "24Gi"
DEFAULT_AGENT_MEMORY_LIMIT = "24Gi"
DEFAULT_SCORER_CPU_REQUEST = "2"
DEFAULT_SCORER_CPU_LIMIT = "2"
DEFAULT_SCORER_MEMORY_REQUEST = "4Gi"
DEFAULT_SCORER_MEMORY_LIMIT = "4Gi"

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


def _cell_id(batch_tag: str, model: str, target: str) -> str:
    cid = _slugify(f"{batch_tag}-{model}-{target}")
    # k8s name length cap is 63 chars
    if len(cid) > 63:
        raise SystemExit(f"cell_id exceeds 63 chars: {cid!r}")
    return cid


def _render_one(env, args, model: str, target: str) -> tuple[str, str]:
    model_slug = _resolve_model(model)
    prompt_body = _render_prompt_body(target)
    opencode_json = _render_opencode_json(model_slug)
    plan_md = _render_plan_md()
    cell_id = _cell_id(args.batch_tag, model, target)
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

    print(f"▶ Rendering {len(cells_list)} cell(s) into {out_dir}")
    for cell in cells_list:
        cell_id, rendered = _render_one(env, args, cell["model"], cell["target"])
        path = out_dir / f"{cell_id}.yaml"
        path.write_text(rendered, encoding="utf-8")
        print(f"  ✔ {path.relative_to(REPO_ROOT)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
