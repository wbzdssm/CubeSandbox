// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

// template_stress is a load/chaos tool for the CubeMaster template API.
// Each worker runs a continuous lifecycle loop:
//
//	create (from-image) -> poll until READY/FAILED -> get info -> get info
//	with create_request -> list -> download artifact -> delete -> confirm
//	gone (expect 130404)
//
// Every operation's latency and outcome is appended to a JSONL log file, and
// a live table (per-op + overall count/errors/latency percentiles) is printed
// periodically and at the end.
//
// Usage:
//
//	go run ./scripts/template_stress/main.go \
//	  -addr http://127.0.0.1:8089 \
//	  -image cube-sandbox-cn.tencentcloudcr.com/cube-sandbox/sandbox-code:latest \
//	  -c 4 -d 10m
//
// Stdout is the live summary; the JSONL log has one record per operation.
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
	"os/signal"
	"sort"
	"strings"
	"sync"
	"syscall"
	"time"
)

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

type config struct {
	addr         string
	image        string
	instanceType string
	concurrency  int
	iterations   int
	duration     time.Duration
	pollInterval time.Duration
	buildTimeout time.Duration
	httpTimeout  time.Duration
	logPath      string
	keep         bool
	verifySHA    bool
	reportEvery  time.Duration
}

func parseFlags() config {
	var c config
	flag.StringVar(&c.addr, "addr", "http://127.0.0.1:8089", "CubeMaster base URL")
	flag.StringVar(&c.image, "image", "", "source image ref for from-image builds (required)")
	flag.StringVar(&c.instanceType, "instance-type", "cubebox", "instance type")
	flag.IntVar(&c.concurrency, "c", 4, "concurrent workers")
	flag.IntVar(&c.iterations, "n", 0, "lifecycle iterations per worker (0 = until -d elapses)")
	flag.DurationVar(&c.duration, "d", 10*time.Minute, "total run duration")
	flag.DurationVar(&c.pollInterval, "poll", 2*time.Second, "status poll interval")
	flag.DurationVar(&c.buildTimeout, "build-timeout", 15*time.Minute, "max wait for one build to reach READY/FAILED")
	flag.DurationVar(&c.httpTimeout, "http-timeout", 60*time.Second, "per-request HTTP timeout")
	flag.StringVar(&c.logPath, "log", "", "JSONL log file (default template_stress_<ts>.jsonl)")
	flag.BoolVar(&c.keep, "keep", false, "keep templates after the loop (skip delete)")
	flag.BoolVar(&c.verifySHA, "verify-sha", true, "verify downloaded artifact sha256 against the annotation")
	flag.DurationVar(&c.reportEvery, "report", 10*time.Second, "live stats print interval")
	flag.Parse()
	if c.image == "" {
		fmt.Fprintln(os.Stderr, "-image is required")
		os.Exit(2)
	}
	if c.logPath == "" {
		c.logPath = fmt.Sprintf("template_stress_%s.jsonl", time.Now().Format("20060102_150405"))
	}
	return c
}

// ---------------------------------------------------------------------------
// Metrics: per-op + overall latency samples
// ---------------------------------------------------------------------------

type opStat struct {
	count   int64
	errors  int64
	sumMs   float64
	minMs   float64
	maxMs   float64
	samples []float64
}

type metrics struct {
	mu sync.Mutex
	// per op name ("create", "poll", "get", "list", "download", "delete", ...)
	ops     map[string]*opStat
	overall *opStat
}

func newMetrics() *metrics {
	return &metrics{ops: make(map[string]*opStat), overall: &opStat{minMs: -1}}
}

func (m *metrics) record(op string, latency time.Duration, err error) {
	ms := float64(latency) / float64(time.Millisecond)
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, s := range []*opStat{m.getOp(op), m.overall} {
		s.count++
		if err != nil {
			s.errors++
		}
		s.sumMs += ms
		if s.minMs < 0 || ms < s.minMs {
			s.minMs = ms
		}
		if ms > s.maxMs {
			s.maxMs = ms
		}
		s.samples = append(s.samples, ms)
	}
}

