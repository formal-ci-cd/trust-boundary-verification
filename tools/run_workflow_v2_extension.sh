#!/bin/sh
# Host orchestration only; all Python, tests and NuSMV run in containers.
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
out=${1:?Usage: run_workflow_v2_extension.sh NEW_OUTPUT_DIRECTORY}
case "$out" in /*) ;; *) out="$repo/$out" ;; esac
if [ -e "$out" ]; then
  echo 'Output directory already exists; refusing to overwrite evidence.' >&2
  exit 2
fi
mkdir -p "$out"
git -C "$repo" status --porcelain=v1 -uall > "$out/git-status-before.txt"
git -C "$repo" rev-parse HEAD > "$out/git-head-before.txt"
analysis_image=${V2_ANALYSIS_IMAGE:-ubuntu-dev:latest}
nusmv_image=${V2_NUSMV_IMAGE:-badouralix/nusmv:latest}
docker image inspect "$analysis_image" "$nusmv_image" --format '{{.Id}} {{.Architecture}} {{json .RepoDigests}}' > "$out/docker-images.txt"
docker run --rm --network none --read-only --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges \
  --memory 2g --cpus 2 --pids-limit 128 --tmpfs /tmp:rw,nosuid,nodev,size=512m \
  -e PYTHONDONTWRITEBYTECODE=1 -v "$repo:/repo:ro" -v "$out:/out:rw" -w /repo "$analysis_image" sh -c '
    python3 -m unittest discover -s tests -p "test_workflow*v2*.py" -v > /out/regression-v2.log 2>&1 || exit 1
    python3 -m unittest discover -s tests -p test_workflow_to_smv.py -v > /out/regression-base.log 2>&1 || exit 1
    python3 -m unittest discover -s tests -p test_workflow_evidence.py -v > /out/regression-evidence.log 2>&1 || exit 1
    python3 tools/evaluate_workflow_v2_extension.py generate > /out/generation.log 2>&1
  '
docker run --rm --platform linux/amd64 --network none --read-only --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges \
  --memory 2g --cpus 2 --pids-limit 128 --tmpfs /tmp:rw,nosuid,nodev,size=256m \
  -v "$repo:/repo:ro" -v "$out:/out:rw" --entrypoint sh "$nusmv_image" /repo/tools/verify_v2_extension_models.sh > "$out/verification.log" 2>&1
docker run --rm --network none --read-only --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges \
  --memory 2g --cpus 2 --pids-limit 128 --tmpfs /tmp:rw,nosuid,nodev,size=512m \
  -e PYTHONDONTWRITEBYTECODE=1 -v "$repo:/repo:ro" -v "$out:/out:rw" -w /repo "$analysis_image" \
  python3 tools/evaluate_workflow_v2_extension.py summarize > "$out/collection.log" 2>&1
git -C "$repo" status --porcelain=v1 -uall > "$out/git-status-after.txt"
if [ "${V2_KEEP_RAW:-0}" != 1 ]; then
  docker run --rm --network none --read-only --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges \
    --memory 2g --cpus 2 --pids-limit 128 --tmpfs /tmp:rw,nosuid,nodev,size=256m \
    -e PYTHONDONTWRITEBYTECODE=1 -v "$repo:/repo:ro" -v "$out:/out:rw" -w /repo "$analysis_image" \
    python3 -m tools.workflow_model_v2.evidence /out
fi
echo "Saved evaluation: $out (set V2_KEEP_RAW=1 only when loose raw details are needed)"
