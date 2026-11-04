// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0

package cube

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/config"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/constants"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/db/models"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/templatecenter"
	"github.com/tencentcloud/CubeSandbox/pkgs/CubeLog"
)

func TestLegacyArtifactProxyAllowsSlowStreams(t *testing.T) {
	gin.SetMode(gin.TestMode)
	object := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Length", "6")
		for _, chunk := range []string{"ab", "cd", "ef"} {
			time.Sleep(80 * time.Millisecond)
			_, _ = w.Write([]byte(chunk))
			w.(http.Flusher).Flush()
		}
	}))
	defer object.Close()
	oldLookup := getRootfsArtifactForRedirectFn
	t.Cleanup(func() { getRootfsArtifactForRedirectFn = oldLookup })
	getRootfsArtifactForRedirectFn = func(context.Context, string, string) (*models.RootfsArtifact, error) {
		return &models.RootfsArtifact{ArtifactID: "slow", StorageBackend: "s3", ArtifactURL: object.URL}, nil
	}
	// TC registers the legacy handler; Master registers the reverse proxy.
	tcRouter := gin.New()
	tcRouter.Use(func(c *gin.Context) {
		c.Request = c.Request.WithContext(CubeLog.WithRequestTrace(c.Request.Context(), &CubeLog.RequestTrace{}))
		c.Next()
	})
	tcRouter.GET("/cube"+TemplateArtifactDownloadAction, downloadTemplateArtifactGinHandler)
	tc := httptest.NewUnstartedServer(tcRouter)
	tc.Config.WriteTimeout = 30 * time.Millisecond
	tc.Start()
	defer tc.Close()
	t.Setenv(config.EnvTemplateCenterAddr, tc.URL)
	masterRouter := gin.New()
	masterRouter.GET("/cube"+TemplateArtifactDownloadAction, proxyToTemplateCenter)
	master := httptest.NewUnstartedServer(masterRouter)
	master.Config.WriteTimeout = 30 * time.Millisecond
	master.Start()
	defer master.Close()
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get(master.URL + "/cube" + TemplateArtifactDownloadAction + "?artifact_id=slow")
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil || resp.StatusCode != http.StatusOK || string(data) != "abcdef" {
		t.Fatalf("status=%d data=%q err=%v", resp.StatusCode, data, err)
	}
}

func TestMasterArtifactDownloadStripsTicketsAndProxiesRange(t *testing.T) {
	gin.SetMode(gin.TestMode)
	componentToken := "existing-component-token"
	t.Setenv(constants.TemplateCallbackTokenEnv, componentToken)
	calls := 0
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.URL.Path != "/tc/api/v1/artifacts/artifact-1/ext4" || r.Header.Get(constants.TemplateCallbackTokenHeader) != componentToken {
			t.Error("wrong component request")
		}
		if r.Header.Get("X-Cube-Artifact-Token") != "" || r.Header.Get(constants.ArtifactDownloadRefreshHeader) != "" || r.Header.Get(constants.ArtifactDownloadNodeIDHeader) != "" {
			t.Error("node credentials leaked to TC")
		}
		if r.Header.Get("Range") != "bytes=2-" {
			t.Error("range was not forwarded")
		}
		w.Header().Set("ETag", `"tc-checksum"`)
		w.Header().Set("Content-Range", "bytes 2-5/6")
		w.WriteHeader(http.StatusPartialContent)
		_, _ = w.Write([]byte("cdef"))
	}))
	defer upstream.Close()
	oldVerify := verifyMasterArtifactDownload
	t.Cleanup(func() { verifyMasterArtifactDownload = oldVerify })
	verifyMasterArtifactDownload = func(_ context.Context, artifactID, nodeID, token string) (*models.RootfsArtifact, error) {
		if artifactID != "artifact-1" || nodeID != "node-1" || token != "node-download-ticket" {
			return nil, templatecenter.ErrArtifactDownloadUnauthorized
		}
		return &models.RootfsArtifact{ArtifactID: artifactID, OwnerTCDownloadBase: upstream.URL, Ext4SHA256: "checksum"}, nil
	}
	router := gin.New()
	router.GET("/internal/template/artifacts/:artifact_id/ext4", handleMasterArtifactDownload)
	request := func(token string) *httptest.ResponseRecorder {
		r := httptest.NewRequest(http.MethodGet, "/internal/template/artifacts/artifact-1/ext4", nil)
		r.Header.Set(constants.ArtifactDownloadNodeIDHeader, "node-1")
		r.Header.Set("X-Cube-Artifact-Token", token)
		r.Header.Set(constants.ArtifactDownloadRefreshHeader, "must-not-leak")
		r.Header.Set("Range", "bytes=2-")
		response := httptest.NewRecorder()
		router.ServeHTTP(response, r)
		return response
	}
	if got := request("invalid"); got.Code != http.StatusForbidden || calls != 0 {
		t.Fatalf("invalid ticket reached TC: status=%d calls=%d", got.Code, calls)
	}
	got := request("node-download-ticket")
	if got.Code != http.StatusPartialContent || got.Body.String() != "cdef" || got.Header().Get("Content-Range") != "bytes 2-5/6" || got.Header().Get("ETag") != `"tc-checksum"` {
		t.Fatalf("range response: status=%d body=%q", got.Code, got.Body.String())
	}
	t.Setenv(constants.TemplateCallbackTokenEnv, "")
	if got := request("node-download-ticket"); got.Code != http.StatusServiceUnavailable || calls != 1 {
		t.Fatal("missing component credential did not fail closed")
	}
}

func TestMasterArtifactDownloadDoesNotFollowRedirects(t *testing.T) {
	gin.SetMode(gin.TestMode)
	t.Setenv(constants.TemplateCallbackTokenEnv, "existing-component-token")
	leaks := 0
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { leaks++ }))
	defer target.Close()
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL, http.StatusFound)
	}))
	defer upstream.Close()
	oldVerify := verifyMasterArtifactDownload
	t.Cleanup(func() { verifyMasterArtifactDownload = oldVerify })
	verifyMasterArtifactDownload = func(context.Context, string, string, string) (*models.RootfsArtifact, error) {
		return &models.RootfsArtifact{ArtifactID: "artifact-1", OwnerTCDownloadBase: upstream.URL}, nil
	}
	router := gin.New()
	router.GET("/internal/template/artifacts/:artifact_id/ext4", handleMasterArtifactDownload)
	r := httptest.NewRequest(http.MethodGet, "/internal/template/artifacts/artifact-1/ext4", nil)
	r.Header.Set(constants.ArtifactDownloadNodeIDHeader, "node-1")
	response := httptest.NewRecorder()
	router.ServeHTTP(response, r)
	if response.Code != http.StatusBadGateway || leaks != 0 || strings.Contains(response.Body.String(), target.URL) {
		t.Fatalf("redirect followed: status=%d leaks=%d", response.Code, leaks)
	}
}