func (m *metrics) getOp(op string) *opStat {
	s, ok := m.ops[op]
	if !ok {
		s = &opStat{minMs: -1}
		m.ops[op] = s
	}
	return s
}

type statView struct {
	count, errors           int64
	avg, p50, p90, p99, max float64
}

func view(s *opStat) statView {
	v := statView{count: s.count, errors: s.errors, max: s.maxMs}
	if s.count == 0 {
		return v
	}
	v.avg = s.sumMs / float64(s.count)
	sorted := make([]float64, len(s.samples))
	copy(sorted, s.samples)
	sort.Float64s(sorted)
	pick := func(q float64) float64 {
		if len(sorted) == 0 {
			return 0
		}
		idx := int(q * float64(len(sorted)-1))
		return sorted[idx]
	}
	v.p50, v.p90, v.p99 = pick(0.50), pick(0.90), pick(0.99)
	return v
}

func (m *metrics) snapshot() (map[string]statView, statView) {
	m.mu.Lock()
	defer m.mu.Unlock()
	out := make(map[string]statView, len(m.ops))
	for k, v := range m.ops {
		out[k] = view(v)
	}
	return out, view(m.overall)
}

// ---------------------------------------------------------------------------
// JSONL operation log
// ---------------------------------------------------------------------------

type opLog struct {
	mu  sync.Mutex
	enc *json.Encoder
}

type opRecord struct {
	TS        string  `json:"ts"`
	Worker    int     `json:"worker"`
	Iter      int     `json:"iter"`
	Op        string  `json:"op"`
	Template  string  `json:"template_id,omitempty"`
	OK        bool    `json:"ok"`
	LatencyMs float64 `json:"latency_ms"`
	Bytes     int64   `json:"bytes,omitempty"`
	Err       string  `json:"err,omitempty"`
	Detail    string  `json:"detail,omitempty"`
}

func newOpLog(path string) (*opLog, *os.File, error) {
	f, err := os.Create(path)
	if err != nil {
		return nil, nil, err
	}
	return &opLog{enc: json.NewEncoder(f)}, f, nil
}

func (l *opLog) write(r opRecord) {
	l.mu.Lock()
	defer l.mu.Unlock()
	_ = l.enc.Encode(r)
}

// ---------------------------------------------------------------------------
// CubeMaster API client
// ---------------------------------------------------------------------------

const retCodeSuccess = 200 // errorcode.ErrorCode_Success

type apiClient struct {
	base string
	hc   *http.Client
}

type envelope struct {
	Ret struct {
		Code int    `json:"ret_code"`
		Msg  string `json:"ret_msg"`
	} `json:"ret"`
	Status    string `json:"status"`
	LastError string `json:"last_error"`
	Template  string `json:"template_id"`
	JobID     string `json:"job_id"`
	// create_request is walked as generic JSON (its struct tags live in
	// another package and are irrelevant here).
	CreateRequest map[string]any  `json:"create_request"`
	Data          json.RawMessage `json:"data"`
}

