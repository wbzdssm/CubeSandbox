#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Tencent. All rights reserved.
#
# Install a central Prometheus server for CVM deployments. Compute-node
# collectors push metrics into this server via OTLP/HTTP (/api/v1/otlp/v1/metrics)
# or Prometheus Remote Write (/api/v1/write); this script does not scrape
# Cubelet endpoints directly.
set -euo pipefail

PROMETHEUS_VERSION="3.13.4"
PROMETHEUS_USER="cube-prometheus"
PROMETHEUS_GROUP="cube-prometheus"
CONFIG_DIR="/etc/cube-sandbox"
CONFIG_PATH="${CONFIG_DIR}/prometheus.yml"
WEB_CONFIG_PATH="${CONFIG_DIR}/prometheus-web.yml"
STATE_DIR="/var/lib/cube-sandbox/prometheus"
BIN_PATH="/usr/local/bin/prometheus"
PROMTOOL_PATH="/usr/local/bin/promtool"
UNIT_NAME="cube-sandbox-prometheus.service"
UNIT_PATH="/etc/systemd/system/${UNIT_NAME}"

log() { echo "[cube-prometheus] $*" >&2; }
die() { echo "[cube-prometheus] ERROR: $*" >&2; exit 1; }

require_root() {
  [[ "$(id -u)" -eq 0 ]] || die "run as root"
}

require_single_line() {
  local name="$1"
  local value="$2"
  [[ "${value}" != *$'\n'* && "${value}" != *$'\r'* ]] || die "${name} must be a single line"
}

listen_address() {
  printf '%s' "${CUBE_PROMETHEUS_LISTEN_ADDRESS:-${CUBE_PROMETHEUS_LISTEN_ADDR:-0.0.0.0:9092}}"
}

listen_host() {
  local address
  address="$(listen_address)"
  printf '%s' "${address%:*}"
}

listen_port() {
  local address
  address="$(listen_address)"
  printf '%s' "${address##*:}"
}

health_url() {
  if [[ -n "${CUBE_PROMETHEUS_HEALTH_URL:-}" ]]; then
    printf '%s' "${CUBE_PROMETHEUS_HEALTH_URL%/}"
    return
  fi
  local host
  host="$(listen_host)"
  case "${host}" in
    0.0.0.0) host="127.0.0.1" ;;
  esac
  printf 'http://%s:%s' "${host}" "$(listen_port)"
}

require_config() {
  local address host port retention_time retention_size
  address="$(listen_address)"
  [[ -n "${address}" ]] || die "set CUBE_PROMETHEUS_LISTEN_ADDRESS to an explicit private IPv4 address and port, for example 10.0.0.10:9092"
  require_single_line "CUBE_PROMETHEUS_LISTEN_ADDRESS" "${address}"
  [[ "${address}" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}:[0-9]+$ ]] || \
    die "CUBE_PROMETHEUS_LISTEN_ADDRESS must be an IPv4 address and port"
  host="${address%:*}"
  port="${address##*:}"
  [[ "${port}" -ge 1 && "${port}" -le 65535 ]] || die "Prometheus listen port must be between 1 and 65535"
  [[ "${port}" != "9090" ]] || die "port 9090 is reserved by cube-proxy; use port 9092"
  if [[ "${host}" == "0.0.0.0" ]]; then
    log "WARNING: Prometheus will listen on all interfaces; restrict access with a firewall and web authentication"
  fi

  retention_time="${CUBE_PROMETHEUS_RETENTION_TIME:-15d}"
  retention_size="${CUBE_PROMETHEUS_RETENTION_SIZE:-40GB}"
  require_single_line "CUBE_PROMETHEUS_RETENTION_TIME" "${retention_time}"
  require_single_line "CUBE_PROMETHEUS_RETENTION_SIZE" "${retention_size}"
  [[ "${retention_time}" =~ ^[0-9]+(ms|s|m|h|d|w|y)$ ]] || die "invalid CUBE_PROMETHEUS_RETENTION_TIME"
  [[ "${retention_size}" =~ ^[0-9]+(B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)$ ]] || die "invalid CUBE_PROMETHEUS_RETENTION_SIZE"

  require_single_line "CUBE_PROMETHEUS_HEALTH_URL" "${CUBE_PROMETHEUS_HEALTH_URL:-}"
  require_single_line "CUBE_PROMETHEUS_HEALTH_AUTHORIZATION" "${CUBE_PROMETHEUS_HEALTH_AUTHORIZATION:-}"
  if [[ -n "${CUBE_PROMETHEUS_WEB_CONFIG_FILE:-}" ]]; then
    [[ -f "${CUBE_PROMETHEUS_WEB_CONFIG_FILE}" ]] || die "web config not found: ${CUBE_PROMETHEUS_WEB_CONFIG_FILE}"
  fi
}

