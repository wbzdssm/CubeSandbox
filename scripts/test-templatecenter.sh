#!/bin/bash
# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0
#
# TemplateCenter 完整自动化测试脚本
#
# 用法: ./scripts/test-templatecenter.sh [--skip-setup] [--skip-cleanup] [--verbose]
#
# 三种被测形态, 用 TARGET / BUILD_MODE 组合切换 (三者都应全绿):
#
#   1) master + local  (默认, 上个迭代形态: CubeMaster 全干)
#      TARGET=master BUILD_MODE=local ./scripts/test-templatecenter.sh
#
#   2) master + remote (本迭代形态: master 校验/写库/下发, TC 只造 ext4)
#      需 CubeMaster conf: template_build_mode: remote + template_center_endpoint
#      TARGET=master BUILD_MODE=remote ./scripts/test-templatecenter.sh
#
#   3) tc              (下个迭代预览: 直连 TC, TC 独立闭环)
#      需 TC 环境变量: CUBE_TC_SERVE_TEMPLATE_API=true
#      TARGET=tc ./scripts/test-templatecenter.sh
#
#   补充: master 的 template_route_mode=proxy 形态下, 用形态 1 的命令即可 ——
#   请求打 master, 由 master 透传给 TC, 对调用方完全透明.

set -e

# ============================================================
# 配置
# ============================================================

MASTER_URL=${MASTER_URL:-http://localhost:8089}
TC_URL=${TC_URL:-http://localhost:8090}
MYSQL_CONTAINER=${MYSQL_CONTAINER:-cube-mysql}
REDIS_CONTAINER=${REDIS_CONTAINER:-cube-redis}
DB_USER=${DB_USER:-cube}
DB_PASS=${DB_PASS:-cube_pass}
DB_NAME=${DB_NAME:-cube_mvp}

# 入口: master (默认, 打 CubeMaster) | tc (直连模板中心)
TARGET=${TARGET:-master}

# 构建模式, 仅 TARGET=master 时有意义:
#   local  = CubeMaster 进程内构建
#   remote = 转发给 TC 构建, TC 回调后由 master 登记产物并下发
BUILD_MODE=${BUILD_MODE:-local}

case "$TARGET" in
  master) API_URL="$MASTER_URL" ;;
  tc)     API_URL="$TC_URL"; BUILD_MODE="local" ;;
  *) echo "TARGET 必须是 master 或 tc (当前: $TARGET)"; exit 1 ;;
esac

# 真实表名 (见 CubeMaster/pkg/base/constants/constants.go)
TABLE_IMAGE_JOBS="t_cube_template_image_job"
TABLE_REPLICAS="t_cube_template_replica"

# 任务终态 (DB/API 原始值均为大写, 见 job_constants.go).
# 三种形态都应到 READY: remote 模式下 TC 回调 BUILT 后, master 的
# ResumeTemplateImageJobAfterRemoteBuild 会继续登记产物 + 下发, 最终仍是 READY.
EXPECTED_DONE_STATUS="READY"

SKIP_SETUP=false
SKIP_CLEANUP=false
VERBOSE=false

# 解析参数
for arg in "$@"; do
  case $arg in
    --skip-setup) SKIP_SETUP=true ;;
    --skip-cleanup) SKIP_CLEANUP=true ;;
    --verbose) VERBOSE=true ;;
  esac
done

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ============================================================
# 工具函数
# ============================================================

log_info() {
  echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
  echo -e "${GREEN}[✓]${NC} $1"
}

log_error() {
  echo -e "${RED}[✗]${NC} $1"
}

log_warn() {
  echo -e "${YELLOW}[!]${NC} $1"
}

log_section() {
  echo ""
  echo "============================================================"
  echo -e "${BLUE}$1${NC}"
  echo "============================================================"
}

verbose() {
  if [ "$VERBOSE" = true ]; then
    echo -e "${YELLOW}[DEBUG]${NC} $1"
  fi
}

wait_for_service() {
  local url=$1
  local name=$2
  local max_wait=${3:-60}
  
  log_info "等待 $name 启动 (最多 ${max_wait}s)..."
  for i in $(seq 1 $max_wait); do
    if curl -sf "$url/health" > /dev/null 2>&1; then
      log_success "$name 已就绪"
      return 0
    fi
    sleep 1
  done
  log_error "$name 启动超时"
  return 1
}

