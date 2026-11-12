#!/usr/bin/env sh
# Symlink the graph-memory skill into one or more agents' skill directories.
#   scripts/install-skill.sh claude        -> ~/.claude/skills/graph-memory
#   scripts/install-skill.sh pi            -> ~/.pi/agent/skills/graph-memory
#   scripts/install-skill.sh ./some/dir    -> ./some/dir/graph-memory
set -e
repo="$(cd "$(dirname "$0")/.." && pwd -P)"
skill="$repo/skills/graph-memory"
[ "$#" -gt 0 ] || set -- claude

for target in "$@"; do
  case "$target" in
    claude) dir="$HOME/.claude/skills" ;;
    pi) dir="$HOME/.pi/agent/skills" ;;
    *) dir="$target" ;;
  esac
  mkdir -p "$dir"
  if [ -e "$dir/graph-memory" ] && [ ! -L "$dir/graph-memory" ]; then
    echo "skip: $dir/graph-memory exists and is not a symlink" >&2
    continue
  fi
  ln -sfn "$skill" "$dir/graph-memory"
  echo "linked $dir/graph-memory -> $skill"
done
