#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Tencent. All rights reserved.
#
# Render a CVM Prometheus scrape job for Cubelet /v1/metrics/resource.
# Does not install a collector. See 方案.md in this directory.
set -euo pipefail

TOOLBOX_ROOT="/usr/local/services/cubetoolbox"
ENV_FILE="${ONE_CLICK_RUNTIME_ENV_FILE:-${TOOLBOX_ROOT}/.one-click.env}"

log() { echo "[cube-observability] $*" >&2; }
die() { echo "[cube-observability] ERROR: $*" >&2; exit 1; }

yaml_dquote() {
  local value="$1"
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  printf '"%s"' "${value}"
}

require_safe_label() {
  local name="$1"
  local value="$2"
  [[ "${value}" =~ ^[A-Za-z0-9_.:@/-]+$ ]] || die "${name} contains characters that cannot be written into the scrape config"
}

load_one_click_env() {
  if [[ -f "${ENV_FILE}" ]]; then
    # shellcheck disable=SC1090
    set -a
    source "${ENV_FILE}"
    set +a
    log "loaded ${ENV_FILE}"
  fi
}

node_ip() {
  if [[ -n "${CUBE_SANDBOX_NODE_IP:-}" ]]; then
    printf '%s\n' "${CUBE_SANDBOX_NODE_IP}"
    return
  fi
  ip -4 route get 1.1.1.1 2>/dev/null | awk '{
    for (i = 1; i <= NF; i++) if ($i == "src") { print $(i + 1); exit }
  }'
}

write_prometheus_config() {
  local output="${CUBE_METRICS_PROM_CONFIG:-${PWD}/cubesandbox-prometheus-scrape.yml}"
  local cluster="${CUBE_OBSERVABILITY_CLUSTER_ID:-${CUBE_METRICS_CLUSTER_ID:-cubesandbox}}"
  require_safe_label "CUBE_OBSERVABILITY_CLUSTER_ID" "${cluster}"
  if [[ -n "${CUBE_METRICS_NODE_LIST:-}" ]]; then
    [[ -f "${CUBE_METRICS_NODE_LIST}" ]] || die "node list not found: ${CUBE_METRICS_NODE_LIST}"
    {
      cat <<EOF
scrape_configs:
  - job_name: cubesandbox-resource
    scrape_interval: 5s
    scrape_timeout: 3s
    metrics_path: /v1/metrics/resource
    static_configs:
EOF
      while IFS= read -r node || [[ -n "${node}" ]]; do
        [[ -z "${node}" || "${node}" == \#* ]] && continue
        require_safe_label "node" "${node}"
        cat <<EOF
      - targets: ["${node}:9998"]
        labels:
          cluster: $(yaml_dquote "${cluster}")
          node: $(yaml_dquote "${node}")
EOF
      done < "${CUBE_METRICS_NODE_LIST}"
    } > "${output}.tmp"
  else
    load_one_click_env
    local node
    node="$(node_ip)"
    [[ -n "${node}" ]] || die "set CUBE_METRICS_NODE_LIST or CUBE_SANDBOX_NODE_IP"
    require_safe_label "node" "${node}"
    cat > "${output}.tmp" <<EOF
scrape_configs:
  - job_name: cubesandbox-resource
    scrape_interval: 5s
    scrape_timeout: 3s
    metrics_path: /v1/metrics/resource
    static_configs:
      - targets: ["${node}:9998"]
        labels:
          cluster: $(yaml_dquote "${cluster}")
          node: $(yaml_dquote "${node}")
EOF
  fi
  mv "${output}.tmp" "${output}"
  log "wrote ${output}"
  log "merge this job into the existing Prometheus"
  log "Cubelet listens on all addresses. Allow only the Prometheus server to reach port 9998"
}

write_prometheus_config