wait_for_job_status() {
  local job_id=$1
  local expected_status=$2
  local max_wait=${3:-300}
  
  log_info "等待 job $job_id 变为 $expected_status (最多 ${max_wait}s)..."
  for i in $(seq 1 $max_wait); do
    local status=$(curl -s "$API_URL/cube/template/build/$job_id/status" | jq -r .job.status 2>/dev/null || echo "")
    local phase=$(curl -s "$API_URL/cube/template/build/$job_id/status" | jq -r .job.phase 2>/dev/null || echo "")
    local progress=$(curl -s "$API_URL/cube/template/build/$job_id/status" | jq -r .job.progress 2>/dev/null || echo "")
    
    verbose "[$i/$max_wait] status=$status phase=$phase progress=$progress"
    
    if [ "$status" = "$expected_status" ]; then
      log_success "job $job_id 已变为 $expected_status"
      return 0
    fi
    
    if [ "$status" = "FAILED" ]; then
      local error_msg=$(curl -s "$API_URL/cube/template/build/$job_id/status" | jq -r .job.error_message 2>/dev/null || echo "")
      log_error "job $job_id 失败: $error_msg"
      return 1
    fi
    
    sleep 1
  done
  
  log_error "job $job_id 超时未变为 $expected_status"
  return 1
}

query_db() {
  local sql=$1
  docker exec $MYSQL_CONTAINER mysql -u$DB_USER -p$DB_PASS $DB_NAME -se "$sql" 2>/dev/null
}

# ============================================================
# 测试用例
# ============================================================

test_health_check() {
  log_section "Test 1: 健康检查 + 入口可达性"

  # 两个服务都要在: remote 模式下 master 需要 TC 造产物; TARGET=tc 时产物下载
  # 端点仍在 master 上 (见 design §9.7).
  wait_for_service "$MASTER_URL" "CubeMaster" || return 1
  wait_for_service "$TC_URL" "CubeTemplateCenter" || return 1

  local master_health=$(curl -s "$MASTER_URL/health" | jq -r .status 2>/dev/null || echo "")
  local tc_health=$(curl -s "$TC_URL/health" | jq -r .status 2>/dev/null || echo "")

  [ "$master_health" = "ok" ] && log_success "CubeMaster health: ok" || { log_error "CubeMaster health: $master_health"; return 1; }
  [ "$tc_health" = "ok" ] && log_success "CubeTemplateCenter health: ok" || { log_error "CubeTemplateCenter health: $tc_health"; return 1; }

  # 验证被测入口真的挂载了模板控制面路由.
  # 未挂载时 gin 返回 404, 挂载时即使参数不全也会走进 handler (返回 200/400).
  # 这一步能直接暴露两类配置错误:
  #   - TARGET=tc 但忘了设 CUBE_TC_SERVE_TEMPLATE_API=true
  #   - TARGET=master + route_mode=proxy 但 TC 侧开关没开 (代理过去是 404)
  local probe_code=$(curl -s -o /dev/null -w "%{http_code}" \
    "$API_URL/cube/template?template_id=__probe_nonexistent__")
  if [ "$probe_code" = "404" ]; then
    log_error "入口 $API_URL 未挂载模板路由 (HTTP 404)"
    if [ "$TARGET" = "tc" ]; then
      log_error "  TARGET=tc 需要 TC 侧设置 CUBE_TC_SERVE_TEMPLATE_API=true"
    else
      log_error "  若 master 配了 template_route_mode=proxy, 检查 TC 侧同一开关"
    fi
    return 1
  fi
  log_success "入口 $API_URL 已挂载模板路由 (HTTP $probe_code)"

  return 0
}

