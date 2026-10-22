#!/bin/sh
# Runs inside the NuSMV container only. Each result is saved separately.
set -u
find /out/baseline /out/mutations /out/extended /out/tanstack /out/audit-history -name '*.smv' -type f | sort > /out/models.list
while IFS= read -r model; do
  started=$(date +%s)
  { time NuSMV -ctt "$model" > "$model.stdout" 2> "$model.stderr"; } 2> "$model.timing"
  code=$?
  echo "$code" > "$model.exitcode"
  finished=$(date +%s)
  echo "$((finished - started))" > "$model.seconds"
  if [ "$code" = 0 ]; then
    printf 'read_model -i %s\ngo\ncompute_reachable\nprint_reachable_states\nquit\n' "$model" > "$model.commands"
    NuSMV -source "$model.commands" > "$model.stats.stdout" 2> "$model.stats.stderr"
    echo "$?" > "$model.stats.exitcode"
  fi
  echo "$code $model"
done < /out/models.list