// do issues a JSON request against the /cube API and returns the decoded
// envelope. A non-200 ret_code is returned as an error carrying ret_msg.
func (a *apiClient) do(ctx context.Context, method, path string, body any) (*envelope, error) {
	var rdr io.Reader
	if body != nil {
		buf, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		rdr = bytes.NewReader(buf)
	}
	req, err := http.NewRequestWithContext(ctx, method, a.base+path, rdr)
	if err != nil {
		return nil, err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := a.hc.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	env := &envelope{}
	if err := json.Unmarshal(raw, env); err != nil {
		return nil, fmt.Errorf("decode response: %w (http %d, body %.200s)", err, resp.StatusCode, raw)
	}
	if env.Ret.Code != retCodeSuccess {
		return env, fmt.Errorf("ret_code=%d ret_msg=%s", env.Ret.Code, env.Ret.Msg)
	}
	return env, nil
}

// artifactDownloadURL extracts the artifact download URL (and expected sha256)
// from the template's stored create_request annotations.
func artifactDownloadURL(env *envelope) (downloadURL, sha string, err error) {
	cr := env.CreateRequest
	if cr == nil {
		return "", "", fmt.Errorf("create_request missing in template info")
	}
	containers, _ := cr["containers"].([]any)
	if len(containers) == 0 {
		return "", "", fmt.Errorf("create_request.containers empty")
	}
	c0, _ := containers[0].(map[string]any)
	img, _ := c0["image"].(map[string]any)
	ann, _ := img["annotations"].(map[string]any)
	downloadURL, _ = ann["cube.master.rootfs.artifact.url"].(string)
	sha, _ = ann["cube.master.rootfs.artifact.sha256"].(string)
	if downloadURL == "" {
		return "", "", fmt.Errorf("artifact url annotation missing")
	}
	return downloadURL, sha, nil
}

// ---------------------------------------------------------------------------
// Worker lifecycle
// ---------------------------------------------------------------------------

type worker struct {
	id  int
	cfg config
	api *apiClient
	dl  *http.Client // separate client: downloads can outlive the API timeout
	met *metrics
	log *opLog
}

// step runs one measured operation: records latency + outcome into metrics
// and appends a JSONL record.
func (w *worker) step(ctx context.Context, iter int, op, tplID string, fn func() (int64, string, error)) error {
	start := time.Now()
	n, detail, err := fn()
	lat := time.Since(start)
	w.met.record(op, lat, err)
	rec := opRecord{
		TS:        time.Now().Format(time.RFC3339Nano),
		Worker:    w.id,
		Iter:      iter,
		Op:        op,
		Template:  tplID,
		OK:        err == nil,
		LatencyMs: float64(lat) / float64(time.Millisecond),
		Bytes:     n,
		Detail:    detail,
	}
	if err != nil {
		rec.Err = err.Error()
	}
	w.log.write(rec)
	return err
}

func (w *worker) run(ctx context.Context) {
	for iter := 0; ; iter++ {
		if ctx.Err() != nil {
			return
		}
		if w.cfg.iterations > 0 && iter >= w.cfg.iterations {
			return
		}
		w.lifecycle(ctx, iter)
	}
}

func (w *worker) lifecycle(ctx context.Context, iter int) {
	tplID := fmt.Sprintf("tpl-stress-w%di%d-%d", w.id, iter, time.Now().UnixNano()%1_000_000)
	lifecycleStart := time.Now()

	// 1. create from image.
	var createErr error
	_ = w.step(ctx, iter, "create", tplID, func() (int64, string, error) {
		env, err := w.api.do(ctx, http.MethodPost, "/cube/template/from-image", map[string]any{
			"RequestID":        fmt.Sprintf("stress-%d-%d", w.id, iter),
			"template_id":      tplID,
			"source_image_ref": w.cfg.image,
			"instance_type":    w.cfg.instanceType,
		})
		if err != nil {
			createErr = err
			return 0, "", err
		}
		return 0, "status=" + env.Status, nil
	})
	if createErr != nil {
		w.cleanup(ctx, iter, tplID)
		return
	}

	// 2. poll until READY / FAILED. Each poll is a "get" sample; the whole
	// wait is recorded once as time-to-ready (or time-to-fail).
	ready, pollErr := w.pollUntilTerminal(ctx, iter, tplID)
	if !ready {
		w.cleanup(ctx, iter, tplID)
		if pollErr != nil {
			w.log.write(opRecord{
				TS: time.Now().Format(time.RFC3339Nano), Worker: w.id, Iter: iter,
				Op: "build", Template: tplID, OK: false, Err: pollErr.Error(),
			})
		}
		return
	}

	// 3. query info with create_request (also the source of the download URL).
	var downloadURL, wantSHA string
	getErr := w.step(ctx, iter, "get", tplID, func() (int64, string, error) {
		env, err := w.api.do(ctx, http.MethodGet,
			"/cube/template?template_id="+tplID+"&include_request=true", nil)
		if err != nil {
			return 0, "", err
		}
		u, sha, err := artifactDownloadURL(env)
		if err != nil {
			return 0, "", err
		}
		downloadURL, wantSHA = u, sha
		return 0, "status=" + env.Status, nil
	})

	// 4. list.
	_ = w.step(ctx, iter, "list", tplID, func() (int64, string, error) {
		env, err := w.api.do(ctx, http.MethodGet, "/cube/template", nil)
		if err != nil {
			return 0, "", err
		}
		return int64(len(env.Data)), "", nil
	})

	// 5. download the artifact (full stream; optional sha256 verify).
	if getErr == nil {
		_ = w.step(ctx, iter, "download", tplID, func() (int64, string, error) {
			req, err := http.NewRequestWithContext(ctx, http.MethodGet, downloadURL, nil)
			if err != nil {
				return 0, "", err
			}
			resp, err := w.dl.Do(req)
			if err != nil {
				return 0, "", err
			}
			defer resp.Body.Close()
			if resp.StatusCode != http.StatusOK {
				return 0, "", fmt.Errorf("http %d", resp.StatusCode)
			}
			h := sha256.New()
			n, err := io.Copy(io.MultiWriter(io.Discard, h), resp.Body)
			if err != nil {
				return n, "", err
			}
			if w.cfg.verifySHA && wantSHA != "" {
				if got := hex.EncodeToString(h.Sum(nil)); got != wantSHA {
					return n, "", fmt.Errorf("sha256 mismatch: got %s want %s", got, wantSHA)
				}
			}
			return n, "ok", nil
		})
	}

	// 6. delete (+ confirm it is gone: read-after-delete must be 130404).
	w.cleanup(ctx, iter, tplID)

	w.met.record("lifecycle", time.Since(lifecycleStart), nil)
}

// pollUntilTerminal polls template info until READY (true) or FAILED /
// timeout / ctx cancel (false).
func (w *worker) pollUntilTerminal(ctx context.Context, iter int, tplID string) (bool, error) {
	deadline := time.Now().Add(w.cfg.buildTimeout)
	for {
		var status, lastErr string
		var queryErr error
		_ = w.step(ctx, iter, "poll", tplID, func() (int64, string, error) {
			env, err := w.api.do(ctx, http.MethodGet, "/cube/template?template_id="+tplID, nil)
			if err != nil {
				// 130404 right after create is possible while the definition
				// does not exist yet; keep polling until the deadline.
				queryErr = err
				return 0, "", nil
			}
			status, lastErr = env.Status, env.LastError
			return 0, "status=" + status, nil
		})
		switch strings.ToUpper(status) {
		case "READY":
			return true, nil
		case "FAILED":
			return false, fmt.Errorf("build failed: %s", lastErr)
		}
		if time.Now().After(deadline) {
			return false, fmt.Errorf("build timeout after %s (last status=%s err=%v)", w.cfg.buildTimeout, status, queryErr)
		}
		select {
		case <-ctx.Done():
			return false, ctx.Err()
		case <-time.After(w.cfg.pollInterval):
		}
	}
}

// cleanup deletes the template and confirms a subsequent read reports
// not-found (130404) — the read-after-delete consistency check.
func (w *worker) cleanup(ctx context.Context, iter int, tplID string) {
	if w.cfg.keep {
		return
	}
	_ = w.step(ctx, iter, "delete", tplID, func() (int64, string, error) {
		_, err := w.api.do(ctx, http.MethodDelete, "/cube/template", map[string]any{
			"template_id": tplID,
		})
		return 0, "", err
	})
	_ = w.step(ctx, iter, "delete_confirm", tplID, func() (int64, string, error) {
		env, err := w.api.do(ctx, http.MethodGet, "/cube/template?template_id="+tplID, nil)
		if err == nil {
			return 0, "", fmt.Errorf("template still readable after delete (status=%s)", env.Status)
		}
		if !strings.Contains(err.Error(), "ret_code=130404") {
			return 0, "", fmt.Errorf("expected 130404 after delete, got: %v", err)
		}
		return 0, "", nil
	})
}

// ---------------------------------------------------------------------------
// Live + final reporting
// ---------------------------------------------------------------------------

func printStats(m *metrics, elapsed time.Duration, header string) {
	ops, overall := m.snapshot()
	fmt.Printf("\n%s (elapsed %s)\n", header, elapsed.Round(time.Second))
	fmt.Printf("%-16s %8s %6s %10s %10s %10s %10s %10s\n",
		"op", "count", "err", "avg", "p50", "p90", "p99", "max")
	names := make([]string, 0, len(ops))
	for n := range ops {
		names = append(names, n)
	}
	sort.Strings(names)
	for _, n := range names {
		v := ops[n]
		fmt.Printf("%-16s %8d %6d %9.1fms %9.1fms %9.1fms %9.1fms %9.1fms\n",
			n, v.count, v.errors, v.avg, v.p50, v.p90, v.p99, v.max)
	}
	fmt.Printf("%-16s %8d %6d %9.1fms %9.1fms %9.1fms %9.1fms %9.1fms\n",
		"OVERALL", overall.count, overall.errors, overall.avg, overall.p50, overall.p90, overall.p99, overall.max)
	if elapsed > 0 {
		fmt.Printf("throughput: %.1f ops/s, error rate: %.2f%%\n",
			float64(overall.count)/elapsed.Seconds(),
			100*float64(overall.errors)/float64(max(overall.count, 1)))
	}
}

func main() {
	cfg := parseFlags()

	log, logFile, err := newOpLog(cfg.logPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "open log file: %v\n", err)
		os.Exit(1)
	}
	defer logFile.Close()

	met := newMetrics()
	api := &apiClient{
		base: strings.TrimRight(cfg.addr, "/"),
		hc:   &http.Client{Timeout: cfg.httpTimeout},
	}
	dl := &http.Client{Timeout: 0} // downloads: no client-side cap, ctx governs

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	if cfg.duration > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, cfg.duration)
		defer cancel()
	}

	fmt.Printf("template stress: addr=%s image=%s c=%d n=%d d=%s log=%s\n",
		cfg.addr, cfg.image, cfg.concurrency, cfg.iterations, cfg.duration, cfg.logPath)
	start := time.Now()

	var wg sync.WaitGroup
	for i := 0; i < cfg.concurrency; i++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()
			w := &worker{id: id, cfg: cfg, api: api, dl: dl, met: met, log: log}
			w.run(ctx)
		}(i)
	}

	// Live stats printer.
	done := make(chan struct{})
	go func() {
		t := time.NewTicker(cfg.reportEvery)
		defer t.Stop()
		for {
			select {
			case <-done:
				return
			case <-t.C:
				printStats(met, time.Since(start), "live stats")
			}
		}
	}()

	wg.Wait()
	close(done)
	elapsed := time.Since(start)
	printStats(met, elapsed, "final stats")

	// Persist the final summary into the JSONL log as a closing record.
	log.write(opRecord{
		TS: time.Now().Format(time.RFC3339Nano), Op: "summary", OK: true,
		Detail: fmt.Sprintf("elapsed=%s total_ops=%d", elapsed.Round(time.Second), func() int64 {
			_, o := met.snapshot()
			return o.count
		}()),
	})
	fmt.Printf("\nlog written to %s\n", cfg.logPath)
}
