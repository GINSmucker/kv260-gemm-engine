#!/bin/sh
set -eu
task_root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
: "${KV260_BUNDLE:?Set KV260_BUNDLE to the verified frozen model directory}"
: "${KV260_PYTHON:?Set KV260_PYTHON to the selected Python 3.12 executable}"
bundle_root=$KV260_BUNDLE
export PYTHONPATH="$bundle_root/source"
export OMP_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=4
cd "$task_root"
exec "$KV260_PYTHON" "$task_root/board_agent.py" --bundle "$bundle_root" --runs "$task_root/runs" --port 8740
