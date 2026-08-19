// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

// Package build implements the core template building logic for CubeTemplateCenter.
// It ONLY does the build work (pull image, mkfs ext4) and reports status back
// to CubeMaster via HTTP callback.
package build

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sync"

	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/log"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox/types"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/templatecenter"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/templatecenter/image"
)

// artifactBuildLocks serializes concurrent builds of the SAME artifact within
// this process. Concurrent same-spec creates share one fingerprint, hence one
// artifactID and one ext4 output path; without this lock the native rootfs
// export races itself (lchown/link/utimes ENOENT) and one build's cleanup
// deletes files another build is still writing.
//
// TODO(multi-replica): once TC runs more than one replica, add cross-process
// mutual exclusion (e.g. the DB session lock in pkg/lock) on top of this.
var artifactBuildLocks = newKeyedMutex()

// keyedMutex is a per-key mutex set with automatic cleanup of idle entries.
type keyedMutex struct {
	mu    sync.Mutex
	items map[string]*keyedMutexItem
}

type keyedMutexItem struct {
	mu   *sync.Mutex
	refs int
}

func newKeyedMutex() *keyedMutex {
	return &keyedMutex{items: make(map[string]*keyedMutexItem)}
}

// Lock acquires the mutex for key and returns the unlock function.
func (k *keyedMutex) Lock(key string) func() {
	k.mu.Lock()
	it, ok := k.items[key]
	if !ok {
		it = &keyedMutexItem{mu: &sync.Mutex{}}
		k.items[key] = it
	}
	it.refs++
	k.mu.Unlock()

	it.mu.Lock()

	return func() {
		it.mu.Unlock()
		k.mu.Lock()
		it.refs--
		if it.refs == 0 {
			delete(k.items, key)
		}
		k.mu.Unlock()
	}
}

