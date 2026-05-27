#!/usr/bin/env bash
# Canonical run script for the `josh-mcp` arm — provided by the HARNESS, not the
# agent. The josh-mcp agent is constrained to Josh-via-MCP with no bash, so it
# authors only Josh source (`simulation.josh` + the `.jshd` it builds via the MCP
# preprocess tool) and never writes this script. The scorer materializes this file
# into the workspace (run_metrics.py, target=josh-mcp) and runs it once at the
# canonical 100-replicate scale, exactly like the other arms' `./run.sh`.
#
# These commands are byte-for-byte what `prompts/targets/josh-mcp.md` tells the
# agent to self-test with via the MCP tools, so a green agent self-test predicts a
# green scoring run. The convention the agent must follow:
#   - entry file `simulation.josh`, simulation `Main`;
#   - `external temperature` + `external precipitation` (bind by stem to the
#     temperature.jshd / precipitation.jshd built below);
#   - `exportFiles.patch = "file:///sandbox/output/results_{replicate}.csv"`.
#
# Units are the known facts we author (data/generate_synthetic_climate.py):
# `tasmax` is K; `pr` is the raw flux `kg m-2 s-1` — preprocess only *labels* the
# values, so the `.josh` model does the `× 31_536_000` → mm/year conversion. The
# synthetic netCDFs were copied into the workspace `data/` dir by the agent prelude.
set -euo pipefail
cd "$(dirname "$0")"
N_REPLICATES="${N_REPLICATES:-2}"
mkdir -p output

josh preprocess simulation.josh Main data/maxtemp_synthetic.nc tasmax K temperature.jshd
josh preprocess simulation.josh Main data/precip_synthetic.nc pr "kg m-2 s-1" precipitation.jshd
josh run simulation.josh Main --replicates "$N_REPLICATES" --data .
