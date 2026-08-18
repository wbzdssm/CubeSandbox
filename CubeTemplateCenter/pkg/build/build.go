// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

// Package build implements the core template building logic for CubeTemplateCenter.
// It ONLY does the build work (pull image, mkfs ext4) and reports status back
// to CubeMaster via HTTP callback.
package build

import (
	"context"
	"fmt"

	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/log"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox/types"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/templatecenter"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/templatecenter/image"
)

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

	// Step 5: Build ext4 (export rootfs, bake envd + CA, mkfs)
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

	// Step 6: Report BUILT. CubeMaster persists the payload into result_json
	// and (TODO) resumes the job: finalize rootfs_artifacts, distribute to
	// Cubelet nodes, register template_definitions.
	if err := reporter.Report(ctx, jobID, map[string]any{
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
	}); err != nil {
		logger.Errorf("report BUILT status fail: %v", err)
		return fmt.Errorf("report BUILT status: %w", err)
	}

	logger.Infof("template build completed: artifact_id=%s sha256=%s size=%d",
		artifactID, result.SHA256, result.SizeBytes)
	return nil
}