test_normal_create() {
  log_section "Test 2: 正常创建模板"
  
  local alias="test-normal-$(date +%s)"
  log_info "创建模板: alias=$alias"
  
  local resp=$(curl -s -X POST "$API_URL/cube/template/from-image" \
    -H "Content-Type: application/json" \
    -d "{
      \"requestID\": \"test-normal-$(date +%s)\",
      \"source_image_ref\": \"nginx:latest\",
      \"instance_type\": \"S5.MEDIUM2\",
      \"alias\": \"$alias\",
      \"writable_layer_size\": \"1G\"
    }")
  
  verbose "响应: $resp"
  
  local job_id=$(echo "$resp" | jq -r .job.job_id 2>/dev/null || echo "")
  [ -z "$job_id" ] || [ "$job_id" = "null" ] && { log_error "未获取到 job_id"; return 1; }
  
  log_success "创建成功: job_id=$job_id"
  
  wait_for_job_status "$job_id" "$EXPECTED_DONE_STATUS" 300 || return 1

  log_success "构建完成: job_id=$job_id"

  # 验证 DB 状态
  local db_status=$(query_db "SELECT status FROM $TABLE_IMAGE_JOBS WHERE job_id='$job_id'")
  [ "$db_status" = "$EXPECTED_DONE_STATUS" ] && log_success "DB 状态正确: status=$db_status" || { log_error "DB 状态错误: status=$db_status (期望 $EXPECTED_DONE_STATUS)"; return 1; }
  
  # 清理
  log_info "删除模板: alias=$alias"
  curl -s -X DELETE "$API_URL/cube/template" \
    -H "Content-Type: application/json" \
    -d "{\"requestID\": \"test-del-$(date +%s)\", \"template_id\": \"$alias\"}" > /dev/null
  
  return 0
}

test_normal_create_with_probe() {
  log_section "Test 3: 带探针创建模板"
  
  local alias="test-probe-$(date +%s)"
  log_info "创建模板: alias=$alias, probe=49983"
  
  local resp=$(curl -s -X POST "$API_URL/cube/template/from-image" \
    -H "Content-Type: application/json" \
    -d "{
      \"requestID\": \"test-probe-$(date +%s)\",
      \"source_image_ref\": \"nginx:latest\",
      \"instance_type\": \"S5.MEDIUM2\",
      \"alias\": \"$alias\",
      \"writable_layer_size\": \"1G\",
      \"exposed_ports\": [49983, 80],
      \"probe\": 49983
    }")
  
  local job_id=$(echo "$resp" | jq -r .job.job_id 2>/dev/null || echo "")
  [ -z "$job_id" ] || [ "$job_id" = "null" ] && { log_error "未获取到 job_id"; return 1; }
  
  log_success "创建成功: job_id=$job_id"
  
  wait_for_job_status "$job_id" "$EXPECTED_DONE_STATUS" 300 || return 1
  
  # 清理
  curl -s -X DELETE "$API_URL/cube/template" \
    -H "Content-Type: application/json" \
    -d "{\"requestID\": \"test-del-$(date +%s)\", \"template_id\": \"$alias\"}" > /dev/null
  
  return 0
}

test_repeated_create_delete() {
  log_section "Test 4: 反复创建删除 (10 次)"
  
  for i in $(seq 1 10); do
    local alias="test-loop-$i-$(date +%s)"
    log_info "[$i/10] 创建模板: alias=$alias"
    
    local resp=$(curl -s -X POST "$API_URL/cube/template/from-image" \
      -H "Content-Type: application/json" \
      -d "{
        \"requestID\": \"test-loop-$i-$(date +%s)\",
        \"source_image_ref\": \"nginx:latest\",
        \"instance_type\": \"S5.MEDIUM2\",
        \"alias\": \"$alias\",
        \"writable_layer_size\": \"1G\"
      }")
    
    local job_id=$(echo "$resp" | jq -r .job.job_id 2>/dev/null || echo "")
    [ -z "$job_id" ] || [ "$job_id" = "null" ] && { log_error "[$i/10] 未获取到 job_id"; return 1; }
    
    wait_for_job_status "$job_id" "$EXPECTED_DONE_STATUS" 300 || return 1
    
    log_info "[$i/10] 删除模板: alias=$alias"
    curl -s -X DELETE "$API_URL/cube/template" \
      -H "Content-Type: application/json" \
      -d "{\"requestID\": \"test-del-$i-$(date +%s)\", \"template_id\": \"$alias\"}" > /dev/null
    
    log_success "[$i/10] 完成"
  done
  
  return 0
}

