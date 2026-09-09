// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//
// template_verify — CubeTemplateCenter (PR #1659) 模板业务端到端验证工具。
//
// 流程: 创建模板 → 轮询构建 → 查询模板 → 下载 ext4 二进制 → 删除模板 →
// 删除已下载二进制。每一步记录时延，原始请求/响应落盘，最后输出统计表和
// stats.json。
//
// 用法:
//   go run main.go -addr http://127.0.0.1:8089 \
//     -image cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-code:latest
//
// 下载二进制需要 artifact 的 download_token（不下发公开 API），工具默认通过
// kubectl exec 查 MySQL 获取（可用 -skip-download 跳过；无 token 时会先验证
// token 门控再尝试）。
package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

// ---------------- 配置 ----------------

type config struct {
	addr         string
	image        string
	size         string
	instanceType string
	networkType  string
	outDir       string
	timeout      time.Duration
	keepBinary   bool
	skipDownload bool

	// kubectl -> MySQL（仅用于取 download_token / artifact_id）
	ns       string
	mysqlPod string
	dbUser   string
	dbPass   string
	dbName   string
}

// ---------------- 原始数据保留 ----------------

type rawStore struct{ dir string }

func (r *rawStore) write(name string, data []byte) {
	if err := os.MkdirAll(r.dir, 0o755); err != nil {
		logf("WARN raw mkdir: %v", err)
		return
	}
	p := filepath.Join(r.dir, name)
	if err := os.WriteFile(p, data, 0o644); err != nil {
		logf("WARN raw write %s: %v", p, err)
	}
}

// ---------------- 日志 ----------------

var logW io.Writer = os.Stdout

func logf(format string, args ...any) {
	fmt.Fprintf(logW, "%s %s\n", time.Now().Format("15:04:05.000"), fmt.Sprintf(format, args...))
}

// ---------------- 时延统计 ----------------

type stepStat struct {
	Step      string `json:"step"`
	StartedAt string `json:"started_at"`
	LatencyMs int64  `json:"latency_ms"`
	OK        bool   `json:"ok"`
	Note      string `json:"note,omitempty"`
}

var stats []stepStat

func record(step string, start time.Time, ok bool, note string) {
	stats = append(stats, stepStat{
		Step:      step,
		StartedAt: start.Format(time.RFC3339),
		LatencyMs: time.Since(start).Milliseconds(),
		OK:        ok,
		Note:      note,
	})
}

// ---------------- HTTP 封装 ----------------

type apiResp struct {
	HTTPCode int
	Body     []byte
	RetCode  int
	RetMsg   string
	Raw      map[string]any
	FinalURL string // 重定向后的最终 URL（S3 302 时为 presigned URL）
}

var httpClient = &http.Client{Timeout: 120 * time.Second}

