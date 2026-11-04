// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0

package templatecenter

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/config"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/constants"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/db/models"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/localcache"
	"github.com/tencentcloud/CubeSandbox/pkgs/blobstore/configenv"
	"github.com/tencentcloud/CubeSandbox/pkgs/proto/artifactticket"
)

const (
	defaultArtifactDownloadTTL = time.Hour
	minArtifactDownloadTTL     = time.Minute
	maxArtifactDownloadTTL     = 24 * time.Hour
	artifactRefreshTokenTTL    = 24 * time.Hour
)

var (
	ErrArtifactDownloadUnauthorized = errors.New("artifact download is not authorized for this node")
	ErrArtifactDownloadUnconfigured = errors.New("artifact direct download is not configured")
)

// ValidateArtifactDownloadSigningKey fails fast when direct-download tickets
// cannot be signed or verified consistently across Master and TC.
func ValidateArtifactDownloadSigningKey() error {
	secret := []byte(strings.TrimSpace(os.Getenv(constants.ArtifactDownloadSigningKeyEnv)))
	return artifactticket.ValidateSigningKey(secret)
}

// IssueArtifactDownloadTicket verifies current node placement and returns a
// fresh capability. It never returns a stored presigned URL.
func IssueArtifactDownloadTicket(ctx context.Context, artifactID, nodeID, refreshToken string) (*artifactticket.Ticket, error) {
	artifactID = strings.TrimSpace(artifactID)
	nodeID = strings.TrimSpace(nodeID)
	if artifactID == "" || nodeID == "" {
		return nil, ErrArtifactDownloadUnauthorized
	}
	if store == nil || store.db == nil {
		return nil, ErrTemplateStoreNotInitialized
	}
	now := time.Now()
	claims, err := artifactticket.Verify(
		[]byte(strings.TrimSpace(os.Getenv(constants.ArtifactDownloadSigningKeyEnv))),
		refreshToken,
		now,
	)
	if err != nil || claims.Purpose != artifactticket.PurposeRefresh ||
		claims.ArtifactID != artifactID || claims.NodeID != nodeID {
		return nil, ErrArtifactDownloadUnauthorized
	}
	if _, ok := localcache.GetNode(nodeID); !ok {
		return nil, ErrArtifactDownloadUnauthorized
	}
	var count int64
	if err := store.db.WithContext(ctx).Table(constants.TemplateReplicaTableName).
		Where("artifact_id = ? AND node_id = ? AND phase IN ?", artifactID, nodeID,
			[]string{ReplicaPhaseDistributing, ReplicaPhaseDistributed, ReplicaPhaseSnapshotting}).
		Count(&count).Error; err != nil {
		return nil, fmt.Errorf("check artifact replica authorization: %w", err)
	}
	if count == 0 {
		return nil, ErrArtifactDownloadUnauthorized
	}
	artifact, err := getRootfsArtifactByID(ctx, artifactID)
	if err != nil {
		return nil, err
	}
	if artifact.Status != ArtifactStatusReady {
		return nil, fmt.Errorf("artifact %s is not ready", artifactID)
	}
	if err := ensureArtifactStableLocator(ctx, artifact); err != nil {
		return nil, err
	}
	if claims.Generation != artifact.Generation {
		return nil, ErrArtifactDownloadUnauthorized
	}
	return buildArtifactDownloadTicket(ctx, artifact, nodeID, now)
}

func buildArtifactDownloadTicket(ctx context.Context, artifact *models.RootfsArtifact, nodeID string, now time.Time) (*artifactticket.Ticket, error) {
	if artifact == nil || strings.TrimSpace(artifact.ArtifactID) == "" {
		return nil, errors.New("artifact is nil or has no id")
	}
	ttl, err := artifactDownloadTTL()
	if err != nil {
		return nil, err
	}
	generation := artifact.Generation
	if generation <= 0 {
		generation = 1
	}
	expiresAt := now.Add(ttl)
	ticket := &artifactticket.Ticket{
		Method:     http.MethodGet,
		ExpiresAt:  expiresAt.UTC(),
		ServerTime: now.UTC(),
		ArtifactID: artifact.ArtifactID,
		Generation: generation,
		SizeBytes:  artifact.Ext4SizeBytes,
		SHA256:     artifact.Ext4SHA256,
	}

	if artifactUsesS3(artifact) {
		signedURL, err := presignArtifactGetURLWithTTL(ctx, artifact, ttl)
		if err != nil {
			return nil, fmt.Errorf("presign artifact %s: %w", artifact.ArtifactID, err)
		}
		if strings.TrimSpace(signedURL) == "" {
			return nil, fmt.Errorf("presign artifact %s returned an empty URL", artifact.ArtifactID)
		}
		ticket.Source = artifactticket.SourceS3
		ticket.URL = signedURL
		return ticket, nil
	}

	baseURL := strings.TrimSpace(artifact.OwnerTCDownloadBase)
	if baseURL == "" {
		baseURL = configuredTemplateCenterDownloadBase()
	}
	downloadURL, err := templateCenterArtifactURL(baseURL, artifact.ArtifactID)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrArtifactDownloadUnconfigured, err)
	}
	secret := []byte(strings.TrimSpace(os.Getenv(constants.ArtifactDownloadSigningKeyEnv)))
	token, err := artifactticket.Sign(secret, artifactticket.Claims{
		ArtifactID: artifact.ArtifactID,
		NodeID:     strings.TrimSpace(nodeID),
		ExpiresAt:  expiresAt.Unix(),
		Generation: generation,
		Purpose:    artifactticket.PurposeDownload,
	})
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrArtifactDownloadUnconfigured, err)
	}
	ticket.Source = artifactticket.SourceTemplateCenter
	ticket.URL = downloadURL
	ticket.Token = token
	ticket.Headers = map[string]string{
		artifactticket.DownloadTokenHeader:  token,
		artifactticket.DownloadNodeIDHeader: strings.TrimSpace(nodeID),
	}
	return ticket, nil
}

