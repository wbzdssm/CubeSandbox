// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0

package httpservice

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/constants"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/db/models"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/templatecenter"
	"github.com/tencentcloud/CubeSandbox/CubeTemplateCenter/pkg/api"
	"github.com/tencentcloud/CubeSandbox/pkgs/blobstore/configenv"
)

func TestArtifactDownloadHandlerOnlyUsesComponentAuthentication(t *testing.T) {
	gin.SetMode(gin.TestMode)
	body := []byte("rootfs-bytes")
	path := filepath.Join(t.TempDir(), "artifact.ext4")
	if err := os.WriteFile(path, body, 0o600); err != nil {
		t.Fatal(err)
	}
	artifact := &models.RootfsArtifact{
		ArtifactID:    "artifact-1",
		Generation:    2,
		Ext4Path:      path,
		Ext4SizeBytes: int64(len(body)),
		Status:        templatecenter.ArtifactStatusReady,
	}
	oldLoader := loadDirectDownloadArtifact
	loadDirectDownloadArtifact = func(_ *gin.Context, artifactID string) (*models.RootfsArtifact, error) {
		if artifactID != artifact.ArtifactID {
			t.Fatalf("artifact id = %q", artifactID)
		}
		copy := *artifact
		return &copy, nil
	}
	t.Cleanup(func() { loadDirectDownloadArtifact = oldLoader })
	secret := strings.Repeat("tc-control-key-", 3)
	t.Setenv(constants.TemplateCallbackTokenEnv, secret)
	router := gin.New()
	api.InternalAPIGroup(router.Group("")).GET("/artifacts/:artifact_id/ext4", artifactDownloadHandler)
	request := func(componentToken string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(http.MethodGet, "/tc/api/v1/artifacts/artifact-1/ext4", nil)
		req.Header.Set(constants.TemplateCallbackTokenHeader, componentToken)
		req.Header.Set("X-Cube-Artifact-Token", "not-a-ticket")
		rec := httptest.NewRecorder()
		router.ServeHTTP(rec, req)
		return rec
	}
	ok := request(secret)
	if ok.Code != http.StatusOK || ok.Body.String() != string(body) {
		t.Fatalf("component download = status %d body %q", ok.Code, ok.Body.String())
	}
	if got := request("").Code; got != http.StatusUnauthorized {
		t.Fatalf("node ticket alone status = %d, want 401", got)
	}
	if got := request("wrong-component-secret").Code; got != http.StatusUnauthorized {
		t.Fatalf("wrong component credential status = %d, want 401", got)
	}
	if got := ok.Header().Get("ETag"); got != `"abc123"` {
		t.Fatalf("ETag = %q", got)
	}
	for _, tc := range []struct {
		match  string
		status int
		body   string
	}{
		{`"abc123"`, http.StatusPartialContent, string(body[2:])},
		{`"old-generation"`, http.StatusOK, string(body)},
	} {
		req := httptest.NewRequest(http.MethodGet, "/tc/api/v1/artifacts/artifact-1/ext4", nil)
		req.Header.Set(constants.TemplateCallbackTokenHeader, secret)
		req.Header.Set("Range", "bytes=2-")
		req.Header.Set("If-Range", tc.match)
		rec := httptest.NewRecorder()
		router.ServeHTTP(rec, req)
		if rec.Code != tc.status || rec.Body.String() != tc.body {
			t.Fatalf("If-Range %s: %d %q", tc.match, rec.Code, rec.Body.String())
		}
	}
	artifact.Status = templatecenter.ArtifactStatusFailed
	if got := request(secret).Code; got != http.StatusGone {
		t.Fatalf("non-ready artifact status = %d, want 410", got)
	}
}

func TestDirectArtifactPathConstrainsFSObjectKey(t *testing.T) {
	root := t.TempDir()
	t.Setenv(configenv.EnvArtifactStoreFSRoot, root)
	valid := &models.RootfsArtifact{
		ArtifactID:     "artifact-1",
		StorageBackend: "fs",
		ObjectKey:      "templates/artifact-1.ext4",
	}
	got, err := directArtifactPath(valid)
	if err != nil {
		t.Fatal(err)
	}
	want := filepath.Join(root, "_data", "templates", "artifact-1.ext4")
	if got != want {
		t.Fatalf("path = %q, want %q", got, want)
	}
	stableRoot := t.TempDir()
	valid.StorageRef = "file://" + stableRoot
	got, err = directArtifactPath(valid)
	if err != nil {
		t.Fatal(err)
	}
	if want := filepath.Join(stableRoot, "_data", "templates", "artifact-1.ext4"); got != want {
		t.Fatalf("stable-locator path = %q, want %q", got, want)
	}

	valid.ObjectKey = "../escape.ext4"
	if _, err := directArtifactPath(valid); err == nil {
		t.Fatal("escaping object key unexpectedly accepted")
	}
	valid.StorageBackend = "s3"
	valid.ObjectKey = "artifact.ext4"
	if _, err := directArtifactPath(valid); err == nil {
		t.Fatal("s3 artifact unexpectedly mapped to a local path")
	}
}
