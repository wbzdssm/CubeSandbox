// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package templatecenter

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/stretchr/testify/require"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/constants"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/db/models"
	sandboxtypes "github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox/types"
	"gorm.io/gorm"
)

func TestCountArtifactReferencesRejectsLikeWildcards(t *testing.T) {
	for _, artifactID := range []string{"rfs-bad%id", "rfs-bad_id"} {
		_, err := countArtifactReferencesTx(context.Background(), nil, artifactID, "")
		if err == nil {
			t.Fatalf("expected wildcard artifact id %q to be rejected", artifactID)
		}
		if !strings.Contains(err.Error(), "SQL wildcard") {
			t.Fatalf("unexpected error for %q: %v", artifactID, err)
		}
	}
}

func TestCleanupArtifactKeepsBuiltJobReference(t *testing.T) {
	db := newMigrateSharedStoreTestDB(t)
	require.NoError(t, db.AutoMigrate(
		&models.TemplateReplica{},
		&models.TemplateImageJob{},
		&models.SnapshotRecord{},
		&models.ArtifactNodePlacement{},
	))
	const artifactID = "rfs-built-owner"
	require.NoError(t, db.Create(&models.RootfsArtifact{
		ArtifactID:              artifactID,
		TemplateSpecFingerprint: artifactID,
		Status:                  ArtifactStatusReady,
	}).Error)
	require.NoError(t, db.Create(&models.TemplateImageJob{
		JobID:      "job-built-owner",
		TemplateID: "tpl-next-owner",
		ArtifactID: artifactID,
		Status:     JobStatusBuilt,
	}).Error)
	require.NoError(t, db.Create(&models.ArtifactNodePlacement{
		ArtifactID: artifactID,
		NodeID:     "node-a",
		NodeIP:     "10.0.0.1",
	}).Error)

	oldNotify := requestTemplateCenterArtifactDelete
	notified := 0
	requestTemplateCenterArtifactDelete = func(context.Context, string) error {
		notified++
		return nil
	}
	t.Cleanup(func() { requestTemplateCenterArtifactDelete = oldNotify })

	require.NoError(t, cleanupArtifactFully(context.Background(), artifactID, "cubebox", "tpl-old-owner"))
	var artifact models.RootfsArtifact
	require.NoError(t, db.Where("artifact_id = ?", artifactID).First(&artifact).Error)
	require.Equal(t, ArtifactStatusReady, artifact.Status)
	require.Zero(t, notified)
	var placements int64
	require.NoError(t, db.Model(&models.ArtifactNodePlacement{}).
		Where("artifact_id = ?", artifactID).Count(&placements).Error)
	require.EqualValues(t, 1, placements)
}

func TestCleanupArtifactDropsRetiredPlacement(t *testing.T) {
	db := newMigrateSharedStoreTestDB(t)
	require.NoError(t, db.AutoMigrate(
		&models.TemplateReplica{},
		&models.TemplateImageJob{},
		&models.SnapshotRecord{},
		&models.ArtifactNodePlacement{},
	))
	stubReplicaRegistry(t, true, map[string]string{})
	const artifactID = "rfs-retired-placement"
	require.NoError(t, db.Create(&models.RootfsArtifact{
		ArtifactID:              artifactID,
		TemplateSpecFingerprint: artifactID,
		Status:                  ArtifactStatusReady,
	}).Error)
	require.NoError(t, db.Create(&models.ArtifactNodePlacement{
		ArtifactID: artifactID,
		NodeID:     "node-retired",
		NodeIP:     "10.0.0.9",
	}).Error)

	oldNotify := requestTemplateCenterArtifactDelete
	notified := 0
	requestTemplateCenterArtifactDelete = func(context.Context, string) error {
		notified++
		return nil
	}
	t.Cleanup(func() { requestTemplateCenterArtifactDelete = oldNotify })

	require.NoError(t, cleanupArtifactFully(context.Background(), artifactID, "cubebox", ""))
	var placements int64
	require.NoError(t, db.Model(&models.ArtifactNodePlacement{}).
		Where("artifact_id = ?", artifactID).Count(&placements).Error)
	require.Zero(t, placements)
	require.Equal(t, 1, notified)
}