func buildArtifactDownloadRefreshToken(artifact *models.RootfsArtifact, nodeID string, now time.Time) (string, error) {
	if artifact == nil || strings.TrimSpace(artifact.ArtifactID) == "" ||
		strings.TrimSpace(nodeID) == "" {
		return "", ErrArtifactDownloadUnauthorized
	}
	generation := artifact.Generation
	if generation <= 0 {
		generation = 1
	}
	token, err := artifactticket.Sign(
		[]byte(strings.TrimSpace(os.Getenv(constants.ArtifactDownloadSigningKeyEnv))),
		artifactticket.Claims{
			ArtifactID: artifact.ArtifactID,
			NodeID:     strings.TrimSpace(nodeID),
			ExpiresAt:  now.Add(artifactRefreshTokenTTL).Unix(),
			Generation: generation,
			Purpose:    artifactticket.PurposeRefresh,
		},
	)
	if err != nil {
		return "", fmt.Errorf("%w: %v", ErrArtifactDownloadUnconfigured, err)
	}
	return token, nil
}

func artifactDirectDownloadOrigin(ctx context.Context, artifact *models.RootfsArtifact) (string, error) {
	if artifact == nil {
		return "", errors.New("artifact is nil")
	}
	var rawURL string
	if artifactUsesS3(artifact) {
		ttl, err := artifactDownloadTTL()
		if err != nil {
			return "", err
		}
		rawURL, err = presignArtifactGetURLWithTTL(ctx, artifact, ttl)
		if err != nil {
			return "", err
		}
	} else {
		baseURL := strings.TrimSpace(artifact.OwnerTCDownloadBase)
		if baseURL == "" {
			baseURL = configuredTemplateCenterDownloadBase()
		}
		var err error
		rawURL, err = templateCenterArtifactURL(baseURL, artifact.ArtifactID)
		if err != nil {
			return "", fmt.Errorf("%w: %v", ErrArtifactDownloadUnconfigured, err)
		}
	}
	origin, err := artifactticket.URLOrigin(rawURL)
	if err != nil {
		return "", fmt.Errorf("resolve artifact direct-download origin: %w", err)
	}
	return origin, nil
}

func configuredTemplateCenterDownloadBase() string {
	if baseURL := strings.TrimSpace(os.Getenv(constants.TemplateCenterDownloadAddrEnv)); baseURL != "" {
		return baseURL
	}
	if cfg := config.GetConfig(); cfg != nil {
		return strings.TrimSpace(cfg.TemplateCenterAddr())
	}
	return ""
}

