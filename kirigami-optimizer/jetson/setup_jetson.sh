#!/usr/bin/env bash
# Installs the kirigami optimizer's orchestrator on a Jetson (JetPack 6 = Ubuntu 22.04, aarch64, Python 3.10).
# NOT tested on real Jetson hardware: it was written from the x86 cloud session. Read the output of every step.
#
# Nothing heavy runs locally: SimScale's cloud does the meshing and solving. This process only builds a canopy
# (about 2.5 s, 210 MB), uploads it and polls SimScale (about 300 MB), so 8 GB is plenty.
set -euo pipefail
cd "$(dirname "$0")/.."

sudo apt-get update
sudo apt-get install -y python3-venv python3-dev build-essential git tmux

python3 -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -U pip setuptools wheel

# The semi-manual route needs neither OpenCascade nor manifold3d (only the experimental auto-flow routes do).
grep -v -E '^(cadquery-ocp|manifold3d)' requirements.txt > /tmp/requirements-jetson.txt
pip install -r /tmp/requirements-jetson.txt
pip install --no-build-isolation git+https://github.com/SimScaleGmbH/simscale-python-sdk.git

# Optional extras (skipped quietly if there is no aarch64 wheel for them).
pip install --only-binary=:all: manifold3d || echo "manifold3d not installed: the union/auto-flow tests will fail, the semi-manual route is unaffected"

if [ ! -f .env ]; then
  cp .env.example .env
  echo
  echo "Created .env from .env.example. Put your SIMSCALE_API_KEY in it with an editor (never paste it into chat or commit it)."
fi

# Without manifold3d/OpenCascade the tests of the experimental auto-flow routes cannot run; the rest must pass.
python -m pytest -q tests --ignore=tests/test_step_export.py \
  -k "not union and not resume and not other_cut_counts and not flow_domain and not sting and not flush" || true
echo
echo "Setup done. Next: see jetson/README.md (restore state, run a batch in tmux)."
