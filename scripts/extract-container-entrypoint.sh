#!/bin/sh
# Extract a container's full entrypoint payload (e.g. the supabase CLI heredoc
# entrypoints for kong/db) into a runnable script file.
#
# Usage: ./extract-container-entrypoint.sh <container-name> [outfile]
#
# The output is meant to be copied to the migration target, bind-mounted
# read-only into the recreated container, and used as its command:
#   podman run ... --entrypoint /bin/sh <image> /migration-src/<name>-entrypoint.sh
#
# NOTE: payloads can contain deployment-specific material (declarative route
# configs, generated keys). Keep them out of public repositories.

set -eu

container="${1:?usage: $0 <container-name> [outfile]}"
out="${2:-${container}-entrypoint.sh}"

index=$(podman container inspect "$container" --format '{{len .Config.Entrypoint}}')
if [ "$index" -lt 3 ]; then
	echo "container '$container' has no multi-part entrypoint payload" >&2
	exit 1
fi

podman container inspect "$container" --format '{{index .Config.Entrypoint 2}}' > "$out"
chmod 755 "$out"
echo "wrote $out (${index} part entrypoint; last part extracted)"