// ensureArtifactStableLocator lazily upgrades pre-migration rows before they
// are distributed. Short-lived credentials never enter these columns.
func ensureArtifactStableLocator(ctx context.Context, artifact *models.RootfsArtifact) error {
	if artifact == nil || strings.TrimSpace(artifact.ArtifactID) == "" {
		return errors.New("artifact is nil or has no id")
	}
	if store == nil || store.db == nil {
		return ErrTemplateStoreNotInitialized
	}
	if artifact.Status != ArtifactStatusReady {
		return fmt.Errorf("artifact %s is not ready", artifact.ArtifactID)
	}
	if strings.TrimSpace(artifact.StorageRef) == "" && artifactUsesS3(artifact) {
		return fmt.Errorf("artifact %s requires explicit migration of its S3 storage locator", artifact.ArtifactID)
	}

	candidate := *artifact
	updates := map[string]any{}
	if candidate.Generation <= 0 {
		candidate.Generation = 1
		updates["generation"] = candidate.Generation
	}
	if strings.TrimSpace(candidate.OwnerTCDownloadBase) == "" {
		if baseURL := configuredTemplateCenterDownloadBase(); baseURL != "" {
			candidate.OwnerTCDownloadBase = baseURL
			updates["owner_tc_download_base"] = baseURL
		}
	}
	if strings.TrimSpace(candidate.StorageRef) == "" {
		if storageRef := inferredArtifactStorageRef(&candidate); storageRef != "" {
			candidate.StorageRef = storageRef
			updates["storage_ref"] = storageRef
		}
	}

	query := store.db.WithContext(ctx).Table(constants.RootfsArtifactTableName).
		Where("artifact_id = ? AND deleted_at IS NULL AND status = ?", artifact.ArtifactID, artifact.Status)
	if artifact.Generation == 0 {
		query = query.Where("(generation = ? OR generation IS NULL)", artifact.Generation)
	} else {
		query = query.Where("generation = ?", artifact.Generation)
	}
	for _, field := range []struct{ column, value string }{
		{"storage_backend", artifact.StorageBackend},
		{"object_key", artifact.ObjectKey},
		{"storage_ref", artifact.StorageRef},
		{"owner_tc_download_base", artifact.OwnerTCDownloadBase},
		{"artifact_url", artifact.ArtifactURL},
		{"ext4_path", artifact.Ext4Path},
	} {
		if field.value == "" {
			query = query.Where("("+field.column+" = ? OR "+field.column+" IS NULL)", field.value)
		} else {
			query = query.Where(field.column+" = ?", field.value)
		}
	}
	if len(updates) == 0 {
		var count int64
		if err := query.Count(&count).Error; err != nil {
			return fmt.Errorf("check stable locator for artifact %s: %w", artifact.ArtifactID, err)
		}
		if count != 1 {
			return fmt.Errorf("stable locator conflict for artifact %s: READY artifact snapshot changed", artifact.ArtifactID)
		}
		return nil
	}
	result := query.Updates(updates)
	if result.Error != nil {
		return fmt.Errorf("backfill stable locator for artifact %s: %w", artifact.ArtifactID, result.Error)
	}
	if result.RowsAffected == 0 {
		return fmt.Errorf("stable locator conflict for artifact %s: READY artifact snapshot changed", artifact.ArtifactID)
	}
	artifact.Generation = candidate.Generation
	artifact.OwnerTCDownloadBase = candidate.OwnerTCDownloadBase
	artifact.StorageRef = candidate.StorageRef
	return nil
}

func inferredArtifactStorageRef(artifact *models.RootfsArtifact) string {
	if artifact == nil || artifactUsesS3(artifact) {
		return ""
	}
	switch strings.ToLower(strings.TrimSpace(artifact.StorageBackend)) {
	case "fs":
		return (&url.URL{Scheme: "file", Path: filepath.Clean(configenv.ResolveArtifactFSRoot())}).String()
	default:
		path := strings.TrimSpace(artifact.Ext4Path)
		if path == "" || !filepath.IsAbs(path) {
			return ""
		}
		return (&url.URL{Scheme: "file", Path: filepath.Clean(path)}).String()
	}
}

func artifactDownloadTTL() (time.Duration, error) {
	envName := constants.ArtifactDownloadTokenTTLEnv
	raw := strings.TrimSpace(os.Getenv(envName))
	if raw == "" {
		envName = constants.ArtifactDownloadTTLEnv
		raw = strings.TrimSpace(os.Getenv(envName))
	}
	if raw == "" {
		return defaultArtifactDownloadTTL, nil
	}
	ttl, err := time.ParseDuration(raw)
	if err != nil {
		return 0, fmt.Errorf("parse %s=%q: %w", envName, raw, err)
	}
	if ttl < minArtifactDownloadTTL || ttl > maxArtifactDownloadTTL {
		return 0, fmt.Errorf("%s must be between %s and %s", envName, minArtifactDownloadTTL, maxArtifactDownloadTTL)
	}
	return ttl, nil
}

func artifactUsesS3(artifact *models.RootfsArtifact) bool {
	if artifact == nil {
		return false
	}
	switch strings.ToLower(strings.TrimSpace(artifact.StorageBackend)) {
	case "s3":
		return true
	case "fs":
		return false
	}
	// Legacy rows did not record storage_backend. A non-empty artifact_url was
	// only produced after a successful object-store upload.
	return strings.TrimSpace(artifact.ArtifactURL) != ""
}

func templateCenterArtifactURL(baseURL, artifactID string) (string, error) {
	baseURL = strings.TrimSpace(baseURL)
	if baseURL == "" {
		return "", errors.New("TemplateCenter download base URL is empty")
	}
	if !strings.Contains(baseURL, "://") {
		baseURL = "http://" + baseURL
	}
	u, err := url.Parse(baseURL)
	if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") || u.User != nil {
		return "", fmt.Errorf("invalid TemplateCenter download base URL %q", baseURL)
	}
	u.RawQuery = ""
	u.Fragment = ""
	return url.JoinPath(u.String(), "internal", "artifacts", artifactID, "ext4")
}

func artifactDownloadTicketURL(baseURL, artifactID string) (string, error) {
	baseURL = strings.TrimSpace(NormalizeBaseURL(baseURL))
	if baseURL == "" {
		return "", errors.New("CubeMaster download base URL is empty")
	}
	u, err := url.Parse(baseURL)
	if err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") {
		return "", fmt.Errorf("invalid CubeMaster download base URL %q", baseURL)
	}
	u.RawQuery = ""
	u.Fragment = ""
	return url.JoinPath(u.String(), "internal", "template", "artifacts", artifactID, "download-ticket")
}