func TestCleanupArtifactKeepsPlacementBeforeRegistryReady(t *testing.T) {
	db := newMigrateSharedStoreTestDB(t)
	require.NoError(t, db.AutoMigrate(
		&models.TemplateReplica{},
		&models.TemplateImageJob{},
		&models.SnapshotRecord{},
		&models.ArtifactNodePlacement{},
	))
	stubReplicaRegistry(t, false, map[string]string{})
	const artifactID = "rfs-cold-registry-placement"
	require.NoError(t, db.Create(&models.RootfsArtifact{
		ArtifactID:              artifactID,
		TemplateSpecFingerprint: artifactID,
		Status:                  ArtifactStatusReady,
	}).Error)
	require.NoError(t, db.Create(&models.ArtifactNodePlacement{
		ArtifactID: artifactID,
		NodeID:     "node-unclassified",
	}).Error)

	oldNotify := requestTemplateCenterArtifactDelete
	notified := 0
	requestTemplateCenterArtifactDelete = func(context.Context, string) error {
		notified++
		return nil
	}
	t.Cleanup(func() { requestTemplateCenterArtifactDelete = oldNotify })

	require.NoError(t, cleanupArtifactFully(context.Background(), artifactID, "cubebox", ""))
	var placements int64
	require.NoError(t, db.Model(&models.ArtifactNodePlacement{}).
		Where("artifact_id = ?", artifactID).Count(&placements).Error)
	require.EqualValues(t, 1, placements)
	require.Zero(t, notified)
}

func TestRootfsArtifactIDFromCreateRequest(t *testing.T) {
	req := &sandboxtypes.CreateCubeSandboxReq{
		Annotations: map[string]string{
			constants.CubeAnnotationRootfsArtifactID: " rfs-top ",
		},
		Containers: []*sandboxtypes.Container{{
			Image: &sandboxtypes.ImageSpec{
				Annotations: map[string]string{
					constants.CubeAnnotationRootfsArtifactID: "rfs-image",
				},
			},
		}},
	}
	if got := rootfsArtifactIDFromCreateRequest(req); got != "rfs-top" {
		t.Fatalf("expected top-level artifact id, got %q", got)
	}

	req.Annotations = nil
	if got := rootfsArtifactIDFromCreateRequest(req); got != "rfs-image" {
		t.Fatalf("expected image artifact id, got %q", got)
	}

	if got := rootfsArtifactIDFromCreateRequest(nil); got != "" {
		t.Fatalf("nil request should have empty artifact id, got %q", got)
	}
}

func definitionArtifactRequest(artifactID, digest string) *sandboxtypes.CreateCubeSandboxReq {
	return &sandboxtypes.CreateCubeSandboxReq{
		InstanceType: "cubebox",
		Backend:      "s3",
		Annotations: map[string]string{
			constants.CubeAnnotationRootfsArtifactID: artifactID,
		},
		Containers: []*sandboxtypes.Container{{
			Image: &sandboxtypes.ImageSpec{
				Annotations: map[string]string{
					constants.CubeAnnotationRootfsArtifactID:     artifactID,
					constants.CubeAnnotationRootfsArtifactSHA256: digest,
				},
			},
		}},
	}
}