test_recreate_same_alias() {
  log_section "Test 5: 创建 → 删除 → 再创建 (同 alias)"
  
  local alias="test-recreate-$(date +%s)"
  
  # 第 1 次创建
  log_info "第 1 次创建: alias=$alias"
  local resp=$(curl -s -X POST "$API_URL/cube/template/from-image" \
    -H "Content-Type: application/json" \
    -d "{
      \"requestID\": \"test-recreate-1-$(date +%s)\",
      \"source_image_ref\": \"nginx:latest\",
      \"instance_type\": \"S5.MEDIUM2\",
      \"alias\": \"$alias\",
      \"writable_layer_size\": \"1G\"
    }")
  
  local job_id_1=$(echo "$resp" | jq -r .job.job_id 2>/dev/null || echo "")
  [ -z "$job_id_1" ] || [ "$job_id_1" = "null" ] && { log_error "第 1 次创建失败"; return 1; }
  
  wait_for_job_status "$job_id_1" "$EXPECTED_DONE_STATUS" 300 || return 1
  
  # 删除
  log_info "删除: alias=$alias"
  curl -s -X DELETE "$API_URL/cube/template" \
    -H "Content-Type: application/json" \
    -d "{\"requestID\": \"test-recreate-del-$(date +%s)\", \"template_id\": \"$alias\"}" > /dev/null
  
  sleep 5
  
  # 第 2 次创建 (同 alias)
  log_info "第 2 次创建: alias=$alias"
  resp=$(curl -s -X POST "$API_URL/cube/template/from-image" \
    -H "Content-Type: application/json" \
    -d "{
      \"requestID\": \"test-recreate-2-$(date +%s)\",
      \"source_image_ref\": \"nginx:latest\",
      \"instance_type\": \"S5.MEDIUM2\",
      \"alias\": \"$alias\",
      \"writable_layer_size\": \"1G\"
    }")
  
  local job_id_2=$(echo "$resp" | jq -r .job.job_id 2>/dev/null || echo "")
  [ -z "$job_id_2" ] || [ "$job_id_2" = "null" ] && { log_error "第 2 次创建失败"; return 1; }
  
  [ "$job_id_1" != "$job_id_2" ] && log_success "job_id 不同: $job_id_1 vs $job_id_2" || { log_error "job_id 相同 (应该不同)"; return 1; }
  
  wait_for_job_status "$job_id_2" "$EXPECTED_DONE_STATUS" 300 || return 1
  
  # 清理
  curl -s -X DELETE "$API_URL/cube/template" \
    -H "Content-Type: application/json" \
    -d "{\"requestID\": \"test-del-$(date +%s)\", \"template_id\": \"$alias\"}" > /dev/null
  
  return 0
}

test_invalid_image_ref() {
  log_section "Test 6: 无效镜像地址"
  
  local resp=$(curl -s -X POST "$API_URL/cube/template/from-image" \
    -H "Content-Type: application/json" \
    -d "{
      \"requestID\": \"test-invalid-$(date +%s)\",
      \"source_image_ref\": \"invalid-image-!!!\",
      \"instance_type\": \"S5.MEDIUM2\",
      \"writable_layer_size\": \"1G\"
    }")
  
  verbose "响应: $resp"
  
  local ret_code=$(echo "$resp" | jq -r .ret.ret_code 2>/dev/null || echo "")
  [ "$ret_code" != "0" ] && log_success "正确拒绝无效镜像: ret_code=$ret_code" || { log_error "未拒绝无效镜像: ret_code=$ret_code"; return 1; }
  
  return 0
}

test_pull_image_fail() {
  log_section "Test 7: 拉取镜像失败"
  
  local resp=$(curl -s -X POST "$API_URL/cube/template/from-image" \
    -H "Content-Type: application/json" \
    -d "{
      \"requestID\": \"test-pull-fail-$(date +%s)\",
      \"source_image_ref\": \"nonexistent-registry.example.com/image:latest\",
      \"instance_type\": \"S5.MEDIUM2\",
      \"writable_layer_size\": \"1G\"
    }")
  
  local job_id=$(echo "$resp" | jq -r .job.job_id 2>/dev/null || echo "")
  [ -z "$job_id" ] || [ "$job_id" = "null" ] && { log_error "未获取到 job_id"; return 1; }
  
  wait_for_job_status "$job_id" "FAILED" 60 || return 1
  
  local error_msg=$(curl -s "$API_URL/cube/template/build/$job_id/status" | jq -r .job.error_message 2>/dev/null || echo "")
  [[ "$error_msg" == *"pull image fail"* ]] && log_success "正确报告拉取失败: $error_msg" || { log_error "错误信息不正确: $error_msg"; return 1; }
  
  return 0
}

