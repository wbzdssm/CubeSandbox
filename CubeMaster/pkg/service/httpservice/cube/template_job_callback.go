// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package cube

import (
	"context"
	"encoding/json"
	"net/http"
	"strconv"
	"strings"

	"github.com/gin-gonic/gin"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/log"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/templatecenter"
)

// templateJobStatusUpdatableColumns is the whitelist of image_jobs columns
// that a CubeTemplateCenter status report may update. Anything else in the
// payload (e.g. the artifact metadata consumed by the resume pipeline) is
// folded into result_json on the terminal report, or dropped.
var templateJobStatusUpdatableColumns = map[string]bool{
	"status":                    true,
	"phase":                     true,
	"progress":                  true,
	"error_message":             true,
	"artifact_id":               true,
	"artifact_status":           true,
	"source_image_digest":       true,
	"template_spec_fingerprint": true,
	// Pull progress: TC flushes the durable terminal snapshot through this
	// same callback (live values go to Redis).
	"pull_total_bytes":      true,
	"pull_downloaded_bytes": true,
	"pull_total_layers":     true,
	"pull_completed_layers": true,
	"pull_speed_bps":        true,
}

// templateJobIntColumns lists whitelisted columns whose DB type is integer.
// JSON decodes numbers as float64, which some drivers reject for int columns.
var templateJobIntColumns = map[string]bool{
	"progress":              true,
	"pull_total_bytes":      true,
	"pull_downloaded_bytes": true,
	"pull_total_layers":     true,
	"pull_completed_layers": true,
	"pull_speed_bps":        true,
}

// handleTemplateJobStatusCallback receives build status reports from
// CubeTemplateCenter (remote build mode) and persists them to image_jobs.
//
// Route: POST /internal/template/jobs/:job_id/status
//
// The endpoint is internal-only: it trusts the payload because it is not
// exposed outside the cluster network (same trust model as the inner API).
func handleTemplateJobStatusCallback(c *gin.Context) {
	jobID := strings.TrimSpace(c.Param("job_id"))
	if jobID == "" {
		c.JSON(http.StatusBadRequest, gin.H{"error": "job_id is required"})
		return
	}

	var payload map[string]any
	if err := c.ShouldBindJSON(&payload); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	values := make(map[string]any, len(payload))
	for k, v := range payload {
		if !templateJobStatusUpdatableColumns[k] {
			continue
		}
		// JSON numbers decode as float64; normalize integer columns to int64
		// so the update works on both MySQL and Postgres drivers.
		if templateJobIntColumns[k] {
			v = payloadInt64(payload, k)
		}
		values[k] = v
	}

	// The BUILT report also carries artifact details (ext4_path / sha / size /
	// image config) that have no image_jobs column. Keep the raw payload in
	// result_json so the state is inspectable and the resume step can be
	// replayed from the row if needed.
	if status, _ := payload["status"].(string); strings.EqualFold(status, templatecenter.JobStatusBuilt) {
		if raw, err := json.Marshal(payload); err == nil {
			values["result_json"] = string(raw)
		}
	}

	if len(values) == 0 {
		c.JSON(http.StatusBadRequest, gin.H{"error": "no updatable fields in payload"})
		return
	}

	ctx := c.Request.Context()
	if err := templatecenter.UpdateTemplateImageJob(ctx, jobID, values); err != nil {
		log.G(ctx).Errorf("template job status callback: update fail: job_id=%s err=%v", jobID, err)
		c.JSON(http.StatusInternalServerError, gin.H{"error": err.Error()})
		return
	}

	log.G(ctx).Infof("template job status callback applied: job_id=%s status=%v phase=%v",
		jobID, payload["status"], payload["phase"])

	// A BUILT report means TC finished the data-plane work. Everything that
	// follows (registering the artifact row, distributing to Cubelet nodes,
	// writing template_definitions / replicas, claiming the alias) is
	// CubeMaster's job and runs here.
	//
	// Detach from the request context: the resume pipeline performs
	// cross-node RPCs and must not be canceled when this HTTP handler
	// returns. TC only needs the acknowledgement that the report landed.
	if status, _ := payload["status"].(string); strings.EqualFold(status, templatecenter.JobStatusBuilt) {
		result := remoteBuildResultFromPayload(payload)
		resumeCtx := log.WithLogger(context.Background(), log.G(ctx).WithFields(map[string]any{
			"job_id":      jobID,
			"artifact_id": result.ArtifactID,
			"build_mode":  "remote",
		}))
		go func() {
			if err := templatecenter.ResumeTemplateImageJobAfterRemoteBuild(resumeCtx, jobID, result); err != nil {
				log.G(resumeCtx).Errorf("resume remote-built template job fail: job_id=%s err=%v", jobID, err)
			}
		}()
	}

	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}

// remoteBuildResultFromPayload extracts the artifact metadata TC reports with
// the terminal BUILT status. Missing fields are validated downstream by
// RemoteBuildResult.Validate so the job records a precise error.
func remoteBuildResultFromPayload(payload map[string]any) *templatecenter.RemoteBuildResult {
	return &templatecenter.RemoteBuildResult{
		ArtifactID:              payloadString(payload, "artifact_id"),
		TemplateSpecFingerprint: payloadString(payload, "template_spec_fingerprint"),
		SourceImageDigest:       payloadString(payload, "source_image_digest"),
		Ext4Path:                payloadString(payload, "ext4_path"),
		Ext4SHA256:              payloadString(payload, "ext4_sha256"),
		Ext4SizeBytes:           payloadInt64(payload, "ext4_size_bytes"),
		ImageConfigJSON:         payloadString(payload, "image_config_json"),
		MasterNodeIP:            payloadString(payload, "master_node_ip"),
		CubeEgressCABaked:       payloadBool(payload, "cube_egress_ca_baked"),
		CubeEgressCAFingerprint: payloadString(payload, "cube_egress_ca_fingerprint"),
		CubeEgressCATargets:     int(payloadInt64(payload, "cube_egress_ca_targets_written")),
	}
}

func payloadString(payload map[string]any, key string) string {
	v, _ := payload[key].(string)
	return strings.TrimSpace(v)
}

// payloadInt64 handles both float64 (the default for JSON numbers) and
// json.Number / string encodings.
func payloadInt64(payload map[string]any, key string) int64 {
	switch v := payload[key].(type) {
	case float64:
		return int64(v)
	case int64:
		return v
	case int:
		return int64(v)
	case json.Number:
		n, err := v.Int64()
		if err != nil {
			return 0
		}
		return n
	case string:
		n, err := strconv.ParseInt(strings.TrimSpace(v), 10, 64)
		if err != nil {
			return 0
		}
		return n
	default:
		return 0
	}
}

func payloadBool(payload map[string]any, key string) bool {
	switch v := payload[key].(type) {
	case bool:
		return v
	case string:
		b, err := strconv.ParseBool(strings.TrimSpace(v))
		return err == nil && b
	default:
		return false
	}
}
