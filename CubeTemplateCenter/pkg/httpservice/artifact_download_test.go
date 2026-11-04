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
	"time"

	"github.com/gin-gonic/gin"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/constants"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/db/models"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/templatecenter"
	"github.com/tencentcloud/CubeSandbox/pkgs/blobstore/configenv"
	"github.com/tencentcloud/CubeSandbox/pkgs/proto/artifactticket"
)

func TestArtifactDownloadHandlerValidatesTokenIdentityAndGeneration(t *testing.T) {
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
	secret := strings.Repeat("tc-download-key-", 3)
	t.Setenv(constants.ArtifactDownloadSigningKeyEnv, secret)
	router := gin.New()
	router.GET("/internal/artifacts/:artifact_id/ext4", artifactDownloadHandler)

	makeToken := func(generation int64, nodeID string) string {
		token, err := artifactticket.Sign([]byte(secret), artifactticket.Claims{
			ArtifactID: "artifact-1",
			NodeID:     nodeID,
			ExpiresAt:  time.Now().Add(time.Hour).Unix(),
			Generation: generation,
			Purpose:    artifactticket.PurposeDownload,
		})
		if err != nil {
			t.Fatal(err)
		}
		return token
	}
	request := func(token, nodeID string) *httptest.ResponseRecorder {
		req := httptest.NewRequest(http.MethodGet, "/internal/artifacts/artifact-1/ext4", nil)
		req.Header.Set(constants.ArtifactDownloadNodeIDHeader, nodeID)
		req.Header.Set("X-Cube-Artifact-Token", token)
		rec := httptest.NewRecorder()
		router.ServeHTTP(rec, req)
		return rec
	}

	ok := request(makeToken(2, "node-1"), "node-1")
	if ok.Code != http.StatusOK || ok.Body.String() != string(body) {
		t.Fatalf("valid download = status %d body %q", ok.Code, ok.Body.String())
	}
	if got := ok.Header().Get("X-Cube-Artifact-Id"); got != "artifact-1" {
		t.Fatalf("artifact header = %q", got)
	}

	if got := request(makeToken(1, "node-1"), "node-1").Code; got != http.StatusGone {
		t.Fatalf("stale generation status = %d, want 410", got)
	}
	if got := request(makeToken(2, "node-2"), "node-1").Code; got != http.StatusUnauthorized {
		t.Fatalf("wrong node status = %d, want 401", got)
	}
	token := makeToken(2, "node-1")
	if got := request(token+"x", "node-1").Code; got != http.StatusUnauthorized {
		t.Fatalf("tampered token status = %d, want 401", got)
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
