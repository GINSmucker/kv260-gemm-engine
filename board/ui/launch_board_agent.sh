#!/bin/sh
set -eu
task_root=/home/ubuntu/fpga_project/ui_agent_v1
bundle_root=/home/ubuntu/fpga_project/model_b/releases/B_MODEL_BUNDLE_V1
export PYTHONPATH="$bundle_root/source"
export OMP_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=4
cd "$task_root"
exec /home/ubuntu/fpga_project/model_b/.venv/bin/python "$task_root/board_agent.py" --bundle "$bundle_root" --runs "$task_root/runs" --port 8740
