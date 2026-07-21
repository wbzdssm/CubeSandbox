// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package cube

import (
<<<<<<< HEAD
	"github.com/gin-gonic/gin"
=======
	"net/http"

>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	"github.com/google/uuid"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/api/services/cubebox/v1"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/log"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/utils"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/errorcode"
<<<<<<< HEAD
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/httpservice/common"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox/types"
	CubeLog "github.com/tencentcloud/CubeSandbox/cubelog"
)

func handleSandboxTimeoutAction(c *gin.Context) {
	rt := CubeLog.GetTraceInfo(c.Request.Context())

	req := &types.SetTimeoutRequest{}
	if err := utils.DecodeHttpBody(c.Request.Body, req); err != nil {
		rt.RetCode = int64(errorcode.ErrorCode_MasterParamsError)
		common.WriteAPI(c, &types.SetTimeoutRes{
=======
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox/types"
	"github.com/tencentcloud/CubeSandbox/cubelog"
)

func handleSandboxTimeoutAction(w http.ResponseWriter, r *http.Request, rt *CubeLog.RequestTrace) interface{} {
	_ = w
	if r.Method != http.MethodPost {
		return &types.SetTimeoutRes{
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_MasterParamsError),
				RetMsg:  http.StatusText(http.StatusMethodNotAllowed),
			},
		}
	}

	req := &types.SetTimeoutRequest{}
	if err := utils.DecodeHttpBody(r.Body, req); err != nil {
		rt.RetCode = int64(errorcode.ErrorCode_MasterParamsError)
		return &types.SetTimeoutRes{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_MasterParamsError),
				RetMsg:  err.Error(),
			},
<<<<<<< HEAD
		})
		return
=======
		}
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	}
	if req.RequestID == "" {
		req.RequestID = uuid.New().String()
	}
	if req.InstanceType == "" {
		req.InstanceType = cubebox.InstanceType_cubebox.String()
	}
	rt.RequestID = req.RequestID
	rt.InstanceID = req.SandboxID
	rt.InstanceType = req.InstanceType

<<<<<<< HEAD
	ctx := log.WithLogger(c.Request.Context(), log.G(c.Request.Context()).WithFields(map[string]interface{}{
=======
	ctx := log.WithLogger(r.Context(), log.G(r.Context()).WithFields(map[string]interface{}{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
		"RequestId":    req.RequestID,
		"InstanceId":   req.SandboxID,
		"InstanceType": req.InstanceType,
	}))
	res := sandbox.SetTimeout(CubeLog.WithRequestTrace(ctx, rt), req)
	if res != nil && res.Ret != nil {
		rt.RetCode = int64(res.Ret.RetCode)
	}
<<<<<<< HEAD
	common.WriteAPI(c, res)
}

func handleSandboxRefreshAction(c *gin.Context) {
	rt := CubeLog.GetTraceInfo(c.Request.Context())

	req := &types.RefreshSandboxRequest{}
	if err := utils.DecodeHttpBody(c.Request.Body, req); err != nil {
		rt.RetCode = int64(errorcode.ErrorCode_MasterParamsError)
		common.WriteAPI(c, &types.RefreshSandboxRes{
=======
	return res
}

func handleSandboxRefreshAction(w http.ResponseWriter, r *http.Request, rt *CubeLog.RequestTrace) interface{} {
	_ = w
	if r.Method != http.MethodPost {
		return &types.RefreshSandboxRes{
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_MasterParamsError),
				RetMsg:  http.StatusText(http.StatusMethodNotAllowed),
			},
		}
	}

	req := &types.RefreshSandboxRequest{}
	if err := utils.DecodeHttpBody(r.Body, req); err != nil {
		rt.RetCode = int64(errorcode.ErrorCode_MasterParamsError)
		return &types.RefreshSandboxRes{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_MasterParamsError),
				RetMsg:  err.Error(),
			},
<<<<<<< HEAD
		})
		return
=======
		}
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	}
	if req.RequestID == "" {
		req.RequestID = uuid.New().String()
	}
	if req.InstanceType == "" {
		req.InstanceType = cubebox.InstanceType_cubebox.String()
	}
	rt.RequestID = req.RequestID
	rt.InstanceID = req.SandboxID
	rt.InstanceType = req.InstanceType

<<<<<<< HEAD
	ctx := log.WithLogger(c.Request.Context(), log.G(c.Request.Context()).WithFields(map[string]interface{}{
=======
	ctx := log.WithLogger(r.Context(), log.G(r.Context()).WithFields(map[string]interface{}{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
		"RequestId":    req.RequestID,
		"InstanceId":   req.SandboxID,
		"InstanceType": req.InstanceType,
	}))
	res := sandbox.Refresh(CubeLog.WithRequestTrace(ctx, rt), req)
	if res != nil && res.Ret != nil {
		rt.RetCode = int64(res.Ret.RetCode)
	}
<<<<<<< HEAD
	common.WriteAPI(c, res)
=======
	return res
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
}
