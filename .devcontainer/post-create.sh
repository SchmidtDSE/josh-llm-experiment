#!/usr/bin/env bash
# Devcontainer post-create hook. Idempotent — re-runs are no-ops.
#
# Devcontainer features handle docker (DinD), gh, gcloud, kubectl, and
# common-utils. This script adds the bits the features don't (reliably) cover:
#
#   1. The GKE gcloud auth plugin. The dhoeric/google-cloud-cli feature can
#      install it via `installGkeGcloudAuthPlugin`, but v1.0.1's plugin step
#      targets the legacy `google-cloud-sdk-gke-gcloud-auth-plugin` package
#      (frozen at an older version that won't co-install with the renamed,
#      current `google-cloud-cli` gcloud) — so it silently no-ops and kubectl
#      can't authenticate to GKE. We disable that option in devcontainer.json
#      and install the correctly-named package here instead.
#   2. The MinIO client `mc`. Used host-side by orchestration/pull_artefacts.sh
#      to fetch per-cell artefacts from the GCS bucket via the S3 interop API.
#      (`mc` also lives inside fortree:scorer for the in-Pod upload; that copy
#      is independent — see scripts/install_mc.sh.) We reuse the same install
#      script so the host and the scorer image share a single source of truth
#      for the rolling-latest sha256 anchor.
#   3. `pixi install` to materialise the orchestrator's Python env from
#      pixi.lock so the user can run `pixi run ...` without paying the
#      lazy-install cost on first invocation.
#   4. An absolute interpreter path in the `ir` (R) Jupyter kernelspec.
#      IRkernel::installspec writes a bare `R` into argv, which only resolves
#      inside `pixi run`. VS Code's Jupyter extension launches kernels
#      directly, so the R kernel fails to start there — and the extension
#      silently falls back to the Python kernel, which then reports a
#      SyntaxError on the first line of R. Affects the R notebooks
#      (01_analysis, 04_ecology_visualizations, 05_hoist_analysis).

set -euo pipefail

SUDO=""
[ "$(id -u)" -ne 0 ] && SUDO="sudo"

if ! command -v gke-gcloud-auth-plugin >/dev/null 2>&1; then
    echo "Installing gke-gcloud-auth-plugin (current google-cloud-cli-* package) ..."
    $SUDO apt-get update -qq
    $SUDO apt-get install -y google-cloud-cli-gke-gcloud-auth-plugin
else
    echo "gke-gcloud-auth-plugin already installed at $(command -v gke-gcloud-auth-plugin); leaving as-is"
fi

if ! command -v mc >/dev/null 2>&1; then
    echo "Installing mc via scripts/install_mc.sh ..."
    ./scripts/install_mc.sh
else
    echo "mc already installed at $(command -v mc); leaving as-is"
fi

# Pixi env. `pixi install` is idempotent — uses pixi.lock as the source of
# truth, no-op when the env is already materialised.
if [ -f pixi.toml ]; then
    echo "Materialising pixi env ..."
    pixi install
fi

# R kernelspec: rewrite argv[0] to the env's absolute R. Idempotent, and safe
# to re-run after any `pixi install` / `IRkernel::installspec`, both of which
# reset it to the bare `R`.
KERNEL_JSON=".pixi/envs/default/share/jupyter/kernels/ir/kernel.json"
R_BIN="$(pwd)/.pixi/envs/default/bin/R"
if [ -f "$KERNEL_JSON" ] && [ -x "$R_BIN" ]; then
    python3 - "$KERNEL_JSON" "$R_BIN" <<'PYEOF'
import json, sys
path, r_bin = sys.argv[1], sys.argv[2]
with open(path) as f:
    spec = json.load(f)
if spec.get("argv", [None])[0] != r_bin:
    spec["argv"][0] = r_bin
    with open(path, "w") as f:
        json.dump(spec, f, indent=1)
    print(f"  ir kernelspec -> {r_bin}")
else:
    print("  ir kernelspec already absolute; leaving as-is")
PYEOF
else
    echo "  ir kernelspec not found (R env not installed?); skipping"
fi

echo "post-create.sh done."