arch_name() {
  case "$(uname -m)" in
    x86_64|amd64) printf 'amd64\n' ;;
    aarch64|arm64) printf 'arm64\n' ;;
    *) die "unsupported architecture: $(uname -m)" ;;
  esac
}

install_binaries() {
  if [[ -n "${CUBE_PROMETHEUS_BIN:-}" || -n "${CUBE_PROMTOOL_BIN:-}" ]]; then
    [[ -n "${CUBE_PROMETHEUS_BIN:-}" && -x "${CUBE_PROMETHEUS_BIN}" ]] || \
      die "CUBE_PROMETHEUS_BIN must identify an executable when custom binaries are used"
    [[ -n "${CUBE_PROMTOOL_BIN:-}" && -x "${CUBE_PROMTOOL_BIN}" ]] || \
      die "CUBE_PROMTOOL_BIN must identify an executable when custom binaries are used"
    install -m 0755 "${CUBE_PROMETHEUS_BIN}" "${BIN_PATH}"
    install -m 0755 "${CUBE_PROMTOOL_BIN}" "${PROMTOOL_PATH}"
    return
  fi

  if [[ -x "${BIN_PATH}" && -x "${PROMTOOL_PATH}" ]] &&
    "${BIN_PATH}" --version 2>&1 | grep -Eq "version ${PROMETHEUS_VERSION}([[:space:]]|$)"; then
    log "Prometheus ${PROMETHEUS_VERSION} already installed"
    return
  fi

  local arch asset base tmp expected actual
  arch="$(arch_name)"
  asset="prometheus-${PROMETHEUS_VERSION}.linux-${arch}.tar.gz"
  base="https://github.com/prometheus/prometheus/releases/download/v${PROMETHEUS_VERSION}"
  tmp="$(mktemp -d)"
  trap 'rm -rf "${tmp:-}"' RETURN

  log "downloading ${base}/${asset}"
  curl -fsSL "${base}/sha256sums.txt" -o "${tmp}/sha256sums.txt"
  curl -fsSL "${base}/${asset}" -o "${tmp}/${asset}"
  expected="$(awk -v asset="${asset}" '$2 == asset { print $1; exit }' "${tmp}/sha256sums.txt")"
  [[ -n "${expected}" ]] || die "checksum for ${asset} is missing from the release manifest"
  actual="$(sha256sum "${tmp}/${asset}" | awk '{print $1}')"
  [[ "${actual}" == "${expected}" ]] || die "checksum mismatch for ${asset}"

  tar -C "${tmp}" -xzf "${tmp}/${asset}"
  install -m 0755 "${tmp}/prometheus-${PROMETHEUS_VERSION}.linux-${arch}/prometheus" "${BIN_PATH}"
  install -m 0755 "${tmp}/prometheus-${PROMETHEUS_VERSION}.linux-${arch}/promtool" "${PROMTOOL_PATH}"
  rm -rf "${tmp}"
  trap - RETURN
}

ensure_user() {
  if ! getent group "${PROMETHEUS_GROUP}" >/dev/null; then
    groupadd --system "${PROMETHEUS_GROUP}"
  fi
  if ! id -u "${PROMETHEUS_USER}" >/dev/null 2>&1; then
    useradd --system --gid "${PROMETHEUS_GROUP}" --home-dir "${STATE_DIR}" \
      --shell /sbin/nologin "${PROMETHEUS_USER}"
  fi
}

write_prometheus_config() {
  local destination="$1"
  mkdir -p "$(dirname "${destination}")"
  cat > "${destination}" <<'EOF'
global:
  scrape_interval: 15s
  evaluation_interval: 15s

storage:
  tsdb:
    out_of_order_time_window: 30m

otlp:
  translation_strategy: UnderscoreEscapingWithSuffixes
  promote_resource_attributes:
    - cluster
    - node
    - cubesandbox.cluster.id
    - host.name
    - service.name
    - service.instance.id

# Compute-node collectors push metrics to /api/v1/otlp/v1/metrics (OTLP/HTTP)
# or /api/v1/write (Prometheus Remote Write). Cubelet targets are not scraped
# here, which prevents duplicate ingestion of the same series.
scrape_configs: []
EOF
}

