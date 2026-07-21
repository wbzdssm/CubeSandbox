// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package cube

import (
	"context"
	"fmt"
<<<<<<< HEAD
	"net/http"
=======
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/agiledragon/gomonkey/v2"
<<<<<<< HEAD
	"github.com/gin-gonic/gin"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
=======
	"github.com/stretchr/testify/assert"
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/constants"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/node"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/errorcode"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/localcache"
<<<<<<< HEAD
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/httpservice/common"
=======
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox/types"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/templatecenter"
	CubeLog "github.com/tencentcloud/CubeSandbox/cubelog"
)

<<<<<<< HEAD
// invokeCommitHandler drives the gin handler handleSandboxCommitAction with a
// test gin.Context carrying rt, returning the decoded JSON response.
func invokeCommitHandler(t *testing.T, req *http.Request, rt *CubeLog.RequestTrace) commitTemplateResponse {
	t.Helper()
	ctx := CubeLog.WithRequestTrace(context.Background(), rt)
	w := httptest.NewRecorder()
	c, _ := gin.CreateTestContext(w)
	c.Request = req.WithContext(ctx)
	handleSandboxCommitAction(c)
	var got commitTemplateResponse
	require.NoError(t, common.FastestJsoniter.Unmarshal(w.Body.Bytes(), &got))
	return got
}

=======
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
func TestHandleSandboxCommitActionRejectsEmptyRequestID(t *testing.T) {
	body := `{
		"sandbox_id":"sb-1",
		"template_id":"tpl-1",
		"create_request":{
			"instance_type":"cubebox",
			"network_type":"tap",
			"annotations":{
				"cube.master.appsnapshot.template.id":"tpl-1",
				"cube.master.appsnapshot.template.version":"v2"
			}
		}
	}`
	req := httptest.NewRequest("POST", "/cube/sandbox/commit", strings.NewReader(body))
	rt := &CubeLog.RequestTrace{}
<<<<<<< HEAD
	got := invokeCommitHandler(t, req, rt)

	require.NotNil(t, got.Res)
	require.NotNil(t, got.Res.Ret)
=======
	resp := handleSandboxCommitAction(httptest.NewRecorder(), req, rt)

	got, ok := resp.(*commitTemplateResponse)
	if !ok {
		t.Fatalf("unexpected response type %T", resp)
	}
	if got.Res == nil || got.Res.Ret == nil {
		t.Fatalf("missing Ret in response: %#v", got)
	}
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	assert.Equal(t, int(errorcode.ErrorCode_MasterParamsError), got.Res.Ret.RetCode)
	assert.Contains(t, got.Res.Ret.RetMsg, "requestID is required")
	assert.NotEqual(t, "tpl-1", got.TemplateID)
	assert.True(t, strings.HasPrefix(got.TemplateID, "tpl-"), got.TemplateID)
}

func TestHandleSandboxCommitActionRejectsMissingFields(t *testing.T) {
	body := `{"requestID":"req-1"}`
	req := httptest.NewRequest("POST", "/cube/sandbox/commit", strings.NewReader(body))
	rt := &CubeLog.RequestTrace{}
<<<<<<< HEAD
	got := invokeCommitHandler(t, req, rt)

=======
	resp := handleSandboxCommitAction(httptest.NewRecorder(), req, rt)

	got, ok := resp.(*commitTemplateResponse)
	if !ok {
		t.Fatalf("unexpected response type %T", resp)
	}
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	assert.Equal(t, int(errorcode.ErrorCode_MasterParamsError), got.Res.Ret.RetCode)
	assert.Contains(t, got.Res.Ret.RetMsg, "sandbox_id and create_request are required")
}

func TestHandleSandboxCommitActionIgnoresProvidedTemplateID(t *testing.T) {
	patches := gomonkey.NewPatches()
	defer patches.Reset()

	var submittedTemplateID string
	patches.ApplyFunc(localcache.GetSandboxCache, func(sandboxID string) *localcache.SandboxCache {
		return &localcache.SandboxCache{SandboxID: sandboxID, HostIP: "10.0.0.1"}
	})
	patches.ApplyFunc(localcache.GetNodesByIp, func(ip string) (*node.Node, bool) {
		return &node.Node{InsID: "node-1", IP: ip}, true
	})
	patches.ApplyFunc(templatecenter.SubmitTemplateCommit, func(ctx context.Context, sandboxID, nodeID, nodeIP string, req *types.CreateCubeSandboxReq) (*types.TemplateImageJobInfo, error) {
		submittedTemplateID = req.Annotations[constants.CubeAnnotationAppSnapshotTemplateID]
		return &types.TemplateImageJobInfo{
			JobID:      "job-1",
			TemplateID: submittedTemplateID,
		}, nil
	})

	body := `{
		"requestID":"req-1",
		"sandbox_id":"sb-1",
		"template_id":"custom-template",
		"create_request":{
			"instance_type":"cubebox",
			"network_type":"tap",
			"annotations":{
				"cube.master.appsnapshot.template.id":"sb-bad",
				"cube.master.appsnapshot.template.version":"v2"
			}
		}
	}`
	req := httptest.NewRequest("POST", "/cube/sandbox/commit", strings.NewReader(body))
	rt := &CubeLog.RequestTrace{}
<<<<<<< HEAD
	got := invokeCommitHandler(t, req, rt)

=======
	resp := handleSandboxCommitAction(httptest.NewRecorder(), req, rt)

	got, ok := resp.(*commitTemplateResponse)
	if !ok {
		t.Fatalf("unexpected response type %T", resp)
	}
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	assert.Equal(t, int(errorcode.ErrorCode_Success), got.Res.Ret.RetCode)
	assert.Equal(t, submittedTemplateID, got.TemplateID)
	assert.NotEqual(t, "custom-template", got.TemplateID)
	assert.NotEqual(t, "sb-bad", submittedTemplateID)
	assert.True(t, strings.HasPrefix(got.TemplateID, "tpl-"), got.TemplateID)
}

func TestCommitTemplateErrorCode(t *testing.T) {
	tests := []struct {
		name string
		err  error
		want int
	}{
		{
			name: "template id required is params error",
			err:  templatecenter.ErrTemplateIDRequired,
			want: int(errorcode.ErrorCode_MasterParamsError),
		},
		{
			name: "duplicate template is params error",
			err:  templatecenter.ErrDuplicateTemplate,
			want: int(errorcode.ErrorCode_MasterParamsError),
		},
		{
			name: "attempt in progress is params error",
			err:  fmt.Errorf("commit conflict: %w", templatecenter.ErrTemplateAttemptInProgress),
			want: int(errorcode.ErrorCode_MasterParamsError),
		},
		{
			name: "store not initialized is db error",
			err:  templatecenter.ErrTemplateStoreNotInitialized,
			want: int(errorcode.ErrorCode_DBError),
		},
		{
			name: "unknown error is internal error",
			err:  fmt.Errorf("unexpected"),
			want: int(errorcode.ErrorCode_MasterInternalError),
		},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			assert.Equal(t, tc.want, commitTemplateErrorCode(tc.err))
		})
	}
}
