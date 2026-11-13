#!/usr/bin/env sh
# Browse a memory graph in Ladybug Explorer, built to match the Ladybug version memory-engine uses.
#
#   scripts/explore.sh [ONTOLOGY] [--port 8000] [--rebuild] [--live]
#
# ONTOLOGY   bundled name or LinkML file (default: workgraph); MEMORY_DB_PATH overrides the file.
# --port     host port (default 8000)
# --rebuild  rebuild the Explorer image even if one exists for this Ladybug version
# --live     mount the real database read-only instead of a snapshot (do not write with `mem`
#            meanwhile: the container cannot see Ladybug's file lock)
#
# Why build: the published ghcr.io/ladybugdb/explorer image lags behind (it reads an older storage
# format), so we build Explorer from its MIT source with @ladybugdb/core pinned to our version.
# The image is cached as memory-engine/ladybug-explorer:<version>.
# By default the database is copied to <data dir>/explorer/<name>/ first; re-run to refresh it.
set -e
repo="$(cd "$(dirname "$0")/.." && pwd -P)"
EXPLORER_REPO="https://github.com/LadybugDB/explorer.git"
EXPLORER_COMMIT="cc507f0cbbeec7691044c0d65a2bf5a87b3a26c7"  # master, 2026-08-08

ontology=workgraph
port=8000
rebuild=0
live=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --port) port="$2"; shift 2 ;;
    --rebuild) rebuild=1; shift ;;
    --live) live=1; shift ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) ontology="$1"; shift ;;
  esac
done

die() { echo "error: $*" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || die "docker is required"
docker info >/dev/null 2>&1 || die "the Docker daemon is not running"

if command -v memory-engine >/dev/null 2>&1; then
  mem() { memory-engine "$@"; }
else
  mem() { uv run --quiet --project "$repo" memory-engine "$@"; }
fi

# 1. which database, which Ladybug version (the envelope is pretty-printed, one key per line)
info="$(mem --ontology "$ontology" db-path)" || die "$info"
field() {  # quoted strings, or bare values such as true/false/null
  printf '%s\n' "$info" | sed -n -e "s/^  \"$1\": \"\(.*\)\",\{0,1\}\$/\1/p" -e "s/^  \"$1\": \([^\",]*\),\{0,1\}\$/\1/p"
}
db="$(field path)"
version="$(field ladybug_version)"
name="$(basename "$db" .lbdb)"
[ "$(field backend)" = "ladybug" ] || die "Explorer only works with the Ladybug backend"
[ "$(field exists)" = "true" ] || die "no database yet at $db (write something with mem first)"
[ -n "$version" ] || die "cannot tell which Ladybug version memory-engine uses"

# 2. snapshot (default) or live read-only mount
if [ "$live" = 1 ]; then
  mount_dir="$(dirname "$db")"
  mode=READ_ONLY
  echo "warning: serving the live file read-only; avoid mem writes while Explorer runs" >&2
else
  if command -v lsof >/dev/null 2>&1; then
    holders="$(lsof -t "$db" 2>/dev/null || true)"
    [ -z "$holders" ] || die "$db is open by process(es) $holders; retry when it is idle"
  else
    echo "warning: lsof not found, cannot check that $db is idle before copying" >&2
  fi
  data_dir="$(dirname "$db")"
  mount_dir="$data_dir/explorer/$name"
  mkdir -p "$mount_dir"
  rm -f "$mount_dir/$name.lbdb" "$mount_dir/$name.lbdb.wal"
  cp "$db" "$mount_dir/$name.lbdb"
  [ ! -f "$db.wal" ] || cp "$db.wal" "$mount_dir/$name.lbdb.wal"
  mode=READ_WRITE  # a disposable copy; Explorer may write its own metadata
  echo "snapshot $db -> $mount_dir"
fi

# 3. Explorer image matching our Ladybug version
image="memory-engine/ladybug-explorer:$version"
if [ "$rebuild" = 1 ] || ! docker image inspect "$image" >/dev/null 2>&1; then
  npm_ok="$(curl -fsS "https://registry.npmjs.org/@ladybugdb/core/$version" >/dev/null 2>&1 && echo yes || echo no)"
  [ "$npm_ok" = yes ] || die "@ladybugdb/core $version is not on npm, so Explorer cannot be built for it yet"
  src="$(mktemp -d)"
  trap 'rm -rf "$src"' EXIT
  echo "building $image from $EXPLORER_REPO@${EXPLORER_COMMIT%"${EXPLORER_COMMIT#???????}"} (a few minutes the first time)"
  git -C "$src" init -q
  git -C "$src" fetch -q --depth 1 "$EXPLORER_REPO" "$EXPLORER_COMMIT"
  git -C "$src" checkout -q FETCH_HEAD
  # pin the database engine to the version memory-engine writes
  sed -i.bak -E "s#(\"@ladybugdb/(core|wasm-core)\": *)\"[^\"]*\"#\1\"$version\"#" "$src/package.json"
  rm -f "$src/package.json.bak" "$src/package-lock.json"
  grep -q "\"@ladybugdb/core\": \"$version\"" "$src/package.json" || die "could not pin @ladybugdb/core in Explorer's package.json"
  docker build --build-arg SKIP_DATASETS=true -t "$image" "$src"
fi

# 4. run
container="memory-explorer-$name"
docker rm -f "$container" >/dev/null 2>&1 || true
docker run -d --rm --name "$container" -p "$port:8000" \
  -v "$mount_dir:/database" -e LBUG_FILE="$name.lbdb" -e MODE="$mode" "$image" >/dev/null
i=0
until curl -fsS -o /dev/null "http://localhost:$port/" 2>/dev/null; do
  i=$((i + 1))
  if [ "$i" -gt 60 ] || ! docker ps -q --filter "name=^$container\$" | grep -q .; then
    docker logs "$container" 2>&1 | tail -20 >&2 || true
    die "Explorer did not start"
  fi
  sleep 1
done
cat <<EOF
Explorer for $name ($mode, Ladybug $version): http://localhost:$port
Stop it with: docker stop $container
Try:  MATCH (a)-[r]->(b) RETURN a, r, b LIMIT 300;
EOF
