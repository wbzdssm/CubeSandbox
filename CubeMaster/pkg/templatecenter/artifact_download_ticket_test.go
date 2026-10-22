// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0

package templatecenter

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/glebarez/sqlite"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/constants"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/db/models"
	"github.com/tencentcloud/CubeSandbox/pkgs/proto/artifactticket"
	"gorm.io/gorm"
)

func TestArtifactDownloadTTL(t *testing.T) {
	t.Setenv(constants.ArtifactDownloadTTLEnv, "")
	if got, err := artifactDownloadTTL(); err != nil || got != time.Hour {
		t.Fatalf("default ttl = %s, %v; want 1h", got, err)
	}

	t.Setenv(constants.ArtifactDownloadTTLEnv, "2h")
	if got, err := artifactDownloadTTL(); err != nil || got != 2*time.Hour {
		t.Fatalf("configured ttl = %s, %v; want 2h", got, err)
	}

	t.Setenv(constants.ArtifactDownloadTTLEnv, "")
	t.Setenv(constants.ArtifactDownloadTokenTTLEnv, "90m")
	if got, err := artifactDownloadTTL(); err != nil || got != 90*time.Minute {
		t.Fatalf("alias ttl = %s, %v; want 90m", got, err)
	}

	t.Setenv(constants.ArtifactDownloadTokenTTLEnv, "")
	for _, value := range []string{"not-a-duration", "30s", "25h"} {
		t.Run(value, func(t *testing.T) {
			t.Setenv(constants.ArtifactDownloadTTLEnv, value)
			if _, err := artifactDownloadTTL(); err == nil {
				t.Fatalf("invalid ttl %q unexpectedly accepted", value)
			}
		})
	}
}

func TestBuildArtifactDownloadTicketForTemplateCenter(t *testing.T) {
	secret := strings.Repeat("download-key-", 3)
	t.Setenv(constants.ArtifactDownloadSigningKeyEnv, secret)
	t.Setenv(constants.ArtifactDownloadTTLEnv, "2h")
	now := time.Unix(1_800_000_000, 0)
	artifact := &models.RootfsArtifact{
		ArtifactID:          "artifact-1",
		Generation:          4,
		StorageBackend:      "fs",
		OwnerTCDownloadBase: "http://tc.internal:8080/base?ignored=1",
		Ext4SizeBytes:       1234,
		Ext4SHA256:          "abc123",
	}

	ticket, err := buildArtifactDownloadTicket(context.Background(), artifact, "node-1", now)
	if err != nil {
		t.Fatal(err)
	}
	if ticket.Source != artifactticket.SourceTemplateCenter ||
		ticket.URL != "http://tc.internal:8080/base/internal/artifacts/artifact-1/ext4" ||
		ticket.ExpiresAt.Sub(now) != 2*time.Hour ||
		ticket.SizeBytes != artifact.Ext4SizeBytes ||
		ticket.SHA256 != artifact.Ext4SHA256 {
		t.Fatalf("ticket = %+v", ticket)
	}
	claims, err := artifactticket.Verify([]byte(secret), ticket.Token, now)
	if err != nil {
		t.Fatal(err)
	}
	if claims.ArtifactID != artifact.ArtifactID || claims.NodeID != "node-1" ||
		claims.Generation != artifact.Generation || claims.ExpiresAt != ticket.ExpiresAt.Unix() ||
		claims.Purpose != artifactticket.PurposeDownload {
		t.Fatalf("claims = %+v, ticket = %+v", claims, ticket)
	}
}

func TestBuildArtifactDownloadRefreshToken(t *testing.T) {
	secret := strings.Repeat("refresh-key-", 3)
	t.Setenv(constants.ArtifactDownloadSigningKeyEnv, secret)
	now := time.Unix(1_800_000_000, 0)
	token, err := buildArtifactDownloadRefreshToken(&models.RootfsArtifact{
		ArtifactID: "artifact-1",
		Generation: 7,
	}, "node-1", now)
	if err != nil {
		t.Fatal(err)
	}
	claims, err := artifactticket.Verify([]byte(secret), token, now)
	if err != nil {
		t.Fatal(err)
	}
	if claims.Purpose != artifactticket.PurposeRefresh ||
		claims.ArtifactID != "artifact-1" || claims.NodeID != "node-1" ||
		claims.Generation != 7 || claims.ExpiresAt != now.Add(artifactRefreshTokenTTL).Unix() {
		t.Fatalf("claims = %+v", claims)
	}
}