// doJSON 发请求、保存原始数据、解析通用返回信封。
func doJSON(raw *rawStore, tag, method, url string, reqBody any) (*apiResp, error) {
	var rd io.Reader
	var reqRaw []byte
	if reqBody != nil {
		var err error
		reqRaw, err = json.MarshalIndent(reqBody, "", "  ")
		if err != nil {
			return nil, fmt.Errorf("marshal request: %w", err)
		}
		rd = bytes.NewReader(reqRaw)
		raw.write(tag+"_request.json", reqRaw)
	}
	req, err := http.NewRequest(method, url, rd)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("%s %s: %w", method, url, err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	raw.write(tag+"_response.json", body)

	ar := &apiResp{HTTPCode: resp.StatusCode, Body: body, FinalURL: resp.Request.URL.String()}
	if json.Unmarshal(body, &ar.Raw) == nil {
		ar.RetCode, ar.RetMsg = extractRet(ar.Raw)
	}
	return ar, nil
}

// extractRet 从常见信封结构提取 ret_code/ret_msg（root.ret / root.res.ret / 扁平）。
func extractRet(m map[string]any) (int, string) {
	for _, key := range []string{"ret", "res"} {
		if sub, ok := m[key].(map[string]any); ok {
			if r, ok := sub["ret"].(map[string]any); ok {
				return retFrom(r)
			}
			return retFrom(sub)
		}
	}
	return retFrom(m)
}

func retFrom(m map[string]any) (int, string) {
	code := -1
	if v, ok := m["ret_code"].(float64); ok {
		code = int(v)
	}
	msg, _ := m["ret_msg"].(string)
	return code, msg
}

// ---------------- kubectl MySQL ----------------

func sqlQuery(cfg *config, sql string) (string, error) {
	cmd := exec.CommandContext(context.Background(), "kubectl", "-n", cfg.ns, "exec", cfg.mysqlPod, "--",
		"mysql", "-u"+cfg.dbUser, "-p"+cfg.dbPass, cfg.dbName, "-N", "-e", sql)
	out, err := cmd.Output()
	if err != nil {
		return "", err
	}
	return strings.TrimSpace(string(out)), nil
}

// ---------------- 主流程 ----------------

func main() {
	cfg := &config{}
	flag.StringVar(&cfg.addr, "addr", "http://127.0.0.1:8089", "CubeMaster base URL")
	flag.StringVar(&cfg.image, "image", "cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-code:latest", "source image ref")
	flag.StringVar(&cfg.size, "size", "10Gi", "writable layer size")
	flag.StringVar(&cfg.instanceType, "instance-type", "cubebox", "instance type")
	flag.StringVar(&cfg.networkType, "network-type", "tap", "network type")
	flag.StringVar(&cfg.outDir, "out", "", "raw data output dir (default ./template_verify_<ts>)")
	flag.DurationVar(&cfg.timeout, "timeout", 20*time.Minute, "build wait timeout")
	flag.BoolVar(&cfg.keepBinary, "keep-binary", false, "keep the downloaded ext4 instead of deleting it")
	flag.BoolVar(&cfg.skipDownload, "skip-download", false, "skip the artifact download step")
	flag.StringVar(&cfg.ns, "ns", "cube-system", "k8s namespace (for mysql token query)")
	flag.StringVar(&cfg.mysqlPod, "mysql-pod", "cube-mysql-0", "mysql pod name")
	flag.StringVar(&cfg.dbUser, "db-user", "cube", "mysql user")
	flag.StringVar(&cfg.dbPass, "db-pass", "CubeSandbox123!", "mysql password")
	flag.StringVar(&cfg.dbName, "db-name", "cube_mvp", "mysql database")
	flag.Parse()

	if cfg.outDir == "" {
		cfg.outDir = fmt.Sprintf("template_verify_%s", time.Now().Format("20060102_150405"))
	}
	raw := &rawStore{dir: filepath.Join(cfg.outDir, "raw")}
	if err := os.MkdirAll(cfg.outDir, 0o755); err != nil {
		fmt.Fprintf(os.Stderr, "mkdir %s: %v\n", cfg.outDir, err)
		os.Exit(1)
	}
	lf, err := os.Create(filepath.Join(cfg.outDir, "verify.log"))
	if err != nil {
		fmt.Fprintf(os.Stderr, "create verify.log: %v\n", err)
		os.Exit(1)
	}
	defer lf.Close()
	logW = io.MultiWriter(os.Stdout, lf)

	logf("template verify: addr=%s image=%s size=%s out=%s", cfg.addr, cfg.image, cfg.size, cfg.outDir)

	failed := false
	run := func(name string, fn func() error) {
		start := time.Now()
		logf("--- %s", name)
		err := fn()
		ok := err == nil
		note := ""
		if err != nil {
			note = err.Error()
			failed = true
		}
		record(name, start, ok, note)
		if ok {
			logf("    OK (%s)", time.Since(start).Round(time.Millisecond))
		} else {
			logf("    FAIL: %v (%s)", err, time.Since(start).Round(time.Millisecond))
		}
	}

	var tplID, jobID string

	// 1. 创建模板
	run("01_create_template", func() error {
		body := map[string]any{
			"request_id":          fmt.Sprintf("verify-%d", time.Now().UnixNano()),
			"source_image_ref":    cfg.image,
			"writable_layer_size": cfg.size,
			"instance_type":       cfg.instanceType,
			"network_type":        cfg.networkType,
		}
		r, err := doJSON(raw, "01_create", "POST", cfg.addr+"/cube/template/from-image", body)
		if err != nil {
			return err
		}
		if r.RetCode != 0 {
			return fmt.Errorf("create ret_code=%d ret_msg=%s", r.RetCode, r.RetMsg)
		}
		job, _ := r.Raw["job"].(map[string]any)
		tplID, _ = job["template_id"].(string)
		jobID, _ = job["job_id"].(string)
		if tplID == "" || jobID == "" {
			return fmt.Errorf("create response missing template_id/job_id")
		}
		logf("    template_id=%s job_id=%s", tplID, jobID)
		return nil
	})
	if tplID == "" {
		finish(cfg, true)
	}

	// 2. 轮询构建状态直至 ready/error
	run("02_wait_build", func() error {
		pollLog, _ := os.Create(filepath.Join(cfg.outDir, "raw", "02_poll_log.jsonl"))
		defer pollLog.Close()
		waitStart := time.Now()
		deadline := waitStart.Add(cfg.timeout)
		lastStatus := ""
		for time.Now().Before(deadline) {
			r, err := doJSON(raw, "02_poll_last", "GET", cfg.addr+"/cube/template/build/"+jobID+"/status", nil)
			if err != nil {
				return err
			}
			status, _ := r.Raw["status"].(string)
			progress, _ := r.Raw["progress"].(float64)
			msg, _ := r.Raw["message"].(string)
			line, _ := json.Marshal(map[string]any{
				"elapsed_s": int(time.Since(waitStart).Seconds()),
				"status":    status, "progress": int(progress), "message": msg,
			})
			pollLog.Write(append(line, '\n'))
			if status != lastStatus {
				logf("    status=%s progress=%d msg=%s", status, int(progress), msg)
				lastStatus = status
			}
			switch status {
			case "ready":
				return nil
			case "error":
				return fmt.Errorf("build failed: %s", msg)
			}
			time.Sleep(5 * time.Second)
		}
		return fmt.Errorf("build not ready within %s", cfg.timeout)
	})

	// 3. 查询模板
	run("03_get_template", func() error {
		r, err := doJSON(raw, "03_get", "GET", cfg.addr+"/cube/template?template_id="+tplID, nil)
		if err != nil {
			return err
		}
		if r.RetCode != 0 {
			return fmt.Errorf("get ret_code=%d ret_msg=%s", r.RetCode, r.RetMsg)
		}
		// 再查一次列表接口，确认模板在列表中
		rl, err := doJSON(raw, "03_list", "GET", cfg.addr+"/cube/template", nil)
		if err != nil {
			return err
		}
		if rl.RetCode != 0 {
			return fmt.Errorf("list ret_code=%d ret_msg=%s", rl.RetCode, rl.RetMsg)
		}
		if !bytes.Contains(rl.Body, []byte(tplID)) {
			return fmt.Errorf("template %s not found in list response", tplID)
		}
		logf("    get + list OK")
		return nil
	})

	// 4. 下载二进制（先验证 token 门控，再用 token 下载）
	var binPath string
	var binSHA string
	run("04_download_binary", func() error {
		if cfg.skipDownload {
			logf("    skipped by -skip-download")
			return nil
		}
		// artifact_id + download_token 经 kubectl 查库获取
		row, err := sqlQuery(cfg, fmt.Sprintf(
			"SELECT CONCAT(artifact_id,'|',download_token) FROM t_cube_rootfs_artifact WHERE artifact_id=(SELECT artifact_id FROM t_cube_template_image_job WHERE template_id='%s' AND artifact_id<>'' ORDER BY id DESC LIMIT 1) ORDER BY id DESC LIMIT 1", tplID))
		if err != nil || row == "" {
			return fmt.Errorf("query artifact row (kubectl/mysql): %v row=%q", err, row)
		}
		parts := strings.SplitN(row, "|", 2)
		artifactID, token := parts[0], ""
		if len(parts) == 2 {
			token = parts[1]
		}
		logf("    artifact_id=%s", artifactID)

		// 4a. 无 token → 必须被门控拒绝
		gate, err := doJSON(raw, "04_gate_no_token", "GET",
			fmt.Sprintf("%s/cube/template/artifact/download?artifact_id=%s", cfg.addr, artifactID), nil)
		if err != nil {
			return err
		}
		if gate.HTTPCode == 200 && gate.RetCode == 0 {
			return fmt.Errorf("download without token unexpectedly succeeded (token gate broken)")
		}
		logf("    token gate OK (http=%d ret=%d)", gate.HTTPCode, gate.RetCode)

		// 4b. 带 token 下载（S3 模式会 302 到重签的 presigned URL）
		binPath = filepath.Join(cfg.outDir, "artifacts", artifactID+".ext4")
		if err := os.MkdirAll(filepath.Dir(binPath), 0o755); err != nil {
			return err
		}
		dlURL := fmt.Sprintf("%s/cube/template/artifact/download?artifact_id=%s&token=%s", cfg.addr, artifactID, token)
		dlStart := time.Now()
		resp, err := httpClient.Get(dlURL)
		if err != nil {
			return fmt.Errorf("download: %w", err)
		}
		defer resp.Body.Close()
		if resp.StatusCode != http.StatusOK {
			b, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
			raw.write("04_download_error_response.txt", b)
			return fmt.Errorf("download http=%d body=%s", resp.StatusCode, string(b))
		}
		h := sha256.New()
		f, err := os.Create(binPath)
		if err != nil {
			return err
		}
		n, err := io.Copy(io.MultiWriter(f, h), resp.Body)
		f.Close()
		if err != nil {
			return fmt.Errorf("save binary: %w", err)
		}
		binSHA = hex.EncodeToString(h.Sum(nil))
		finalURL := resp.Request.URL.String()
		raw.write("04_download_meta.json", []byte(fmt.Sprintf(
			"{\n  \"artifact_id\": %q,\n  \"bytes\": %d,\n  \"sha256\": %q,\n  \"final_url\": %q,\n  \"elapsed_ms\": %d\n}",
			artifactID, n, binSHA, finalURL, time.Since(dlStart).Milliseconds())))
		mbs := float64(n) / 1e6 / time.Since(dlStart).Seconds()
		logf("    downloaded %d bytes (%.1f MB/s) sha256=%s", n, mbs, binSHA[:16])
		logf("    final_url=%s", finalURL)

		// 4c. 与库里记录的 sha256 比对
		wantSHA, _ := sqlQuery(cfg, fmt.Sprintf(
			"SELECT ext4_sha256 FROM t_cube_rootfs_artifact WHERE artifact_id='%s'", artifactID))
		wantSHA = strings.TrimPrefix(strings.TrimSpace(wantSHA), "sha256:")
		if wantSHA != "" && !strings.EqualFold(wantSHA, binSHA) {
			return fmt.Errorf("sha256 mismatch: db=%s downloaded=%s", wantSHA, binSHA)
		}
		logf("    sha256 matches DB record")
		return nil
	})

	// 5. 删除模板
	run("05_delete_template", func() error {
		body := map[string]any{
			"request_id":    fmt.Sprintf("verify-del-%d", time.Now().UnixNano()),
			"template_id":   tplID,
			"instance_type": cfg.instanceType,
		}
		r, err := doJSON(raw, "05_delete", "DELETE", cfg.addr+"/cube/template", body)
		if err != nil {
			return err
		}
		if r.RetCode != 0 {
			return fmt.Errorf("delete ret_code=%d ret_msg=%s", r.RetCode, r.RetMsg)
		}
		// 删除后查询应 404
		rg, err := doJSON(raw, "05_get_after_delete", "GET", cfg.addr+"/cube/template?template_id="+tplID, nil)
		if err != nil {
			return err
		}
		if rg.RetCode == 0 {
			return fmt.Errorf("template still queryable after delete")
		}
		logf("    deleted; subsequent get returns ret_code=%d (expected non-zero)", rg.RetCode)
		return nil
	})

	// 6. 删除下载的二进制
	run("06_remove_binary", func() error {
		if binPath == "" {
			logf("    no downloaded binary (skipped earlier)")
			return nil
		}
		if cfg.keepBinary {
			logf("    keeping %s (-keep-binary)", binPath)
			return nil
		}
		if err := os.Remove(binPath); err != nil {
			return fmt.Errorf("remove %s: %w", binPath, err)
		}
		logf("    removed %s", binPath)
		return nil
	})

	finish(cfg, failed)
}

// finish 输出统计并退出。
func finish(cfg *config, failed bool) {
	// stats.json
	stm := map[string]any{
		"generated_at": time.Now().Format(time.RFC3339),
		"config": map[string]any{
			"addr": cfg.addr, "image": cfg.image, "size": cfg.size,
			"instance_type": cfg.instanceType, "network_type": cfg.networkType,
		},
		"steps": stats,
		"ok":    !failed,
	}
	b, _ := json.MarshalIndent(stm, "", "  ")
	_ = os.WriteFile(filepath.Join(cfg.outDir, "stats.json"), b, 0o644)

	// 汇总表
	fmt.Fprintf(logW, "\n===== latency summary =====\n")
	fmt.Fprintf(logW, "%-24s %12s %s\n", "step", "latency", "result")
	var total int64
	for _, s := range stats {
		mark := "OK"
		if !s.OK {
			mark = "FAIL(" + s.Note + ")"
		}
		fmt.Fprintf(logW, "%-24s %10d ms %s\n", s.Step, s.LatencyMs, mark)
		total += s.LatencyMs
	}
	fmt.Fprintf(logW, "%-24s %10d ms\n", "TOTAL", total)
	logf("raw data + log + stats: %s", cfg.outDir)
	if failed {
		os.Exit(1)
	}
}
