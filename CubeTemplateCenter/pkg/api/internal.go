// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package api

import (
	"context"
	"net/http"

	"github.com/gin-gonic/gin"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/log"
	"github.com/tencentcloud/CubeSandbox/CubeTemplateCenter/pkg/build"
)

// handleBuildSubmit receives a build job from CubeMaster and starts the build.
// TC will report status back to CubeMaster via POST /internal/template/jobs/:job_id/status.
func handleBuildSubmit(c *gin.Context) {
	var req BuildJobRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, ErrorResponse{Error: err.Error()})
		return
	}

	log.G(c.Request.Context()).Infof("received build job: job_id=%s image=%s", req.JobID, req.Request.SourceImageRef)

	// Start build in background. Detach from the gin request context —
	// it is canceled as soon as the handler returns, which would abort
	// the in-flight build.
	go func() {
		ctx := context.Background()
		if err := build.Build(ctx, req.JobID, req.Request, req.DownloadBaseURL, req.EnvdSHA256, req.EnvdData); err != nil {
			log.G(ctx).Errorf("build template fail: job_id=%s err=%v", req.JobID, err)
		}
	}()

	c.JSON(http.StatusOK, BuildJobResponse{Status: "accepted", JobID: req.JobID})
}

// RegisterInternalRoutes registers TC's internal API routes.
func RegisterInternalRoutes(g *gin.RouterGroup) {
	g.POST("/tc/api/v1/build", handleBuildSubmit)
}
