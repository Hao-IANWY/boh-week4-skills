#!/usr/bin/env bash
# Prepare clean TSV files only. Plotting is performed exclusively by plot.R.
set -euo pipefail
RUN_DIR="${1:-$(pwd)}"
cd "$RUN_DIR"
python3 -B scripts/prepare_data.py --plan run_plan.json --out .
printf '[data_prep] Generated cleaned_data.tsv and figure_N.tsv files in %s\n' "$RUN_DIR"