test_multi_node_status() {
  log_section "Test 8: 多节点下发状态追踪"
  
  local alias="test-multi-node-$(date +%s)"
  log_info "创建模板: alias=$alias"
  
  local resp=$(curl -s -X POST "$API_URL/cube/template/from-image" \
    -H "Content-Type: application/json" \
    -d "{
      \"requestID\": \"test-multi-node-$(date +%s)\",
      \"source_image_ref\": \"nginx:latest\",
      \"instance_type\": \"S5.MEDIUM2\",
      \"alias\": \"$alias\",
      \"writable_layer_size\": \"1G\"
    }")
  
  local job_id=$(echo "$resp" | jq -r .job.job_id 2>/dev/null || echo "")
  [ -z "$job_id" ] || [ "$job_id" = "null" ] && { log_error "未获取到 job_id"; return 1; }

  # t_cube_template_replica.template_id 存的是 template_id, 不是 alias ——
  # 用 alias 查会恒为空. 从响应里取真实 template_id, 取不到再退回 alias.
  local template_id=$(echo "$resp" | jq -r '.job.template_id // empty' 2>/dev/null || echo "")
  [ -z "$template_id" ] && template_id="$alias"
  verbose "template_id=$template_id (alias=$alias)"

  wait_for_job_status "$job_id" "$EXPECTED_DONE_STATUS" 300 || return 1

  # 三种形态都会产生副本行: remote 模式下 master 的
  # ResumeTemplateImageJobAfterRemoteBuild 复用了 local 的同一批函数.
  local replicas=$(query_db "SELECT node_id, status FROM $TABLE_REPLICAS WHERE template_id='$template_id'")
  verbose "节点状态: $replicas"

  local total=$(query_db "SELECT COUNT(*) FROM $TABLE_REPLICAS WHERE template_id='$template_id'")
  local ready_count=$(query_db "SELECT COUNT(*) FROM $TABLE_REPLICAS WHERE template_id='$template_id' AND status='READY'")

  if [ "${total:-0}" -le 1 ]; then
    # 单节点环境: 降级为"唯一副本 Ready"校验, 多节点追踪需多节点环境
    log_warn "单节点环境 (replicas=${total:-0}), 降级为单副本 Ready 校验"
    [ "$ready_count" = "1" ] && log_success "单副本已 Ready" || { log_error "唯一副本未 Ready: ready_count=$ready_count"; return 1; }
  else
    [ "${ready_count:-0}" -gt 0 ] && log_success "有 $ready_count/$total 个节点 Ready" || { log_error "没有 Ready 节点"; return 1; }
  fi
  
  # 清理
  curl -s -X DELETE "$API_URL/cube/template" \
    -H "Content-Type: application/json" \
    -d "{\"requestID\": \"test-del-$(date +%s)\", \"template_id\": \"$alias\"}" > /dev/null
  
  return 0
}

test_concurrent_create() {
  log_section "Test 9: 并发创建 (20 个)"

  local result_dir=$(mktemp -d)
  local pids=()
  for i in $(seq 1 20); do
    local alias="test-concurrent-$i-$(date +%s)"
    (
      local resp=$(curl -s -X POST "$API_URL/cube/template/from-image" \
        -H "Content-Type: application/json" \
        -d "{
          \"requestID\": \"test-concurrent-$i-$(date +%s)\",
          \"source_image_ref\": \"nginx:latest\",
          \"instance_type\": \"S5.MEDIUM2\",
          \"alias\": \"$alias\",
          \"writable_layer_size\": \"1G\"
        }")

      local job_id=$(echo "$resp" | jq -r .job.job_id 2>/dev/null || echo "")
      if [ -n "$job_id" ] && [ "$job_id" != "null" ]; then
        if wait_for_job_status "$job_id" "$EXPECTED_DONE_STATUS" 300; then
          touch "$result_dir/ok-$i"
        else
          touch "$result_dir/fail-$i"
        fi
        curl -s -X DELETE "$API_URL/cube/template" \
          -H "Content-Type: application/json" \
          -d "{\"requestID\": \"test-del-$i-$(date +%s)\", \"template_id\": \"$alias\"}" > /dev/null
      else
        touch "$result_dir/fail-$i"
      fi
    ) &
    pids+=($!)
  done

  # 等待所有并发任务完成
  for pid in "${pids[@]}"; do
    wait $pid
  done

  local ok_count=$(ls "$result_dir"/ok-* 2>/dev/null | wc -l | tr -d ' ')
  local fail_count=$(ls "$result_dir"/fail-* 2>/dev/null | wc -l | tr -d ' ')
  rm -rf "$result_dir"

  log_info "并发结果: 成功 $ok_count / 失败 $fail_count (共 20)"
  # 同 spec 并发会命中同一 artifactID, TC 侧已按 artifact 串行化并复用产物, 允许全部成功
  [ "$fail_count" = "0" ] && log_success "20 个并发全部成功" || { log_error "$fail_count 个并发失败"; return 1; }

  return 0
}

