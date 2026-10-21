#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2026 Tencent. All rights reserved.
#
# Kubernetes / TKE compute-node collector (DaemonSet cube-node-otelcol).
# Supports both metrics (OTLP/HTTP or Prometheus Remote Write) and logs
# (Elasticsearch or OTLP/HTTP). See 方案.md in this directory.
set -euo pipefail

OTELCOL_VERSION="0.162.0"
K8S_NAME="cube-node-otelcol"
DEFAULT_NAMESPACE="cube-system"
DEFAULT_IMAGE="otel/opentelemetry-collector-contrib:${OTELCOL_VERSION}"

log() { echo "[cube-observability-k8s] $*" >&2; }
die() { echo "[cube-observability-k8s] ERROR: $*" >&2; exit 1; }

yaml_dquote() {
  local value="$1"
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  printf '"%s"' "${value}"
}

require_safe_label() {
  local name="$1"
  local value="$2"
  [[ "${value}" =~ ^[A-Za-z0-9_.:@/-]+$ ]] || die "${name} contains characters that cannot be written into the collector config"
}

require_safe_absolute_path() {
  local name="$1"
  local value="$2"
  [[ "${value}" =~ ^/[A-Za-z0-9._/-]+$ ]] || \
    die "${name} must be an absolute path containing only letters, digits, '.', '_', '-', and '/'"
}

require_single_line() {
  local name="$1"
  local value="$2"
  [[ "${value}" != *$'\n'* && "${value}" != *$'\r'* ]] || die "${name} must be a single line"
}

