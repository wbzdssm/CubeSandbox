// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package cube

import (
	"net/http"

<<<<<<< HEAD
	"github.com/gin-gonic/gin"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/api/services/cubebox/v1"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/log"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/utils"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/httpservice/common"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox/types"
	CubeLog "github.com/tencentcloud/CubeSandbox/cubelog"
)

func handleExecAction(c *gin.Context) {
	rt := CubeLog.GetTraceInfo(c.Request.Context())
=======
	"github.com/tencentcloud/CubeSandbox/CubeMaster/api/services/cubebox/v1"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/log"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/utils"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox/types"
	"github.com/tencentcloud/CubeSandbox/cubelog"
)

func handleExecAction(w http.ResponseWriter, r *http.Request, rt *CubeLog.RequestTrace) interface{} {
	_ = w
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	rt.RetCode = -1
	rsp := &types.Res{
		Ret: &types.Ret{
			RetCode: -1,
			RetMsg:  http.StatusText(http.StatusNotFound),
		},
	}
	req := &types.ExecRequest{}
<<<<<<< HEAD
	if err := utils.DecodeHttpBody(c.Request.Body, req); err != nil {
		rsp.Ret.RetMsg = err.Error()
		common.WriteAPI(c, rsp)
		return
=======
	if err := utils.DecodeHttpBody(r.Body, req); err != nil {
		rsp.Ret.RetMsg = err.Error()
		return rsp
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	}
	rt.RequestID = req.RequestID
	if req.InstanceType == "" {
		req.InstanceType = cubebox.InstanceType_cubebox.String()
	}
	rt.InstanceType = req.InstanceType
<<<<<<< HEAD
	ctx := log.WithLogger(c.Request.Context(), log.G(c.Request.Context()).WithFields(map[string]any{
=======
	ctx := log.WithLogger(r.Context(), log.G(r.Context()).WithFields(map[string]any{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
		"RequestId":    req.RequestID,
		"InstanceType": req.InstanceType,
	}))
	rsp = sandbox.Exec(CubeLog.WithRequestTrace(ctx, rt), req)
<<<<<<< HEAD
	common.WriteAPI(c, rsp)
=======
	return rsp

>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
}
