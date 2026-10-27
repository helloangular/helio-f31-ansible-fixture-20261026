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
ready=false
for attempt in {1..90}; do
  status="$(curl --silent --cacert "$HOME/.helio-tomcat/tls/local-tls/cert.pem" --max-time 3 --output /dev/null --write-out '%{http_code}' https://localhost:9444/service/rest/v1/status || true)"
  if [[ "$status" == 200 ]]; then ready=true; break; fi
  sleep 2
done
if [[ "$ready" != true ]]; then
  echo 'Local Nexus did not become ready; runner remains stopped.' >&2
  exit 1
fi
for port in 3461 3462 3463; do
  ready=false
  for attempt in {1..30}; do
    status="$(curl --silent --max-time 3 --output /dev/null --write-out '%{http_code}' "http://127.0.0.1:$port/" || true)"
    if [[ "$status" == 200 || "$status" == 404 ]]; then ready=true; break; fi
    sleep 2
  done
  if [[ "$ready" != true ]]; then
    echo "Owned Tomcat on $port did not become ready; runner remains stopped." >&2
    exit 1
  fi
done
echo 'F31 local services ready. Starting the repo-only runner in this foreground terminal.'
cd "$runner_dir"
python3 "$fixture_dir/runner_env.py" ./run.sh