func TestEnsureTemplateDefinitionReplacesRebuiltArtifact(t *testing.T) {
	db := newMigrateSharedStoreTestDB(t)
	require.NoError(t, db.AutoMigrate(&models.TemplateReplica{}, &models.SnapshotRecord{}))
	ctx := context.Background()
	const templateID = "tpl-rebuilt-definition"
	t.Cleanup(func() { templateDefinitionCache.Delete(templateID) })
	oldReq := definitionArtifactRequest("rfs-old-build", "old-digest")
	created, err := ensureTemplateDefinitionWithOptions(ctx, templateID, oldReq, "cubebox", "v1", definitionCreateOptions{
		StorageBackend: "s3",
		DisplayName:    "stable-alias",
	})
	require.NoError(t, err)
	require.True(t, created)
	before, err := GetDefinition(ctx, templateID)
	require.NoError(t, err)
	payload, _, err := applyAliasToRequestJSON(before.RequestJSON, "stable-alias")
	require.NoError(t, err)
	require.NoError(t, updateDefinitionFields(ctx, templateID, map[string]any{
		"request_json": payload,
		"status":       StatusFailed,
		"last_error":   "first build failed to boot",
	}))

	newReq := definitionArtifactRequest("rfs-redo-build", "new-digest")
	created, err = ensureTemplateDefinitionWithOptions(ctx, templateID, newReq, "cubebox", "v2", definitionCreateOptions{StorageBackend: "s3"})
	require.NoError(t, err)
	require.False(t, created)
	def, err := GetDefinition(ctx, templateID)
	require.NoError(t, err)
	require.Equal(t, "rfs-redo-build", def.RootfsArtifactID)
	require.Contains(t, def.RequestJSON, "new-digest")
	require.NotContains(t, def.RequestJSON, "old-digest")
	require.Equal(t, "stable-alias", def.DisplayName)
	require.Equal(t, "stable-alias", aliasFromRequestJSON(def.RequestJSON))
	require.Equal(t, before.CreatedAt, def.CreatedAt)
	require.Equal(t, "s3", def.StorageBackend)
	require.Equal(t, "v2", def.Version)
	require.Equal(t, StatusPending, def.Status)
	require.Empty(t, def.LastError)

	for _, cold := range []bool{false, true} {
		if cold {
			templateDefinitionCache.Delete(templateID)
		}
		req, err := GetTemplateRequest(ctx, templateID)
		require.NoError(t, err)
		require.Equal(t, "rfs-redo-build", rootfsArtifactIDFromCreateRequest(req))
		require.Equal(t, "new-digest", req.Containers[0].Image.Annotations[constants.CubeAnnotationRootfsArtifactSHA256])
	}
}

func TestEnsureTemplateDefinitionSameArtifactPreservesConvergedRequest(t *testing.T) {
	newMigrateSharedStoreTestDB(t)
	ctx := context.Background()
	const templateID = "tpl-reused-definition"
	t.Cleanup(func() { templateDefinitionCache.Delete(templateID) })
	req := definitionArtifactRequest("rfs-reused-build", "digest")
	req.Annotations[constants.CubeAnnotationComponentEnvdVersion] = "0.6.0"
	created, err := ensureTemplateDefinitionWithOptions(ctx, templateID, req, "cubebox", "v1", definitionCreateOptions{StorageBackend: "s3"})
	require.NoError(t, err)
	require.True(t, created)
	require.NoError(t, UpdateDefinitionStatus(ctx, templateID, StatusReady, ""))
	before, err := GetDefinition(ctx, templateID)
	require.NoError(t, err)
	created, err = ensureTemplateDefinitionWithOptions(ctx, templateID, definitionArtifactRequest("rfs-reused-build", "digest"), "cubebox", "v2", definitionCreateOptions{StorageBackend: "xfs"})
	require.NoError(t, err)
	require.False(t, created)
	after, err := GetDefinition(ctx, templateID)
	require.NoError(t, err)
	require.Equal(t, before.RequestJSON, after.RequestJSON)
	require.Equal(t, StatusReady, after.Status)
	require.Equal(t, "v1", after.Version)
	require.Equal(t, "s3", after.StorageBackend)
	cached, hit, err := getCachedTemplateRequest(templateID)
	require.NoError(t, err)
	require.True(t, hit)
	require.Equal(t, "0.6.0", cached.Annotations[constants.CubeAnnotationComponentEnvdVersion])
}

func TestEnsureTemplateDefinitionFailedReplacementKeepsCache(t *testing.T) {
	db := newMigrateSharedStoreTestDB(t)
	require.NoError(t, db.AutoMigrate(&models.TemplateReplica{}))
	ctx := context.Background()
	const templateID = "tpl-failed-definition-update"
	t.Cleanup(func() { templateDefinitionCache.Delete(templateID) })
	_, err := ensureTemplateDefinitionWithOptions(ctx, templateID, definitionArtifactRequest("rfs-old", "old-digest"), "cubebox", "v1", definitionCreateOptions{StorageBackend: "s3"})
	require.NoError(t, err)
	const callback = "test:reject_definition_update"
	require.NoError(t, db.Callback().Update().Before("gorm:update").Register(callback, func(tx *gorm.DB) {
		tx.AddError(errors.New("definition update rejected"))
	}))
	t.Cleanup(func() { db.Callback().Update().Remove(callback) })
	_, err = ensureTemplateDefinitionWithOptions(ctx, templateID, definitionArtifactRequest("rfs-new", "new-digest"), "cubebox", "v2", definitionCreateOptions{StorageBackend: "s3"})
	require.ErrorContains(t, err, "definition update rejected")
	def, err := GetDefinition(ctx, templateID)
	require.NoError(t, err)
	require.Equal(t, "rfs-old", def.RootfsArtifactID)
	cached, hit, err := getCachedTemplateRequest(templateID)
	require.NoError(t, err)
	require.True(t, hit)
	require.Equal(t, "rfs-old", rootfsArtifactIDFromCreateRequest(cached))
}

