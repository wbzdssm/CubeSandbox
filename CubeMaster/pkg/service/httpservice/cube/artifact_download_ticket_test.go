// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0

package cube

import (
	"context"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/config"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/db/models"
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


