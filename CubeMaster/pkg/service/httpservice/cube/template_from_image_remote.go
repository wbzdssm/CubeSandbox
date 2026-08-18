// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package cube

import (
	"context"
	"strings"
	"time"

	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/config"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/log"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox/types"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/tcclient"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/templatecenter"
)

// remoteTemplateBuildEnabled reports whether template-from-image builds
// should be forwarded to the standalone CubeTemplateCenter process instead
// of running in-process (gray rollout switch).
func remoteTemplateBuildEnabled() bool {
	cfg := config.GetConfig()
	if cfg == nil || cfg.Common == nil {
		return false
	}
	return strings.EqualFold(strings.TrimSpace(cfg.Common.TemplateBuildMode), "remote")
}

// forwardBuildJobToTemplateCenter pushes an already-persisted job to
// CubeTemplateCenter. Runs in a background goroutine; any transport failure
// marks the job FAILED so it never hangs in PENDING.
func forwardBuildJobToTemplateCenter(jobID string, req *types.CreateTemplateFromImageReq, downloadBaseURL string, envdPayload *templatecenter.EnvdInjectionPayload) {
	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	cfg := config.GetConfig()
	endpoint := ""
	if cfg != nil && cfg.Common != nil {
		endpoint = strings.TrimSpace(cfg.Common.TemplateCenterEndpoint)
	}

	markFailed := func(msg string) {
		if err := templatecenter.UpdateTemplateImageJob(ctx, jobID, map[string]any{
			"status":        templatecenter.JobStatusFailed,
			"error_message": msg,
		}); err != nil {
			log.G(ctx).Errorf("forward to templatecenter: mark job failed fail: job_id=%s err=%v", jobID, err)
		}
	}

	if endpoint == "" {
		log.G(ctx).Errorf("forward to templatecenter: template_center_endpoint is empty, job_id=%s", jobID)
		markFailed("template_build_mode=remote but template_center_endpoint is not configured")
		return
	}

	var envdSHA string
	var envdData []byte
	if envdPayload != nil {
		envdSHA = envdPayload.SHA256
		envdData = envdPayload.Data
	}

	if err := tcclient.NewClient(endpoint).SubmitBuildJob(ctx, jobID, req, downloadBaseURL, envdSHA, envdData); err != nil {
		log.G(ctx).Errorf("forward to templatecenter fail: job_id=%s endpoint=%s err=%v", jobID, endpoint, err)
		markFailed("forward build job to templatecenter: " + err.Error())
		return
	}
	log.G(ctx).Infof("build job forwarded to templatecenter: job_id=%s endpoint=%s", jobID, endpoint)
}