require_http_url() {
  local name="$1"
  local value="$2"
  [[ "${value}" == http://* || "${value}" == https://* ]] || die "${name} must start with http:// or https://"
  require_single_line "${name}" "${value}"
}

require_bool() {
  local name="$1"
  local value="$2"
  [[ "${value}" == "true" || "${value}" == "false" ]] || die "${name} must be true or false"
}

require_k8s_config() {
  local es_endpoint="${CUBE_LOGS_ES_ENDPOINT:-${CUBE_LOGS_ELASTICSEARCH_ENDPOINT:-}}"
  [[ -n "${CUBE_METRICS_REMOTE_WRITE_URL:-}" || -n "${CUBE_METRICS_OTLP_HTTP_ENDPOINT:-}" || -n "${es_endpoint}" || -n "${CUBE_LOGS_OTLP_HTTP_ENDPOINT:-}" ]] || \
    die "set CUBE_METRICS_OTLP_HTTP_ENDPOINT, CUBE_METRICS_REMOTE_WRITE_URL, CUBE_LOGS_ES_ENDPOINT, or CUBE_LOGS_OTLP_HTTP_ENDPOINT"
  if [[ -n "${CUBE_METRICS_REMOTE_WRITE_URL:-}" && -n "${CUBE_METRICS_OTLP_HTTP_ENDPOINT:-}" ]]; then
    die "set only one of CUBE_METRICS_OTLP_HTTP_ENDPOINT and CUBE_METRICS_REMOTE_WRITE_URL"
  fi
  if [[ -n "${es_endpoint}" && -n "${CUBE_LOGS_OTLP_HTTP_ENDPOINT:-}" ]]; then
    die "set only one of CUBE_LOGS_ES_ENDPOINT and CUBE_LOGS_OTLP_HTTP_ENDPOINT"
  fi
  if [[ -n "${CUBE_METRICS_REMOTE_WRITE_URL:-}" ]]; then
    require_http_url "CUBE_METRICS_REMOTE_WRITE_URL" "${CUBE_METRICS_REMOTE_WRITE_URL}"
  fi
  if [[ -n "${CUBE_METRICS_OTLP_HTTP_ENDPOINT:-}" ]]; then
    require_http_url "CUBE_METRICS_OTLP_HTTP_ENDPOINT" "${CUBE_METRICS_OTLP_HTTP_ENDPOINT}"
    case "${CUBE_METRICS_OTLP_HTTP_ENDPOINT%/}" in
      */v1/metrics) die "CUBE_METRICS_OTLP_HTTP_ENDPOINT must be the base endpoint (e.g. http://<prom>:9092/api/v1/otlp); remove /v1/metrics" ;;
    esac
  fi
  if [[ -n "${es_endpoint}" ]]; then
    require_http_url "CUBE_LOGS_ES_ENDPOINT" "${es_endpoint}"
    require_safe_label "CUBE_LOGS_ES_INDEX" "${CUBE_LOGS_ES_INDEX:-cubesandbox-logs}"
    require_single_line "CUBE_LOGS_ES_USER" "${CUBE_LOGS_ES_USER:-}"
    require_single_line "CUBE_LOGS_ES_PASSWORD" "${CUBE_LOGS_ES_PASSWORD:-}"
    require_single_line "CUBE_LOGS_ES_API_KEY" "${CUBE_LOGS_ES_API_KEY:-}"
    require_bool "CUBE_LOGS_ES_TLS_INSECURE_SKIP_VERIFY" "${CUBE_LOGS_ES_TLS_INSECURE_SKIP_VERIFY:-false}"
    if [[ -n "${CUBE_LOGS_ES_API_KEY:-}" &&
          ( -n "${CUBE_LOGS_ES_USER:-}" || -n "${CUBE_LOGS_ES_PASSWORD:-}" ) ]]; then
      die "set either CUBE_LOGS_ES_API_KEY or CUBE_LOGS_ES_USER/CUBE_LOGS_ES_PASSWORD, not both"
    fi
    if [[ -n "${CUBE_LOGS_ES_USER:-}" && -z "${CUBE_LOGS_ES_PASSWORD:-}" ]] ||
       [[ -z "${CUBE_LOGS_ES_USER:-}" && -n "${CUBE_LOGS_ES_PASSWORD:-}" ]]; then
      die "CUBE_LOGS_ES_USER and CUBE_LOGS_ES_PASSWORD must be set together"
    fi
  fi
  if [[ -n "${CUBE_LOGS_OTLP_HTTP_ENDPOINT:-}" ]]; then
    require_http_url "CUBE_LOGS_OTLP_HTTP_ENDPOINT" "${CUBE_LOGS_OTLP_HTTP_ENDPOINT}"
    case "${CUBE_LOGS_OTLP_HTTP_ENDPOINT%/}" in
      */v1/logs) die "CUBE_LOGS_OTLP_HTTP_ENDPOINT must be the base endpoint; remove /v1/logs" ;;
    esac
  fi
  local cluster="${CUBE_OBSERVABILITY_CLUSTER_ID:-${CUBE_METRICS_CLUSTER_ID:-cubesandbox}}"
  require_safe_label "CUBE_OBSERVABILITY_CLUSTER_ID" "${cluster}"
  if [[ -n "${CUBE_METRICS_AUTHORIZATION:-}" && -n "${CUBE_METRICS_REMOTE_WRITE_TOKEN:-}" ]]; then
    die "set only one of CUBE_METRICS_AUTHORIZATION and CUBE_METRICS_REMOTE_WRITE_TOKEN"
  fi
  require_single_line "CUBE_METRICS_AUTHORIZATION" "${CUBE_METRICS_AUTHORIZATION:-}"
  require_single_line "CUBE_METRICS_REMOTE_WRITE_TOKEN" "${CUBE_METRICS_REMOTE_WRITE_TOKEN:-}"
  require_single_line "CUBE_METRICS_TENANT_ID" "${CUBE_METRICS_TENANT_ID:-}"
  if [[ -n "${CUBE_LOGS_AUTHORIZATION:-}" && -n "${CUBE_LOGS_TOKEN:-}" ]]; then
    die "set only one of CUBE_LOGS_AUTHORIZATION and CUBE_LOGS_TOKEN"
  fi
  require_single_line "CUBE_LOGS_AUTHORIZATION" "${CUBE_LOGS_AUTHORIZATION:-}"
  require_single_line "CUBE_LOGS_TOKEN" "${CUBE_LOGS_TOKEN:-}"
  require_single_line "CUBE_LOGS_TENANT_ID" "${CUBE_LOGS_TENANT_ID:-}"
  if [[ -n "${es_endpoint}" &&
        ( -n "${CUBE_LOGS_AUTHORIZATION:-}" || -n "${CUBE_LOGS_TOKEN:-}" ) &&
        ( -n "${CUBE_LOGS_ES_USER:-}" || -n "${CUBE_LOGS_ES_API_KEY:-}" ) ]]; then
    die "set either generic CUBE_LOGS_AUTHORIZATION/CUBE_LOGS_TOKEN or Elasticsearch-specific credentials, not both"
  fi
}

