// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package build

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/log"
)

// Reporter reports build status back to CubeMaster.
// CubeMaster receives the callback and writes DB.
type Reporter struct {
	masterURL  string
	httpClient *http.Client
}

// NewReporter creates a new status reporter. The CubeMaster base URL comes
// from CUBE_MASTER_ENDPOINT (default http://localhost:8089) — TC has no
// config knob of its own for this yet.
func NewReporter() *Reporter {
	masterURL := strings.TrimSpace(os.Getenv("CUBE_MASTER_ENDPOINT"))
	if masterURL == "" {
		masterURL = "http://localhost:8089"
	}
	return &Reporter{
		masterURL: strings.TrimRight(masterURL, "/"),
		httpClient: &http.Client{
			Timeout: 10 * time.Second,
		},
	}
}

// Report sends a status update to CubeMaster.
// CubeMaster's POST /internal/template/jobs/:job_id/status handler receives this.
func (r *Reporter) Report(ctx context.Context, jobID string, fields map[string]any) error {
	body, err := json.Marshal(fields)
	if err != nil {
		return fmt.Errorf("marshal status: %w", err)
	}

	url := fmt.Sprintf("%s/internal/template/jobs/%s/status", r.masterURL, jobID)
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")

	log.G(ctx).Infof("report status to master: job_id=%s url=%s fields=%+v", jobID, url, fields)

	resp, err := r.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("http post: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		respBody, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("master returned %d: %s", resp.StatusCode, string(respBody))
	}

	return nil
}

// Close cleans up the reporter.
func (r *Reporter) Close() {
	// Nothing to close for now
}