// Build is the TC-only entry point for template building.
//
// What TC does:
//   - Pull image (image.PrepareSource)
//   - Compute fingerprint + artifact ID (same helpers as local mode)
//   - Build ext4 (image.BuildExt4), baking envd + CubeEgress CA into rootfs
//   - Report status back to CubeMaster via HTTP callback
//
// What TC does NOT do (CubeMaster's job):
//   - Parameter validation (instance_type / image_ref legality)
//   - Write image_jobs (status updates arrive via the callback handler)
//   - Write rootfs_artifacts / template_definitions (the BUILT report carries
//     the artifact metadata in result_json; CubeMaster finalizes the record
//     when it resumes the job for distribution)
//   - Distribute artifact to Cubelet nodes
func Build(ctx context.Context, jobID string, req *types.CreateTemplateFromImageReq, downloadBaseURL string, envdSHA string, envdData []byte) error {
	logger := log.G(ctx).WithFields(map[string]any{
		"job_id":      jobID,
		"template_id": req.TemplateID,
		"image":       req.SourceImageRef,
	})

	reporter := NewReporter()
	defer reporter.Close()

	reportPhase := func(phase string, progress int) {
		if err := reporter.Report(ctx, jobID, map[string]any{
			"status":   templatecenter.JobStatusRunning,
			"phase":    phase,
			"progress": progress,
		}); err != nil {
			logger.Warnf("report %s phase fail: %v", phase, err)
		}
	}
	reportFailed := func(phase, errMsg string) {
		if err := reporter.Report(ctx, jobID, map[string]any{
			"status":        templatecenter.JobStatusFailed,
			"phase":         phase,
			"progress":      100,
			"error_message": errMsg,
		}); err != nil {
			logger.Warnf("report FAILED status fail: %v", err)
		}
	}

	// Step 1: Pull image
	reportPhase(templatecenter.JobPhasePulling, 5)
	source, err := image.PrepareSource(ctx, image.SourceSpec{
		ImageRef:         req.SourceImageRef,
		RegistryUsername: req.RegistryUsername,
		RegistryPassword: req.RegistryPassword,
		DownloadBaseURL:  downloadBaseURL,
	})
	if err != nil {
		errMsg := fmt.Sprintf("pull image fail: %v", err)
		logger.Errorf(errMsg)
		reportFailed(templatecenter.JobPhasePulling, errMsg)
		return fmt.Errorf("pull image: %w", err)
	}
	if source.Cleanup != nil {
		defer source.Cleanup(context.Background())
	}

	// Step 2: Resolve envd payload (same validation as local mode)
	reportPhase(templatecenter.JobPhaseUnpacking, 20)
	var envdPayload *templatecenter.EnvdInjectionPayload
	if len(envdData) > 0 {
		envdPayload, err = templatecenter.NewEnvdInjectionPayloadFromBytes(envdData)
		if err != nil {
			errMsg := fmt.Sprintf("validate envd payload fail: %v", err)
			logger.Errorf(errMsg)
			reportFailed(templatecenter.JobPhaseUnpacking, errMsg)
			return fmt.Errorf("validate envd payload: %w", err)
		}
	}

	// Step 3: Resolve CubeEgress CA (nil WithCubeCA defaults to true, same as
	// resolveWithCubeCA in local mode)
	withCubeCA := req.WithCubeCA == nil || *req.WithCubeCA
	caPEM, caFingerprint, err := templatecenter.LoadCubeEgressCA(ctx, withCubeCA)
	if err != nil {
		errMsg := fmt.Sprintf("load cube egress CA fail: %v", err)
		logger.Errorf(errMsg)
		reportFailed(templatecenter.JobPhaseUnpacking, errMsg)
		return fmt.Errorf("load cube egress CA: %w", err)
	}

	// Step 4: Fingerprint + artifact ID (identical to local mode so artifact
	// dedup stays compatible across build modes)
	fingerprint := templatecenter.BuildTemplateSpecFingerprintWithEnvdSHA(req, source.Digest, caFingerprint, envdSHA)
	artifactID := templatecenter.BuildArtifactID(fingerprint)
	if err := reporter.Report(ctx, jobID, map[string]any{
		"artifact_id":               artifactID,
		"template_spec_fingerprint": fingerprint,
		"source_image_digest":       source.Digest,
	}); err != nil {
		logger.Warnf("report fingerprint fail: %v", err)
	}

	// Step 5: Serialize same-artifact builds, then reuse a finished artifact
	// if a sibling job already produced it while we were waiting.
	unlock := artifactBuildLocks.Lock(artifactID)
	defer unlock()

	reportBuilt := func(result *image.BuildResult) error {
		return reporter.Report(ctx, jobID, map[string]any{
			"status":                    templatecenter.JobStatusBuilt,
			"phase":                     templatecenter.JobPhaseReady,
			"progress":                  100,
			"artifact_id":               artifactID,
			"artifact_status":           templatecenter.ArtifactStatusReady,
			"template_spec_fingerprint": fingerprint,
			"source_image_digest":       source.Digest,
			"ext4_path":                 result.Ext4Path,
			"ext4_sha256":               result.SHA256,
			"ext4_size_bytes":           result.SizeBytes,
		})
	}

	if existing, ok := reuseExistingArtifact(ctx, artifactID); ok {
		logger.Infof("artifact already built by a sibling job, reusing: artifact_id=%s path=%s", artifactID, existing.Ext4Path)
		if err := reportBuilt(existing); err != nil {
			logger.Errorf("report BUILT status fail: %v", err)
			return fmt.Errorf("report BUILT status: %w", err)
		}
		return nil
	}

	// Step 6: Build ext4 (export rootfs, bake envd + CA, mkfs)
	reportPhase(templatecenter.JobPhaseBuildingExt4, 40)
	opts := image.BuildOptions{ArtifactID: artifactID}
	opts.PostRootfsExport = func(ctx context.Context, rootfsDir string) error {
		if _, err := templatecenter.InjectEnvdPayloadIntoRootfs(ctx, rootfsDir, envdPayload); err != nil {
			return err
		}
		if envdPayload != nil {
			envdPayload.ReleaseData()
		}
		_, err := templatecenter.ApplyCubeEgressCAToRootfs(ctx, rootfsDir, caPEM, caFingerprint)
		return err
	}
	result, err := image.BuildExt4(ctx, source, opts)
	if err != nil {
		errMsg := fmt.Sprintf("build ext4 fail: %v", err)
		logger.Errorf(errMsg)
		reportFailed(templatecenter.JobPhaseBuildingExt4, errMsg)
		return fmt.Errorf("build ext4: %w", err)
	}

	// Step 7: Report BUILT. CubeMaster persists the payload into result_json
	// and (TODO) resumes the job: finalize rootfs_artifacts, distribute to
	// Cubelet nodes, register template_definitions.
	if err := reportBuilt(&result); err != nil {
		logger.Errorf("report BUILT status fail: %v", err)
		return fmt.Errorf("report BUILT status: %w", err)
	}

	logger.Infof("template build completed: artifact_id=%s sha256=%s size=%d",
		artifactID, result.SHA256, result.SizeBytes)
	return nil
}

// reuseExistingArtifact returns the already-built ext4 for artifactID when a
// previous (or sibling) build left a finished image in the artifact store.
// Callers must hold the per-artifact lock so the file cannot appear
// half-written.
func reuseExistingArtifact(ctx context.Context, artifactID string) (*image.BuildResult, bool) {
	storeDir, err := image.ResolveArtifactStoreDir(ctx, artifactID)
	if err != nil {
		return nil, false
	}
	ext4Path := filepath.Join(storeDir, artifactID+".ext4")
	st, err := os.Stat(ext4Path)
	if err != nil || st.Size() == 0 {
		return nil, false
	}
	shaValue, err := fileSHA256(ext4Path)
	if err != nil {
		log.G(ctx).Warnf("reuse check: sha256 %s failed, rebuilding: %v", ext4Path, err)
		return nil, false
	}
	return &image.BuildResult{Ext4Path: ext4Path, SHA256: shaValue, SizeBytes: st.Size()}, true
}

// fileSHA256 streams the file through sha256 (same algorithm as the build path).
func fileSHA256(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}