func TestEnsureTemplateDefinitionRejectsUnsafeArtifactReplacement(t *testing.T) {
	for _, scenario := range []string{"ready-replica", "snapshot", "missing-artifact", "deleting"} {
		t.Run(scenario, func(t *testing.T) {
			db := newMigrateSharedStoreTestDB(t)
			require.NoError(t, db.AutoMigrate(&models.TemplateReplica{}))
			ctx := context.Background()
			templateID := "tpl-unsafe-" + scenario
			t.Cleanup(func() { templateDefinitionCache.Delete(templateID) })
			opts := definitionCreateOptions{StorageBackend: "s3"}
			if scenario == "snapshot" {
				opts.Kind = TemplateKindSnapshot
			}
			_, err := ensureTemplateDefinitionWithOptions(ctx, templateID, definitionArtifactRequest("rfs-old", "old-digest"), "cubebox", "v1", opts)
			require.NoError(t, err)
			if scenario == "ready-replica" {
				require.NoError(t, db.Create(&models.TemplateReplica{
					TemplateID: templateID,
					NodeID:     "node-ready",
					ArtifactID: "rfs-old",
					Status:     ReplicaStatusReady,
				}).Error)
			}
			if scenario == "deleting" {
				require.NoError(t, UpdateDefinitionStatus(ctx, templateID, StatusDeleting, ""))
			}
			newReq := definitionArtifactRequest("rfs-new", "new-digest")
			if scenario == "missing-artifact" {
				newReq = &sandboxtypes.CreateCubeSandboxReq{}
			}
			_, err = ensureTemplateDefinitionWithOptions(ctx, templateID, newReq, "cubebox", "v2", opts)
			require.Error(t, err)
			def, err := GetDefinition(ctx, templateID)
			require.NoError(t, err)
			require.Equal(t, "rfs-old", def.RootfsArtifactID)
			cached, hit, err := getCachedTemplateRequest(templateID)
			require.NoError(t, err)
			require.True(t, hit)
			require.Equal(t, "rfs-old", rootfsArtifactIDFromCreateRequest(cached))
		})
	}
}

func TestEnsureTemplateDefinitionSerializesRebuiltArtifact(t *testing.T) {
	db := newMigrateSharedStoreTestDB(t)
	require.NoError(t, db.AutoMigrate(&models.TemplateReplica{}))
	ctx := context.Background()
	const templateID = "tpl-concurrent-rebuild"
	t.Cleanup(func() { templateDefinitionCache.Delete(templateID) })
	_, err := ensureTemplateDefinitionWithOptions(ctx, templateID, definitionArtifactRequest("rfs-old", "old-digest"), "cubebox", "v1", definitionCreateOptions{StorageBackend: "s3"})
	require.NoError(t, err)
	results := make(chan error, 8)
	for i := 0; i < cap(results); i++ {
		go func() {
			_, err := ensureTemplateDefinitionWithOptions(ctx, templateID, definitionArtifactRequest("rfs-new", "new-digest"), "cubebox", "v2", definitionCreateOptions{StorageBackend: "s3"})
			results <- err
		}()
	}
	for i := 0; i < cap(results); i++ {
		require.NoError(t, <-results)
	}
	def, err := GetDefinition(ctx, templateID)
	require.NoError(t, err)
	require.Equal(t, "rfs-new", def.RootfsArtifactID)
	cached, hit, err := getCachedTemplateRequest(templateID)
	require.NoError(t, err)
	require.True(t, hit)
	require.Equal(t, "rfs-new", rootfsArtifactIDFromCreateRequest(cached))
}