metrics_authorization() {
  if [[ -n "${CUBE_METRICS_AUTHORIZATION:-}" ]]; then
    printf '%s' "${CUBE_METRICS_AUTHORIZATION}"
  elif [[ -n "${CUBE_METRICS_REMOTE_WRITE_TOKEN:-}" ]]; then
    printf 'Bearer %s' "${CUBE_METRICS_REMOTE_WRITE_TOKEN}"
  fi
}

logs_authorization() {
  if [[ -n "${CUBE_LOGS_AUTHORIZATION:-}" ]]; then
    printf '%s' "${CUBE_LOGS_AUTHORIZATION}"
  elif [[ -n "${CUBE_LOGS_TOKEN:-}" ]]; then
    printf 'Bearer %s' "${CUBE_LOGS_TOKEN}"
  fi
}

sha256_stdin() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 | awk '{print $1}'
  else
    die "sha256sum or shasum is required"
  fi
}

base64_stdin() {
  base64 | tr -d '\r\n'
}

require_cubelet_host_network() {
  local namespace="$1"
  local rows name host_net
  rows="$(kubectl -n "${namespace}" get daemonset -l app.kubernetes.io/component=cube-node -o jsonpath='{range .items[*]}{.metadata.name}{"="}{.spec.template.spec.hostNetwork}{"\n"}{end}' 2>/dev/null || true)"
  if [[ -z "${rows}" ]]; then
    log "no cube-node DaemonSet in ${namespace}; 127.0.0.1:9998 only works while cubeNode.hostNetwork stays true"
    return
  fi
  while IFS= read -r row || [[ -n "${row}" ]]; do
    [[ -z "${row}" ]] && continue
    name="${row%%=*}"
    host_net="${row#*=}"
    [[ "${host_net}" == "true" ]] || die "${namespace}/${name} hostNetwork=${host_net:-false}; this collector cannot scrape Cubelet on 127.0.0.1:9998"
  done <<< "${rows}"
}