func TestBuildArtifactDownloadTicketForS3UsesFreshBoundedURL(t *testing.T) {
	t.Setenv(constants.ArtifactDownloadTTLEnv, "2h")
	oldPresign := presignArtifactGetURLWithTTL
	t.Cleanup(func() { presignArtifactGetURLWithTTL = oldPresign })
	var gotTTL time.Duration
	presignArtifactGetURLWithTTL = func(_ context.Context, artifact *models.RootfsArtifact, ttl time.Duration) (string, error) {
		gotTTL = ttl
		if artifact.ArtifactID != "artifact-s3" {
			t.Fatalf("artifact id = %q", artifact.ArtifactID)
		}
		return "https://objects.example/new-signature", nil
	}
	now := time.Unix(1_800_000_000, 0)
	ticket, err := buildArtifactDownloadTicket(context.Background(), &models.RootfsArtifact{
		ArtifactID:     "artifact-s3",
		Generation:     2,
		StorageBackend: "s3",
		ArtifactURL:    "https://objects.example/stale-signature",
		Ext4SizeBytes:  42,
		Ext4SHA256:     "sha",
	}, "node-1", now)
	if err != nil {
		t.Fatal(err)
	}
	if gotTTL != 2*time.Hour || ticket.Source != artifactticket.SourceS3 ||
		ticket.URL != "https://objects.example/new-signature" || ticket.Token != "" {
		t.Fatalf("ticket = %+v, presign ttl = %s", ticket, gotTTL)
	}
}

func TestBuildArtifactDownloadTicketDoesNotFallBackToStoredS3URL(t *testing.T) {
	oldPresign := presignArtifactGetURLWithTTL
	t.Cleanup(func() { presignArtifactGetURLWithTTL = oldPresign })
	wantErr := errors.New("signer unavailable")
	presignArtifactGetURLWithTTL = func(context.Context, *models.RootfsArtifact, time.Duration) (string, error) {
		return "", wantErr
	}
	_, err := buildArtifactDownloadTicket(context.Background(), &models.RootfsArtifact{
		ArtifactID:     "artifact-s3",
		Generation:     1,
		StorageBackend: "s3",
		ArtifactURL:    "https://objects.example/stale-signature",
	}, "node-1", time.Now())
	if !errors.Is(err, wantErr) {
		t.Fatalf("error = %v, want signer error", err)
	}
}

func TestEnsureArtifactStableLocatorBackfillsLegacyRow(t *testing.T) {
	db, err := gorm.Open(sqlite.Open("file:artifact-stable-locator?mode=memory&cache=shared"), &gorm.Config{})
	if err != nil {
		t.Fatal(err)
	}
	if err := db.AutoMigrate(&models.RootfsArtifact{}); err != nil {
		t.Fatal(err)
	}
	artifact := &models.RootfsArtifact{
		ArtifactID: "artifact-legacy",
		Ext4Path:   "/var/lib/cube/artifact-legacy.ext4",
		Status:     ArtifactStatusReady,
	}
	if err := db.Create(artifact).Error; err != nil {
		t.Fatal(err)
	}
	oldDB := store.db
	store.db = db
	t.Cleanup(func() { store.db = oldDB })
	t.Setenv(constants.TemplateCenterDownloadAddrEnv, "http://tc.internal:8090")

	if err := ensureArtifactStableLocator(context.Background(), artifact); err != nil {
		t.Fatal(err)
	}
	var got models.RootfsArtifact
	if err := db.Where("artifact_id = ?", artifact.ArtifactID).First(&got).Error; err != nil {
		t.Fatal(err)
	}
	if got.Generation != 1 ||
		got.OwnerTCDownloadBase != "http://tc.internal:8090" ||
		got.StorageRef != "file:///var/lib/cube/artifact-legacy.ext4" {
		t.Fatalf("backfilled artifact = %+v", got)
	}
}