func TestTemplateArtifactRequestCacheDetectsRemoteRebuild(t *testing.T) {
	db := newMigrateSharedStoreTestDB(t)
	require.NoError(t, db.AutoMigrate(&models.SnapshotRecord{}))
	ctx := context.Background()
	const templateID = "tpl-remote-rebuild"
	t.Cleanup(func() { templateDefinitionCache.Delete(templateID) })
	oldReq := definitionArtifactRequest("rfs-old", "old-digest")
	_, err := ensureTemplateDefinitionWithOptions(ctx, templateID, oldReq, "cubebox", "v1", definitionCreateOptions{StorageBackend: "s3"})
	require.NoError(t, err)
	payload, err := json.Marshal(definitionArtifactRequest("rfs-new", "new-digest"))
	require.NoError(t, err)
	require.NoError(t, db.Model(&models.TemplateDefinition{}).Where("template_id = ?", templateID).
		Updates(map[string]any{"rootfs_artifact_id": "rfs-new", "request_json": string(payload), "storage_backend": "s3"}).Error)
	cached, hit, err := getCachedTemplateRequest(templateID)
	require.NoError(t, err)
	require.True(t, hit)
	require.Equal(t, "rfs-old", rootfsArtifactIDFromCreateRequest(cached))
	req, err := GetTemplateRequest(ctx, templateID)
	require.NoError(t, err)
	require.Equal(t, "rfs-new", rootfsArtifactIDFromCreateRequest(req))
	require.Equal(t, "new-digest", req.Containers[0].Image.Annotations[constants.CubeAnnotationRootfsArtifactSHA256])
}

func TestTemplateArtifactRequestCacheUsesStoredBackend(t *testing.T) {
	db := newMigrateSharedStoreTestDB(t)
	require.NoError(t, db.AutoMigrate(&models.SnapshotRecord{}))
	ctx := context.Background()
	const templateID = "tpl-backend-authority"
	t.Cleanup(func() { templateDefinitionCache.Delete(templateID) })
	req := definitionArtifactRequest("rfs-same", "digest")
	_, err := ensureTemplateDefinitionWithOptions(ctx, templateID, req, "cubebox", "v1", definitionCreateOptions{StorageBackend: "s3"})
	require.NoError(t, err)
	require.NoError(t, updateDefinitionFields(ctx, templateID, map[string]any{"storage_backend": "xfs"}))
	_, err = ensureTemplateDefinitionWithOptions(ctx, templateID, req, "cubebox", "v2", definitionCreateOptions{StorageBackend: "s3"})
	require.NoError(t, err)
	for _, cold := range []bool{false, true} {
		if cold {
			templateDefinitionCache.Delete(templateID)
		}
		got, err := GetTemplateRequest(ctx, templateID)
		require.NoError(t, err)
		require.Equal(t, "xfs", got.Backend)
	}
}

func TestTemplateArtifactReadyReplicaRejectsStaleArtifact(t *testing.T) {
	db := newMigrateSharedStoreTestDB(t)
	require.NoError(t, db.AutoMigrate(&models.TemplateReplica{}))
	ctx := context.Background()
	const templateID = "tpl-ready-artifact-guard"
	t.Cleanup(func() { templateDefinitionCache.Delete(templateID) })
	_, err := ensureTemplateDefinitionWithOptions(ctx, templateID, definitionArtifactRequest("rfs-new", "new-digest"), "cubebox", "v1", definitionCreateOptions{StorageBackend: "s3"})
	require.NoError(t, err)
	replica := ReplicaStatus{NodeID: "node-a", Status: ReplicaStatusReady, ArtifactID: "rfs-old"}
	require.ErrorContains(t, UpsertReplica(ctx, templateID, "cubebox", replica), "does not match")
	replica.ArtifactID = "rfs-new"
	require.NoError(t, UpsertReplica(ctx, templateID, "cubebox", replica))
	require.NoError(t, UpdateDefinitionStatus(ctx, templateID, StatusDeleting, ""))
	require.ErrorIs(t, UpsertReplica(ctx, templateID, "cubebox", replica), ErrTemplateNotFound)
}