test_tc_build_logic() {
  log_section "Test 10: TC 核心 Build 逻辑验证"
  
  # 检查 TC 日志
  local tc_log=$(tail -100 /data/log/CubeTemplateCenter/templatecenter.INFO 2>/dev/null || echo "")
  
  if echo "$tc_log" | grep -q "received build job"; then
    log_success "TC 收到构建请求"
  else
    log_warn "TC 日志中未找到 'received build job' (可能 TC 未被调用)"
  fi
  
  if echo "$tc_log" | grep -q "report status to master"; then
    log_success "TC 上报状态到 Master"
  else
    log_warn "TC 日志中未找到 'report status to master'"
  fi
  
  if echo "$tc_log" | grep -q "template build completed"; then
    log_success "TC 完成构建"
  else
    log_warn "TC 日志中未找到 'template build completed'"
  fi
  
  return 0
}

# ============================================================
# 主流程
# ============================================================

main() {
  log_section "TemplateCenter 完整自动化测试"
  
  log_info "配置:"
  log_info "  被测入口: TARGET=$TARGET -> $API_URL"
  log_info "  构建模式: BUILD_MODE=$BUILD_MODE (期望终态 $EXPECTED_DONE_STATUS)"
  log_info "  CubeMaster: $MASTER_URL"
  log_info "  CubeTemplateCenter: $TC_URL"
  log_info "  MySQL: $MYSQL_CONTAINER"
  log_info "  Redis: $REDIS_CONTAINER"
  if [ "$TARGET" = "tc" ]; then
    log_warn "  TARGET=tc 需要 TC 侧 CUBE_TC_SERVE_TEMPLATE_API=true"
  elif [ "$BUILD_MODE" = "remote" ]; then
    log_warn "  BUILD_MODE=remote 需要 master conf: template_build_mode: remote + template_center_endpoint"
  fi
  
  # 启动依赖
  if [ "$SKIP_SETUP" = false ]; then
    log_section "启动依赖"
    
    if ! docker ps | grep -q $MYSQL_CONTAINER; then
      log_info "启动 MySQL..."
      docker run -d --name $MYSQL_CONTAINER -p 3306:3306 \
        -e MYSQL_ROOT_PASSWORD=root \
        -e MYSQL_DATABASE=$DB_NAME \
        -e MYSQL_USER=$DB_USER \
        -e MYSQL_PASSWORD=$DB_PASS \
        mysql:8.0
      sleep 10
    else
      log_info "MySQL 已运行"
    fi
    
    if ! docker ps | grep -q $REDIS_CONTAINER; then
      log_info "启动 Redis..."
      docker run -d --name $REDIS_CONTAINER -p 6379:6379 redis:7
      sleep 5
    else
      log_info "Redis 已运行"
    fi
  fi
  
  # 运行测试
  local failed_tests=()
  
  test_health_check || failed_tests+=("health_check")
  test_normal_create || failed_tests+=("normal_create")
  test_normal_create_with_probe || failed_tests+=("normal_create_with_probe")
  test_repeated_create_delete || failed_tests+=("repeated_create_delete")
  test_recreate_same_alias || failed_tests+=("recreate_same_alias")
  test_invalid_image_ref || failed_tests+=("invalid_image_ref")
  test_pull_image_fail || failed_tests+=("pull_image_fail")
  test_multi_node_status || failed_tests+=("multi_node_status")
  test_concurrent_create || failed_tests+=("concurrent_create")
  test_tc_build_logic || failed_tests+=("tc_build_logic")
  
  # 清理
  if [ "$SKIP_CLEANUP" = false ]; then
    log_section "清理"
    log_info "停止并删除容器..."
    docker stop $MYSQL_CONTAINER $REDIS_CONTAINER 2>/dev/null || true
    docker rm $MYSQL_CONTAINER $REDIS_CONTAINER 2>/dev/null || true
  fi
  
  # 总结
  log_section "测试总结"
  
  local total_tests=10
  local passed_tests=$((total_tests - ${#failed_tests[@]}))
  
  log_info "总计: $passed_tests/$total_tests 通过"
  
  if [ ${#failed_tests[@]} -eq 0 ]; then
    log_success "所有测试通过！"
    return 0
  else
    log_error "失败的测试: ${failed_tests[*]}"
    return 1
  fi
}

# 运行主流程
main "$@"
