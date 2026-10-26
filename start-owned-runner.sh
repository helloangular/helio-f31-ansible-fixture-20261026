#!/usr/bin/env bash
# Owner-run foreground command only. Builds F31 local targets; no acceptance case.
# Credentials remain GitHub secrets / private runner registration files.
# Teardown: Ctrl-C closes the runner, removes only this compose project's targets,
# and stops only Nexus-front services that this invocation itself started.
set -euo pipefail
fixture_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
product_dir="$HOME/medtronic-cicd"
runner_dir="$HOME/.helio-actions-runner-f31"
started_services=()
for service in nexus local-tls datadog-stub; do
  state="$(docker inspect --format '{{.State.Running}}' "medtronic-cicd-${service}-1" 2>/dev/null || true)"
  if [[ "$state" != true ]]; then started_services+=("$service"); fi
done
cleanup() {
  docker compose -f "$fixture_dir/compose.yml" down
  if [[ ${#started_services[@]} -gt 0 ]]; then
    (cd "$product_dir" && docker compose stop "${started_services[@]}")
  fi
}
trap cleanup EXIT
(cd "$product_dir" && docker compose up -d nexus local-tls)
docker compose -f "$fixture_dir/compose.yml" up -d
cd "$runner_dir"
./run.sh