func TestTemplateArtifactStatusPublishRejectsStaleJob(t *testing.T) {
	db := newMigrateSharedStoreTestDB(t)
	require.NoError(t, db.AutoMigrate(&models.TemplateImageJob{}))
	ctx := context.Background()
	const templateID = "tpl-ready-job-guard"
	t.Cleanup(func() { templateDefinitionCache.Delete(templateID) })
	_, err := ensureTemplateDefinitionWithOptions(ctx, templateID, definitionArtifactRequest("rfs-new", "new-digest"), "cubebox", "v1", definitionCreateOptions{StorageBackend: "s3"})
	require.NoError(t, err)
	require.NoError(t, db.Create(&models.TemplateImageJob{
		JobID:       "job-old-artifact",
		TemplateID:  templateID,
		Operation:   JobOperationRedo,
		ArtifactID:  "rfs-old",
		Status:      JobStatusRunning,
		RequestJSON: "{}",
	}).Error)
	_, _, err = publishTemplateStatusWithAlias(ctx, templateID, "job-old-artifact", StatusReady, "")
	require.ErrorContains(t, err, "does not match job artifact")
	def, err := GetDefinition(ctx, templateID)
	require.NoError(t, err)
	require.Equal(t, StatusPending, def.Status)
}

func TestPlacementToNodePrefersCurrentRegistryAddress(t *testing.T) {
	stubReplicaRegistry(t, true, map[string]string{"node-a": "10.1.0.8"})
	target := placementToNode(&models.ArtifactNodePlacement{
		NodeID: "node-a",
		NodeIP: "10.0.0.8",
	})
	if target == nil || target.ID() != "node-a" || target.HostIP() != "10.1.0.8" {
		t.Fatalf("resolved placement = %+v", target)
	}
}

func TestArtifactPlacementRetirementRequiresStableMissingNodeID(t *testing.T) {
	stubReplicaRegistry(t, true, map[string]string{"node-live": "10.1.0.8"})
	if artifactPlacementOnRetiredNode(&models.ArtifactNodePlacement{NodeID: "node-live"}) {
		t.Fatal("registered node must not be retired")
	}
	if !artifactPlacementOnRetiredNode(&models.ArtifactNodePlacement{NodeID: "node-gone"}) {
		t.Fatal("missing stable node id must be retired")
	}
	if artifactPlacementOnRetiredNode(&models.ArtifactNodePlacement{NodeIP: "10.0.0.9"}) {
		t.Fatal("IP-only placement must not be inferred as retired")
	}
}

// requestTemplateCenterArtifactDelete is the ONLY thing allowed to remove an
// artifact's S3 object/local file/row (see CubeTemplateCenter/pkg/build/
// deleter.go). cleanupArtifactFully reaches it via
// notifyTemplateCenterArtifactDelete exactly once per finalized artifact --
// regression test for the S3-leak bug where CubeMaster used to hard-delete the
// row itself without ever notifying TC.
//
// The DB-bound phases of cleanupArtifactFully (1 and 3) are not unit-testable
// without a live database, so this exercises the production notify step
// directly instead of asserting on the stub itself.
func TestNotifyTemplateCenterArtifactDeleteCallsSeamOnce(t *testing.T) {
	orig := requestTemplateCenterArtifactDelete
	defer func() { requestTemplateCenterArtifactDelete = orig }()

	var calledWith []string
	requestTemplateCenterArtifactDelete = func(ctx context.Context, artifactID string) error {
		calledWith = append(calledWith, artifactID)
		return nil
	}

	notifyTemplateCenterArtifactDelete(context.Background(), "rfs-1")
	if len(calledWith) != 1 || calledWith[0] != "rfs-1" {
		t.Fatalf("calledWith = %v, want exactly [rfs-1]", calledWith)
	}
}

// A notify failure must not propagate: the artifact row stays CLEANUP_PENDING
// and TC's reconciler backstop-sweeps it later, so template deletion proceeds.
func TestNotifyTemplateCenterArtifactDeleteSwallowsErrors(t *testing.T) {
	orig := requestTemplateCenterArtifactDelete
	defer func() { requestTemplateCenterArtifactDelete = orig }()

	requestTemplateCenterArtifactDelete = func(ctx context.Context, artifactID string) error {
		return errors.New("tc unreachable")
	}

	// Must not panic; there is no return value to check by design.
	notifyTemplateCenterArtifactDelete(context.Background(), "rfs-2")
}

// When TC's endpoint is not configured, the seam must fail loudly (not
// silently pretend success) so the caller knows to leave the row
// CLEANUP_PENDING rather than assume cleanup happened.
func TestRequestTemplateCenterArtifactDeleteRequiresEndpoint(t *testing.T) {
	t.Setenv("CUBE_TEMPLATE_CENTER_ADDR", "")
	if err := requestTemplateCenterArtifactDelete(context.Background(), "rfs-2"); err == nil {
		t.Fatal("expected error when template center endpoint is not configured")
	}
}
