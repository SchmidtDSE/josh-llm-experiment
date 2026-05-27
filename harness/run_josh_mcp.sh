#!/usr/bin/env bash
# Canonical run script for the josh-mcp arm, materialized into the workspace by
# the scorer (the constrained agent authors only Josh source, never run.sh).
set -euo pipefail
cd "$(dirname "$0")"
N_REPLICATES="${N_REPLICATES:-2}"
mkdir -p output
rm -f output/results*.csv

josh preprocess --x-coord lon --y-coord lat --time-dim calendar_year \
  simulation.josh Main data/maxtemp_synthetic.nc tasmax K data/temperature.jshd
josh preprocess --x-coord lon --y-coord lat --time-dim calendar_year \
  simulation.josh Main data/precip_synthetic.nc pr "kg m-2 s-1" data/precipitation.jshd
josh run simulation.josh Main --replicates "$N_REPLICATES" \
  --data temperature.jshd=data/temperature.jshd \
  --data precipitation.jshd=data/precipitation.jshd