k8s_config() {
  local cluster url authorization tenant es_endpoint es_index logs_otlp_endpoint
  local logs_authorization_value logs_tenant es_user es_password es_api_key es_tls_skip
  local metrics_enabled="false" logs_enabled="false" logs_exporter_name=""
  cluster="${CUBE_OBSERVABILITY_CLUSTER_ID:-${CUBE_METRICS_CLUSTER_ID:-cubesandbox}}"
  url="$(yaml_dquote '${env:CUBE_METRICS_BACKEND_URL}')"
  authorization="$(metrics_authorization)"
  tenant="${CUBE_METRICS_TENANT_ID:-}"
  es_endpoint="${CUBE_LOGS_ES_ENDPOINT:-${CUBE_LOGS_ELASTICSEARCH_ENDPOINT:-}}"
  es_endpoint="${es_endpoint%/}"
  es_index="${CUBE_LOGS_ES_INDEX:-cubesandbox-logs}"
  logs_otlp_endpoint="${CUBE_LOGS_OTLP_HTTP_ENDPOINT:-}"
  logs_otlp_endpoint="${logs_otlp_endpoint%/}"
  logs_authorization_value="$(logs_authorization)"
  logs_tenant="${CUBE_LOGS_TENANT_ID:-}"
  es_user="${CUBE_LOGS_ES_USER:-}"
  es_password="${CUBE_LOGS_ES_PASSWORD:-}"
  es_api_key="${CUBE_LOGS_ES_API_KEY:-}"
  es_tls_skip="${CUBE_LOGS_ES_TLS_INSECURE_SKIP_VERIFY:-false}"

  if [[ -n "${CUBE_METRICS_OTLP_HTTP_ENDPOINT:-}" || -n "${CUBE_METRICS_REMOTE_WRITE_URL:-}" ]]; then
    metrics_enabled="true"
  fi
  if [[ -n "${es_endpoint}" ]]; then
    logs_enabled="true"
    logs_exporter_name="elasticsearch/logs"
  elif [[ -n "${logs_otlp_endpoint}" ]]; then
    logs_enabled="true"
    logs_exporter_name="otlp_http/logs"
  fi

  printf 'receivers:\n'
  if [[ "${metrics_enabled}" == "true" ]]; then
    cat <<EOF
  prometheus:
    trim_metric_suffixes: false
    config:
      scrape_configs:
        - job_name: cubesandbox-resource
          scrape_interval: 5s
          scrape_timeout: 3s
          metrics_path: /v1/metrics/resource
          static_configs:
            - targets: ["127.0.0.1:9998"]
EOF
  fi

  if [[ "${logs_enabled}" == "true" ]]; then
    cat <<EOF
  filelog/sandbox:
    include:
      - /data/cubelet/log/*/stdout
      - /data/cubelet/log/*/stderr
    start_at: beginning
    storage: file_storage
    include_file_path: true
    include_file_name: false
    max_log_size: 1MiB
    operators:
      - type: regex_parser
        parse_from: attributes["log.file.path"]
        regex: '^/data/cubelet/log/(?P<sandbox_id>[^/]+)/(?P<stream>stdout|stderr)$'
      - type: copy
        from: attributes["log.file.path"]
        to: attributes.log_file_path
      - type: copy
        from: attributes.sandbox_id
        to: attributes.cubesandbox_sandbox_id
      - type: move
        from: attributes.sandbox_id
        to: resource["cubesandbox.sandbox.id"]
      - type: copy
        from: attributes.stream
        to: attributes.log_iostream
      - type: move
        from: attributes.stream
        to: attributes["log.iostream"]
EOF
  fi

  cat <<EOF
processors:
  memory_limiter:
    check_interval: 1s
    limit_mib: 256
  batch:
    timeout: 2s
    send_batch_size: 1024
EOF

  if [[ -n "${CUBE_METRICS_OTLP_HTTP_ENDPOINT:-}" ]]; then
    cat <<EOF
  resource/metrics:
    attributes:
      - key: cluster
        value: $(yaml_dquote "${cluster}")
        action: upsert
      - key: node
        value: $(yaml_dquote '${env:NODE_NAME}')
        action: upsert
      - key: cubesandbox.cluster.id
        value: $(yaml_dquote "${cluster}")
        action: upsert
      - key: host.name
        value: $(yaml_dquote '${env:NODE_NAME}')
        action: upsert
EOF
  fi

  if [[ "${logs_enabled}" == "true" ]]; then
    cat <<EOF
  resource/logs_common:
    attributes:
      - key: host.name
        value: $(yaml_dquote '${env:NODE_NAME}')
        action: upsert
      - key: cubesandbox.cluster.id
        value: $(yaml_dquote "${cluster}")
        action: upsert
  attributes/logs_common:
    actions:
      - key: host_name
        value: $(yaml_dquote '${env:NODE_NAME}')
        action: upsert
      - key: cubesandbox_cluster_id
        value: $(yaml_dquote "${cluster}")
        action: upsert
      - key: cubesandbox_sandbox_id
        value: "none"
        action: insert
      - key: log_iostream
        value: "none"
        action: insert
  resource/sandbox:
    attributes:
      - key: service.name
        value: cubesandbox-sandbox
        action: upsert
      - key: cubesandbox.log.source
        value: sandbox-init
        action: upsert
  attributes/sandbox:
    actions:
      - key: service_name
        value: cubesandbox-sandbox
        action: upsert
      - key: cubesandbox_log_source
        value: sandbox-init
        action: upsert
EOF
  fi

  printf 'exporters:\n'
  if [[ -n "${CUBE_METRICS_OTLP_HTTP_ENDPOINT:-}" ]]; then
    cat <<EOF
  otlp_http/metrics:
    endpoint: ${url}
    compression: gzip
EOF
    if [[ -n "${authorization}" || -n "${tenant}" ]]; then
      cat <<EOF
    headers:
EOF
      if [[ -n "${authorization}" ]]; then
        printf '      Authorization: %s\n' "$(yaml_dquote '${env:CUBE_METRICS_AUTHORIZATION}')"
      fi
      if [[ -n "${tenant}" ]]; then
        printf '      X-Scope-OrgID: %s\n' "$(yaml_dquote '${env:CUBE_METRICS_TENANT_ID}')"
      fi
    fi
    cat <<EOF
    sending_queue:
      enabled: true
      storage: file_storage
      queue_size: 1000
      block_on_overflow: true
    retry_on_failure:
      enabled: true
      max_elapsed_time: 0
EOF
  elif [[ -n "${CUBE_METRICS_REMOTE_WRITE_URL:-}" ]]; then
    cat <<EOF
  prometheus_remote_write:
    http:
      endpoint: ${url}
EOF
    if [[ -n "${authorization}" || -n "${tenant}" ]]; then
      cat <<EOF
      headers:
EOF
      if [[ -n "${authorization}" ]]; then
        printf '        Authorization: %s\n' "$(yaml_dquote '${env:CUBE_METRICS_AUTHORIZATION}')"
      fi
      if [[ -n "${tenant}" ]]; then
        printf '        X-Scope-OrgID: %s\n' "$(yaml_dquote '${env:CUBE_METRICS_TENANT_ID}')"
      fi
    fi
    cat <<EOF
    external_labels:
      cluster: $(yaml_dquote "${cluster}")
      node: $(yaml_dquote '${env:NODE_NAME}')
    wal:
      directory: /var/lib/otelcol/prom_rw
    resource_to_telemetry_conversion:
      enabled: false
EOF
  fi

  if [[ -n "${es_endpoint}" ]]; then
    cat <<EOF
  elasticsearch/logs:
    endpoints:
      - $(yaml_dquote '${env:CUBE_LOGS_BACKEND_URL}')
    logs_index: $(yaml_dquote "${es_index}")
    mapping:
      allowed_modes:
        - raw
    discover:
      on_start: false
      interval: 0s
EOF
    if [[ -n "${es_user}" ]]; then
      printf '    user: %s\n' "$(yaml_dquote '${env:CUBE_LOGS_ES_USER}')"
      printf '    password: %s\n' "$(yaml_dquote '${env:CUBE_LOGS_ES_PASSWORD}')"
    fi
    if [[ -n "${es_api_key}" ]]; then
      printf '    api_key: %s\n' "$(yaml_dquote '${env:CUBE_LOGS_ES_API_KEY}')"
    fi
    if [[ -n "${logs_authorization_value}" || -n "${logs_tenant}" ]]; then
      cat <<EOF
    headers:
EOF
      if [[ -n "${logs_authorization_value}" ]]; then
        printf '      Authorization: %s\n' "$(yaml_dquote '${env:CUBE_LOGS_AUTHORIZATION}')"
      fi
      if [[ -n "${logs_tenant}" ]]; then
        printf '      X-Scope-OrgID: %s\n' "$(yaml_dquote '${env:CUBE_LOGS_TENANT_ID}')"
      fi
    fi
    if [[ "${es_tls_skip}" == "true" ]]; then
      cat <<EOF
    tls:
      insecure_skip_verify: true
EOF
    fi
    cat <<EOF
    sending_queue:
      enabled: true
      num_consumers: 10
      queue_size: 1000
      block_on_overflow: true
      batch:
        flush_timeout: 2s
        min_size: 0
        max_size: 5000000
        sizer: bytes
    retry:
      enabled: true
      max_retries: 5
      initial_interval: 100ms
      max_interval: 1m
EOF
  elif [[ -n "${logs_otlp_endpoint}" ]]; then
    cat <<EOF
  otlp_http/logs:
    endpoint: $(yaml_dquote '${env:CUBE_LOGS_BACKEND_URL}')
    compression: gzip
EOF
    if [[ -n "${logs_authorization_value}" || -n "${logs_tenant}" ]]; then
      cat <<EOF
    headers:
EOF
      if [[ -n "${logs_authorization_value}" ]]; then
        printf '      Authorization: %s\n' "$(yaml_dquote '${env:CUBE_LOGS_AUTHORIZATION}')"
      fi
      if [[ -n "${logs_tenant}" ]]; then
        printf '      X-Scope-OrgID: %s\n' "$(yaml_dquote '${env:CUBE_LOGS_TENANT_ID}')"
      fi
    fi
    cat <<EOF
    sending_queue:
      enabled: true
      storage: file_storage
      queue_size: 1000
      block_on_overflow: true
    retry_on_failure:
      enabled: true
      max_elapsed_time: 0
EOF
  fi

  local need_file_storage="false"
  if [[ -n "${CUBE_METRICS_OTLP_HTTP_ENDPOINT:-}" || "${logs_enabled}" == "true" ]]; then
    need_file_storage="true"
  fi

  cat <<EOF
extensions:
  health_check:
    endpoint: 127.0.0.1:13133
EOF
  if [[ "${need_file_storage}" == "true" ]]; then
    cat <<EOF
  file_storage:
    directory: /var/lib/otelcol/storage
    create_directory: true
EOF
  fi

  printf 'service:\n'
  if [[ "${need_file_storage}" == "true" ]]; then
    printf '  extensions: [health_check, file_storage]\n'
  else
    printf '  extensions: [health_check]\n'
  fi
  cat <<EOF
  telemetry:
    metrics:
      readers:
        - pull:
            exporter:
              prometheus:
                host: 127.0.0.1
                port: 8888
  pipelines:
EOF
  if [[ "${metrics_enabled}" == "true" ]]; then
    if [[ -n "${CUBE_METRICS_OTLP_HTTP_ENDPOINT:-}" ]]; then
      cat <<EOF
    metrics:
      receivers: [prometheus]
      processors: [memory_limiter, resource/metrics, batch]
      exporters: [otlp_http/metrics]
EOF
    else
      cat <<EOF
    metrics:
      receivers: [prometheus]
      processors: [memory_limiter, batch]
      exporters: [prometheus_remote_write]
EOF
    fi
  fi

  if [[ "${logs_enabled}" == "true" ]]; then
    cat <<EOF
    logs/sandbox:
      receivers: [filelog/sandbox]
      processors: [memory_limiter, resource/logs_common, resource/sandbox, attributes/logs_common, attributes/sandbox, batch]
      exporters: [${logs_exporter_name}]
EOF
  fi
}

