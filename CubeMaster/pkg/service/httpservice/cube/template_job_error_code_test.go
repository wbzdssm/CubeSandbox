// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package cube

import (
	"errors"
	"fmt"
	"testing"

	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/errorcode"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/templatecenter"
	"gorm.io/gorm"
)

// TestTemplateImageJobErrorCode pins the ret code every handler returns when a
// build-job lookup fails.
//
// Asking for a job that does not exist used to answer MasterInternalError (500):
// GetTemplateImageJobInfo leaked gorm.ErrRecordNotFound, and the handlers
// classified anything they did not recognise as a server fault. So a client
// probing for a missing job could not tell "it is not there" from "CubeMaster
// is broken".
func TestTemplateImageJobErrorCode(t *testing.T) {
	tests := []struct {
		name string
		err  error
		want errorcode.ErrorCode
	}{
		{
			name: "nil-is-success",
			err:  nil,
			want: errorcode.ErrorCode_Success,
		},
		{
			name: "absent-job-is-not-found",
			err:  templatecenter.ErrTemplateImageJobNotFound,
			want: errorcode.ErrorCode_NotFound,
		},
		{
			// The repository wraps the sentinel with the job id for the log, so
			// the classification must survive wrapping.
			name: "wrapped-absent-job-is-not-found",
			err:  fmt.Errorf("%w: job_id=job-abc", templatecenter.ErrTemplateImageJobNotFound),
			want: errorcode.ErrorCode_NotFound,
		},
		{
			name: "doubly-wrapped-absent-job-is-not-found",
			err:  fmt.Errorf("lookup redo job: %w", fmt.Errorf("%w: job_id=x", templatecenter.ErrTemplateImageJobNotFound)),
			want: errorcode.ErrorCode_NotFound,
		},
		{
			name: "store-not-initialized-is-db-error",
			err:  templatecenter.ErrTemplateStoreNotInitialized,
			want: errorcode.ErrorCode_DBError,
		},
		{
			name: "wrapped-store-not-initialized-is-db-error",
			err:  fmt.Errorf("get job: %w", templatecenter.ErrTemplateStoreNotInitialized),
			want: errorcode.ErrorCode_DBError,
		},
		{
			// A raw driver error must NOT be silently reinterpreted as a client
			// error. It means the translation at the repository boundary was
			// bypassed, which is a server-side bug and must stay a 5xx.
			name: "raw-gorm-not-found-stays-internal",
			err:  gorm.ErrRecordNotFound,
			want: errorcode.ErrorCode_MasterInternalError,
		},
		{
			name: "unknown-error-stays-internal",
			err:  errors.New("connection reset by peer"),
			want: errorcode.ErrorCode_MasterInternalError,
		},
		{
			// Guessing a client-side code from an error string would turn real
			// server faults into 404s.
			name: "error-mentioning-not-found-stays-internal",
			err:  errors.New("upstream said record not found"),
			want: errorcode.ErrorCode_MasterInternalError,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := templateImageJobErrorCode(tc.err); got != int(tc.want) {
				t.Fatalf("templateImageJobErrorCode(%v) = %d, want %d (%v)",
					tc.err, got, int(tc.want), tc.want)
			}
		})
	}
}

// TestTemplateImageJobErrorCodeIsDistinctPerClass guards against a refactor
// collapsing the three outcomes into one code, which would make the endpoint
// undiagnosable again.
func TestTemplateImageJobErrorCodeIsDistinctPerClass(t *testing.T) {
	notFound := templateImageJobErrorCode(templatecenter.ErrTemplateImageJobNotFound)
	dbError := templateImageJobErrorCode(templatecenter.ErrTemplateStoreNotInitialized)
	internal := templateImageJobErrorCode(errors.New("boom"))

	if notFound == internal {
		t.Fatalf("an absent job (%d) must not share a code with an internal error", notFound)
	}
	if dbError == internal {
		t.Fatalf("an uninitialized store (%d) must not share a code with an internal error", dbError)
	}
	if notFound == dbError {
		t.Fatalf("an absent job (%d) must not share a code with a DB error", notFound)
	}
}
