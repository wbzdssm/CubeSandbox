// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0

package cube

import (
	"errors"
	"io"
	"net/http"
	"os"
	"regexp"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/constants"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/db/models"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/templatecenter"
	"gorm.io/gorm"
)

var artifactDownloadIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$`)

// handleArtifactDownloadTicket is called by Cubelet immediately before a
// Master-proxied file download or direct S3 download. Replica authorization is checked on every refresh,
// so an expired capability cannot be renewed after placement is removed.
func handleArtifactDownloadTicket(c *gin.Context) {
	c.Header("Cache-Control", "no-store")
	artifactID := strings.TrimSpace(c.Param("artifact_id"))
	nodeID := strings.TrimSpace(c.GetHeader(constants.ArtifactDownloadNodeIDHeader))
	if !artifactDownloadIDPattern.MatchString(artifactID) || !artifactDownloadIDPattern.MatchString(nodeID) {
		c.JSON(http.StatusBadRequest, gin.H{"error": "valid artifact_id and X-Cube-Node-ID are required"})
		return
	}
	ticket, err := templatecenter.IssueArtifactDownloadTicket(
		c.Request.Context(),
		artifactID,
		nodeID,
		c.GetHeader(constants.ArtifactDownloadRefreshHeader),
	)
	if err == nil {
		c.JSON(http.StatusOK, ticket)
		return
	}
	switch {
	case errors.Is(err, templatecenter.ErrArtifactDownloadUnauthorized):
		c.JSON(http.StatusForbidden, gin.H{"error": "artifact download is not authorized"})
	case errors.Is(err, templatecenter.ErrArtifactDownloadUnconfigured):
		c.JSON(http.StatusServiceUnavailable, gin.H{"error": err.Error()})
	case errors.Is(err, gorm.ErrRecordNotFound):
		c.JSON(http.StatusNotFound, gin.H{"error": "artifact not found"})
	default:
		c.JSON(http.StatusInternalServerError, gin.H{"error": "issue artifact download ticket failed"})
	}
}

var verifyMasterArtifactDownload = templatecenter.VerifyArtifactDownloadToken
var templateArtifactHTTPClient = &http.Client{
	Transport:     artifactProxyHTTPClient.Transport,
	CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
}

func handleMasterArtifactDownload(c *gin.Context) {
	artifactID := strings.TrimSpace(c.Param("artifact_id"))
	nodeID := strings.TrimSpace(c.GetHeader(constants.ArtifactDownloadNodeIDHeader))
	if !artifactDownloadIDPattern.MatchString(artifactID) || !artifactDownloadIDPattern.MatchString(nodeID) {
		c.Status(http.StatusBadRequest)
		return
	}
	artifact, err := verifyMasterArtifactDownload(c.Request.Context(), artifactID, nodeID, c.GetHeader("X-Cube-Artifact-Token"))
	if err != nil {
		switch {
		case errors.Is(err, templatecenter.ErrArtifactDownloadUnauthorized):
			c.Status(http.StatusForbidden)
		case errors.Is(err, gorm.ErrRecordNotFound):
			c.Status(http.StatusNotFound)
		default:
			c.Status(http.StatusServiceUnavailable)
		}
		return
	}
	proxyTemplateCenterArtifact(c, artifact)
}

func proxyTemplateCenterArtifact(c *gin.Context, artifact *models.RootfsArtifact) {
	upstreamURL, err := templatecenter.TemplateCenterArtifactDownloadURL(artifact)
	callbackToken := strings.TrimSpace(os.Getenv(constants.TemplateCallbackTokenEnv))
	if err != nil || callbackToken == "" {
		c.Status(http.StatusServiceUnavailable)
		return
	}
	request, err := http.NewRequestWithContext(c.Request.Context(), c.Request.Method, upstreamURL, nil)
	if err != nil {
		c.Status(http.StatusBadGateway)
		return
	}
	request.Header.Set(constants.TemplateCallbackTokenHeader, callbackToken)
	for _, key := range []string{"Range", "If-Range", "If-Modified-Since", "If-None-Match"} {
		if value := c.GetHeader(key); value != "" {
			request.Header.Set(key, value)
		}
	}
	response, err := templateArtifactHTTPClient.Do(request)
	if err != nil {
		c.Status(http.StatusBadGateway)
		return
	}
	defer response.Body.Close()
	if response.StatusCode >= 300 && response.StatusCode < 400 && response.StatusCode != http.StatusNotModified {
		c.Status(http.StatusBadGateway)
		return
	}
	_ = http.NewResponseController(c.Writer).SetWriteDeadline(time.Time{})
	copyArtifactProxyHeaders(c.Writer.Header(), response.Header)
	c.Header("X-Cube-Artifact-Id", artifact.ArtifactID)
	c.Status(response.StatusCode)
	if c.Request.Method != http.MethodHead {
		_, _ = io.Copy(c.Writer, response.Body)
	}
}