install_collector() {
  command -v kubectl >/dev/null 2>&1 || die "kubectl is required"
  require_k8s_config
  local namespace image state_host_path tmp config_sum secret_sum authorization tenant backend_url logs_backend_url es_endpoint
  local logs_auth logs_tenant es_user es_password es_api_key
  namespace="${CUBE_METRICS_NAMESPACE:-${DEFAULT_NAMESPACE}}"
  image="${CUBE_OTELCOL_IMAGE:-${DEFAULT_IMAGE}}"
  state_host_path="${CUBE_OTEL_K8S_STATE_HOST_PATH:-/var/lib/cube-sandbox/otelcol-k8s}"
  require_safe_absolute_path "CUBE_OTEL_K8S_STATE_HOST_PATH" "${state_host_path}"
  authorization="$(metrics_authorization)"
  tenant="${CUBE_METRICS_TENANT_ID:-}"
  logs_auth="$(logs_authorization)"
  logs_tenant="${CUBE_LOGS_TENANT_ID:-}"
  es_user="${CUBE_LOGS_ES_USER:-}"
  es_password="${CUBE_LOGS_ES_PASSWORD:-}"
  es_api_key="${CUBE_LOGS_ES_API_KEY:-}"
  es_endpoint="${CUBE_LOGS_ES_ENDPOINT:-${CUBE_LOGS_ELASTICSEARCH_ENDPOINT:-}}"
  if [[ -n "${CUBE_METRICS_OTLP_HTTP_ENDPOINT:-}" ]]; then
    backend_url="${CUBE_METRICS_OTLP_HTTP_ENDPOINT%/}"
  else
    backend_url="${CUBE_METRICS_REMOTE_WRITE_URL:-}"
  fi
  if [[ -n "${es_endpoint}" ]]; then
    logs_backend_url="${es_endpoint%/}"
  else
    logs_backend_url="${CUBE_LOGS_OTLP_HTTP_ENDPOINT:-}"
    logs_backend_url="${logs_backend_url%/}"
  fi
  require_safe_label "namespace" "${namespace}"
  require_safe_label "CUBE_OTELCOL_IMAGE" "${image}"
  kubectl get namespace "${namespace}" >/dev/null
  require_cubelet_host_network "${namespace}"
  if [[ "${image}" == "${DEFAULT_IMAGE}" ]]; then
    log "image is Docker Hub ${DEFAULT_IMAGE}; set CUBE_OTELCOL_IMAGE when compute nodes cannot pull it"
  fi
  tmp="$(mktemp)"
  trap "rm -f -- $(printf '%q' "${tmp}")" EXIT
  k8s_config > "${tmp}"
  config_sum="$(sha256_stdin < "${tmp}")"
  secret_sum="$(printf '%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s\n%s' \
    "${backend_url}" "${logs_backend_url}" "${authorization}" "${tenant}" \
    "${logs_auth}" "${logs_tenant}" "${es_user}" "${es_password}" "${es_api_key}" | sha256_stdin)"
  kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: ${K8S_NAME}
  namespace: ${namespace}
type: Opaque
data:
  url: "$(printf '%s' "${backend_url}" | base64_stdin)"
  logs_url: "$(printf '%s' "${logs_backend_url}" | base64_stdin)"
  authorization: "$(printf '%s' "${authorization}" | base64_stdin)"
  tenant_id: "$(printf '%s' "${tenant}" | base64_stdin)"
  logs_authorization: "$(printf '%s' "${logs_auth}" | base64_stdin)"
  logs_tenant_id: "$(printf '%s' "${logs_tenant}" | base64_stdin)"
  es_user: "$(printf '%s' "${es_user}" | base64_stdin)"
  es_password: "$(printf '%s' "${es_password}" | base64_stdin)"
  es_api_key: "$(printf '%s' "${es_api_key}" | base64_stdin)"
EOF
  kubectl -n "${namespace}" create configmap "${K8S_NAME}" \
    --from-file=config.yaml="${tmp}" \
    --dry-run=client -o yaml | kubectl apply -f -

  # Durable node state and Cubelet-owned log files both require host access.
  # The container still has a read-only rootfs, no added capabilities, and no
  # privilege escalation.
  local run_as_non_root="false"
  local run_as_user="0"
  local extra_mounts=""
  local extra_volumes=""
  if [[ -n "${logs_backend_url}" ]]; then
    run_as_non_root="false"
    run_as_user="0"
    extra_mounts="${extra_mounts}
            - name: cubelet-logs
              mountPath: /data/cubelet/log
              readOnly: true"
    extra_volumes="${extra_volumes}
        - name: cubelet-logs
          hostPath:
            path: /data/cubelet/log
            type: DirectoryOrCreate"
  fi

  kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: ${K8S_NAME}
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: ${K8S_NAME}
    app.kubernetes.io/component: metrics
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: ${K8S_NAME}
  template:
    metadata:
      labels:
        app.kubernetes.io/name: ${K8S_NAME}
        app.kubernetes.io/component: metrics
      annotations:
        checksum/config: ${config_sum}
        checksum/secret: ${secret_sum}
    spec:
      hostNetwork: true
      dnsPolicy: ClusterFirstWithHostNet
      nodeSelector:
        cube.tencent.com/cube-node: "true"
      tolerations:
        - key: cube.tencent.com/compute
          operator: Equal
          value: "true"
          effect: NoSchedule
      securityContext:
        runAsNonRoot: ${run_as_non_root}
        runAsUser: ${run_as_user}
        fsGroup: ${run_as_user}
      containers:
        - name: otelcol
          image: ${image}
          imagePullPolicy: IfNotPresent
          args: ["--config=file:/etc/otelcol/config.yaml"]
          env:
            - name: NODE_NAME
              valueFrom:
                fieldRef:
                  fieldPath: spec.nodeName
            - name: CUBE_METRICS_BACKEND_URL
              valueFrom:
                secretKeyRef:
                  name: ${K8S_NAME}
                  key: url
            - name: CUBE_LOGS_BACKEND_URL
              valueFrom:
                secretKeyRef:
                  name: ${K8S_NAME}
                  key: logs_url
            - name: CUBE_METRICS_AUTHORIZATION
              valueFrom:
                secretKeyRef:
                  name: ${K8S_NAME}
                  key: authorization
            - name: CUBE_METRICS_TENANT_ID
              valueFrom:
                secretKeyRef:
                  name: ${K8S_NAME}
                  key: tenant_id
            - name: CUBE_LOGS_AUTHORIZATION
              valueFrom:
                secretKeyRef:
                  name: ${K8S_NAME}
                  key: logs_authorization
            - name: CUBE_LOGS_TENANT_ID
              valueFrom:
                secretKeyRef:
                  name: ${K8S_NAME}
                  key: logs_tenant_id
            - name: CUBE_LOGS_ES_USER
              valueFrom:
                secretKeyRef:
                  name: ${K8S_NAME}
                  key: es_user
            - name: CUBE_LOGS_ES_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: ${K8S_NAME}
                  key: es_password
            - name: CUBE_LOGS_ES_API_KEY
              valueFrom:
                secretKeyRef:
                  name: ${K8S_NAME}
                  key: es_api_key
          readinessProbe:
            httpGet:
              path: /
              port: 13133
              host: 127.0.0.1
            periodSeconds: 10
          resources:
            requests:
              cpu: 50m
              memory: 128Mi
            limits:
              cpu: "1"
              memory: 512Mi
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities:
              drop: ["ALL"]
          volumeMounts:
            - name: config
              mountPath: /etc/otelcol
              readOnly: true
            - name: wal
              mountPath: /var/lib/otelcol
            - name: tmp
              mountPath: /tmp${extra_mounts}
      volumes:
        - name: config
          configMap:
            name: ${K8S_NAME}
        - name: wal
          hostPath:
            path: ${state_host_path}
            type: DirectoryOrCreate
        - name: tmp
          emptyDir:
            sizeLimit: 64Mi${extra_volumes}
EOF
  log "applied ${namespace}/${K8S_NAME} on nodes labeled cube.tencent.com/cube-node=true"
  log "re-running install rolls pods when the Secret or ConfigMap checksum changes"
  rm -f -- "${tmp}"
  trap - EXIT
}

uninstall_collector() {
  command -v kubectl >/dev/null 2>&1 || die "kubectl is required"
  local namespace="${CUBE_METRICS_NAMESPACE:-${DEFAULT_NAMESPACE}}"
  kubectl -n "${namespace}" delete daemonset "${K8S_NAME}" --ignore-not-found
  kubectl -n "${namespace}" delete configmap "${K8S_NAME}" --ignore-not-found
  kubectl -n "${namespace}" delete secret "${K8S_NAME}" --ignore-not-found
  log "removed ${namespace}/${K8S_NAME}"
}

render_config() {
  require_k8s_config
  local output="${CUBE_OTEL_CONFIG_OUTPUT:-${PWD}/cubesandbox-k8s-otelcol.yaml}"
  k8s_config > "${output}.tmp"
  mv "${output}.tmp" "${output}"
  log "wrote ${output}"
}

case "${1:-install}" in
  install) install_collector ;;
  render) render_config ;;
  uninstall) uninstall_collector ;;
  *) die "usage: $0 [install|render|uninstall]" ;;
esac