write_unit() {
  local destination="$1"
  local web_config_arg=""
  if [[ -f "${WEB_CONFIG_PATH}" ]]; then
    web_config_arg=" \\
  --web.config.file=${WEB_CONFIG_PATH}"
  fi
  cat > "${destination}" <<EOF
[Unit]
Description=CubeSandbox central Prometheus
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${PROMETHEUS_USER}
Group=${PROMETHEUS_GROUP}
ExecStart=${BIN_PATH} \\
  --config.file=${CONFIG_PATH} \\
  --storage.tsdb.path=${STATE_DIR} \\
  --storage.tsdb.retention.time=${CUBE_PROMETHEUS_RETENTION_TIME:-15d} \\
  --storage.tsdb.retention.size=${CUBE_PROMETHEUS_RETENTION_SIZE:-40GB} \\
  --web.listen-address=$(listen_address) \\
  --web.enable-otlp-receiver \\
  --web.enable-remote-write-receiver \\
  --web.enable-lifecycle${web_config_arg}
ExecReload=/bin/kill -HUP \$MAINPID
Restart=on-failure
RestartSec=5s
LimitNOFILE=1048576
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=${STATE_DIR}

[Install]
WantedBy=multi-user.target
EOF
}

check_port_available() {
  local port
  port="$(listen_port)"
  if systemctl is-active --quiet "${UNIT_NAME}" 2>/dev/null; then
    return
  fi
  if ss -H -lnt 2>/dev/null | awk -v port=":${port}" '$4 ~ port "$" { found=1 } END { exit !found }'; then
    die "TCP port ${port} is already in use"
  fi
}

install_service() {
  require_root
  require_config
  install_binaries
  ensure_user
  check_port_available

  install -d -m 0750 -o root -g "${PROMETHEUS_GROUP}" "${CONFIG_DIR}"
  install -d -m 0700 -o "${PROMETHEUS_USER}" -g "${PROMETHEUS_GROUP}" "${STATE_DIR}"

  local config_tmp unit_tmp
  config_tmp="$(mktemp)"
  unit_tmp="$(mktemp)"
  write_prometheus_config "${config_tmp}"
  install -m 0640 -o root -g "${PROMETHEUS_GROUP}" "${config_tmp}" "${CONFIG_PATH}"
  rm -f "${config_tmp}"
  "${PROMTOOL_PATH}" check config "${CONFIG_PATH}"

  rm -f "${WEB_CONFIG_PATH}"
  if [[ -n "${CUBE_PROMETHEUS_WEB_CONFIG_FILE:-}" ]]; then
    install -m 0640 -o root -g "${PROMETHEUS_GROUP}" \
      "${CUBE_PROMETHEUS_WEB_CONFIG_FILE}" "${WEB_CONFIG_PATH}"
    "${PROMTOOL_PATH}" check web-config "${WEB_CONFIG_PATH}"
  fi

  write_unit "${unit_tmp}"
  install -m 0644 "${unit_tmp}" "${UNIT_PATH}"
  rm -f "${unit_tmp}"
  systemctl daemon-reload
  systemctl enable "${UNIT_NAME}"
  systemctl restart "${UNIT_NAME}"
  sleep 2
  systemctl is-active --quiet "${UNIT_NAME}" || \
    die "Prometheus failed to start; inspect journalctl -u ${UNIT_NAME}"

  local -a headers=()
  if [[ -n "${CUBE_PROMETHEUS_HEALTH_AUTHORIZATION:-}" ]]; then
    headers=(-H "Authorization: ${CUBE_PROMETHEUS_HEALTH_AUTHORIZATION}")
  fi
  curl -fsS --max-time 5 "${headers[@]}" "$(health_url)/-/ready" >/dev/null
  log "Prometheus is ready at $(health_url)"
  log "OTLP/HTTP metrics endpoint: $(health_url)/api/v1/otlp/v1/metrics (collector base endpoint: $(health_url)/api/v1/otlp)"
  log "remote write endpoint: $(health_url)/api/v1/write"
}

render_config() {
  require_config
  local output="${CUBE_PROMETHEUS_CONFIG_OUTPUT:-${CUBE_PROMETHEUS_CONFIG:-${PWD}/cubesandbox-prometheus.yml}}"
  write_prometheus_config "${output}.tmp"
  mv "${output}.tmp" "${output}"
  log "wrote ${output}"
}

uninstall_service() {
  require_root
  systemctl disable --now "${UNIT_NAME}" 2>/dev/null || true
  rm -f "${UNIT_PATH}" "${CONFIG_PATH}" "${WEB_CONFIG_PATH}"
  systemctl daemon-reload
  log "removed ${UNIT_NAME}; retained ${BIN_PATH}, ${PROMTOOL_PATH}, and ${STATE_DIR}"
}

case "${1:-install}" in
  install) install_service ;;
  render) render_config ;;
  uninstall) uninstall_service ;;
  *) die "usage: $0 [install|render|uninstall]" ;;
esac
