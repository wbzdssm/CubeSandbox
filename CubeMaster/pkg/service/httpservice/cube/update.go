// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package cube

import (
	"net/http"

<<<<<<< HEAD
	"github.com/gin-gonic/gin"
=======
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
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

func handleUpdateAction(c *gin.Context) {
	rt := CubeLog.GetTraceInfo(c.Request.Context())
=======
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox/types"
	"github.com/tencentcloud/CubeSandbox/cubelog"
)

func handleUpdateAction(w http.ResponseWriter, r *http.Request, rt *CubeLog.RequestTrace) interface{} {
	_ = w
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	rt.RetCode = -1
	rsp := &types.Res{
		Ret: &types.Ret{
			RetCode: -1,
			RetMsg:  http.StatusText(http.StatusNotFound),
		},
	}
	req := &types.UpdateRequest{}
<<<<<<< HEAD
	if err := utils.DecodeHttpBody(c.Request.Body, req); err != nil {
		rsp.Ret.RetCode = int(errorcode.ErrorCode_MasterParamsError)
		rsp.Ret.RetMsg = "请求体解析失败"
		common.WriteAPI(c, rsp)
		return
=======
	if err := utils.DecodeHttpBody(r.Body, req); err != nil {
		rsp.Ret.RetCode = int(errorcode.ErrorCode_MasterParamsError)
		rsp.Ret.RetMsg = "请求体解析失败"
		return rsp
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	}
	if req.RequestID == "" {
		rsp.Ret.RetCode = int(errorcode.ErrorCode_MasterParamsError)
		rsp.Ret.RetMsg = "requestID is empty"
		rt.RetCode = int64(errorcode.ErrorCode_MasterParamsError)
<<<<<<< HEAD
		common.WriteAPI(c, rsp)
		return
=======
		return rsp
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	}
	if req.InstanceType == "" {
		req.InstanceType = cubebox.InstanceType_cubebox.String()
	}
	rt.RequestID = req.RequestID
	rt.InstanceType = req.InstanceType
<<<<<<< HEAD
	ctx := log.WithLogger(c.Request.Context(), log.G(c.Request.Context()).WithFields(map[string]any{
=======
	ctx := log.WithLogger(r.Context(), log.G(r.Context()).WithFields(map[string]any{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
		"RequestId":    req.RequestID,
		"InstanceType": req.InstanceType,
	}))
	rsp = sandbox.Update(CubeLog.WithRequestTrace(ctx, rt), req)
<<<<<<< HEAD
	common.WriteAPI(c, rsp)
=======
	return rsp

>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
}
