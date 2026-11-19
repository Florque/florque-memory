#!/usr/bin/env sh
# Build a memory skill from a LinkML ontology and link it into agents' skill directories.
#
#   scripts/install-skill.sh [--reinstall-cli] [ONTOLOGY] [TARGET ...]
#   scripts/install-skill.sh [--reinstall-cli] --project MANIFEST [TARGET ...]
#
# ONTOLOGY  a LinkML .yaml/.yml file, or the name of a bundled ontology in ontologies/
#           (default: workgraph). The skill is named <ontology name>-memory.
# MANIFEST  a project manifest (name, work, domain, optional domain_prefix): one work ontology
#           and one domain ontology in one graph. The skill is named <project name>-memory.
# TARGET    claude -> ~/.claude/skills, pi -> ~/.pi/agent/skills, or any directory (default: claude)
#
# Steps:
#   1. install the memory-engine CLI globally (uv tool install --editable <this repo>) if it is
#      not on PATH yet, so every generated skill shares one `mem`; --reinstall-cli forces it
#   2. generate SKILL.md, references/ and scripts/mem into <data dir>/skills/<name>-memory
#      ($MEMORY_DATA_DIR, else the per-user data dir). A project skill also copies its manifest
#      and both ontologies into references/
#   3. symlink that directory into each target
# Re-run after editing the ontology to regenerate the skill; the links stay valid.
# Each skill keeps its own database: <data dir>/<ontology name>.lbdb.
set -e
repo="$(cd "$(dirname "$0")/.." && pwd -P)"

reinstall=0
if [ "$1" = "--reinstall-cli" ]; then
  reinstall=1
  shift
fi

project=""
if [ "$1" = "--project" ]; then
  [ "$#" -ge 2 ] || { echo "error: --project needs a manifest path" >&2; exit 1; }
  project="$2"
  shift 2
fi
ontology="${1:-workgraph}"
if [ -z "$project" ] && [ "$#" -gt 0 ]; then shift; fi
[ "$#" -gt 0 ] || set -- claude

# 1. global CLI
if [ "$reinstall" = 1 ] || ! command -v memory-engine >/dev/null 2>&1; then
  if ! command -v uv >/dev/null 2>&1; then
    echo "error: uv is required to install the memory-engine CLI: https://docs.astral.sh/uv/" >&2
    exit 1
  fi
  echo "installing memory-engine CLI from $repo"
  uv tool install --force --editable "$repo" >/dev/null
  if ! command -v memory-engine >/dev/null 2>&1; then
    echo "error: memory-engine was installed but is not on PATH; run 'uv tool update-shell' and retry" >&2
    exit 1
  fi
fi

# 2. build the skill (accept a relative ontology path from the caller's directory)
if [ -n "$project" ]; then
  [ -f "$project" ] || { echo "error: manifest $project does not exist" >&2; exit 1; }
  project="$(cd "$(dirname "$project")" && pwd -P)/$(basename "$project")"
  if ! result="$(memory-engine skill build-project "$project")"; then
    echo "$result" >&2
    exit 1
  fi
else
  case "$ontology" in
    *.yaml|*.yml) [ -f "$ontology" ] && ontology="$(cd "$(dirname "$ontology")" && pwd -P)/$(basename "$ontology")" ;;
  esac
  if ! result="$(memory-engine skill build "$ontology")"; then
    echo "$result" >&2
    exit 1
  fi
fi
# the envelope is pretty-printed JSON with one `"key": "value",` per line
field() { printf '%s\n' "$result" | sed -n "s/^  \"$1\": \"\(.*\)\",\{0,1\}\$/\1/p"; }
skill="$(field skill)"
built="$(field path)"
echo "built $skill -> $built"

# 3. link into each target
for target in "$@"; do
  case "$target" in
    claude) dir="$HOME/.claude/skills" ;;
    pi) dir="$HOME/.pi/agent/skills" ;;
    *) dir="$target" ;;
  esac
  mkdir -p "$dir"
  if [ -L "$dir/graph-memory" ] && [ ! -e "$dir/graph-memory" ]; then
    rm "$dir/graph-memory"
    echo "removed stale legacy link $dir/graph-memory"
  fi
  if [ -e "$dir/$skill" ] && [ ! -L "$dir/$skill" ]; then
    echo "skip: $dir/$skill exists and is not a symlink" >&2
    continue
  fi
  ln -sfn "$built" "$dir/$skill"
  echo "linked $dir/$skill -> $built"
done
