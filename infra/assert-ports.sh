#!/bin/sh
# §10.11: production publishes 0.0.0.0:80:80 and NOTHING else. Garage's
# admin listener (3903) is the sharpest of these — an unauthenticated
# control plane that would let any LAN device turn the private staging
# bucket into a website.
set -eu

CONFIG_JSON="$(docker compose -f compose.yaml -f compose.prod.yaml config --format json)"
echo "$CONFIG_JSON" | grep -E '"published"' || true

# Every published-port entry, one per line, as "service:published".
# `.services[] | select(.ports) | .name + ":" + (.ports[].published)`
# requires jq; use it when available and fall back to a portable grep/sed
# scan of the JSON otherwise so this works on any installed Compose/CLI
# combination.
if command -v jq >/dev/null 2>&1; then
  BAD="$(printf '%s' "$CONFIG_JSON" | jq -r '
    .services
    | to_entries[]
    | select(.value.ports != null)
    | .key as $svc
    | .value.ports[]
    | select((.published // "") != "80")
    | "\($svc): published \(.published)"
  ')"
else
  # Portable fallback: walk the JSON text service-by-service. Each
  # `"ports": [ ... ]` array's `"published": N` entries are attributed to
  # the nearest preceding top-level service key.
  BAD="$(printf '%s' "$CONFIG_JSON" | python3 -c '
import json, sys
cfg = json.load(sys.stdin)
bad = []
for name, svc in cfg.get("services", {}).items():
    for p in svc.get("ports") or []:
        published = str(p.get("published", ""))
        if published != "80":
            bad.append(f"{name}: published {published}")
print("\n".join(bad))
')"
fi

if [ -n "$BAD" ]; then
  echo "FATAL: a service other than caddy:80 publishes a port:" >&2
  echo "$BAD" >&2
  exit 1
fi

echo "assert-ports: ok — only caddy:80 is published"
