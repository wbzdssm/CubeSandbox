// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package cube

import (
	"encoding/json"
	"net/http"
	"strings"

	"github.com/gin-gonic/gin"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/log"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/templatecenter"
)

// templateJobStatusUpdatableColumns is the whitelist of image_jobs columns
// that a CubeTemplateCenter status report may update. Anything else in the
// payload is folded into result_json (final report only) or dropped.
var templateJobStatusUpdatableColumns = map[string]bool{
	"status":                    true,
	"phase":                     true,
	"progress":                  true,
	"error_message":             true,
	"artifact_id":               true,
	"artifact_status":           true,
	"source_image_digest":       true,
	"template_spec_fingerprint": true,
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
		// JSON numbers decode as float64; normalize progress to int64 so the
		// int4 column update works on both MySQL and Postgres drivers.
		if k == "progress" {
			if f, ok := v.(float64); ok {
				v = int64(f)
			}
		}
		values[k] = v
	}

	// The final BUILT report carries artifact details (ext4_path / sha / size)
	// that have no image_jobs column; keep the raw payload in result_json so
	// the follow-up distribution step can pick them up.
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

	// TODO(remote-build): when status == JobStatusBuilt, trigger the
	// post-build pipeline here (finalize rootfs_artifacts record, distribute
	// artifact to Cubelet nodes, create template_definitions) by resuming the
	// job with the artifact metadata stored in result_json.
	if status, _ := payload["status"].(string); strings.EqualFold(status, templatecenter.JobStatusBuilt) {
		log.G(ctx).Warnf("template job %s BUILT remotely; distribution/template registration is not wired yet (TODO)", jobID)
	}

	c.JSON(http.StatusOK, gin.H{"status": "ok"})
}
