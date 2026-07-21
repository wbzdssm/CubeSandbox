// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package cube

import (
<<<<<<< HEAD
	"github.com/gin-gonic/gin"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/httpservice/common"
	CubeLog "github.com/tencentcloud/CubeSandbox/cubelog"
)

func createSandboxGinHandler(c *gin.Context) {
	rt := CubeLog.GetTraceInfo(c.Request.Context())
	common.WriteAPI(c, createSandbox(c.Request, rt))
}

func deleteSandboxGinHandler(c *gin.Context) {
	rt := CubeLog.GetTraceInfo(c.Request.Context())
	common.WriteAPI(c, deleteSandbox(c.Request, rt))
=======
	"net/http"

	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/errorcode"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox/types"
	"github.com/tencentcloud/CubeSandbox/cubelog"
)

func handleSandboxAction(w http.ResponseWriter, r *http.Request, rt *CubeLog.RequestTrace) interface{} {
	switch r.Method {
	case http.MethodPost:
		return createSandbox(w, r, rt)
	case http.MethodDelete:
		return deleteSandbox(w, r, rt)
	default:
		return &types.Res{
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_MasterParamsError),
				RetMsg:  http.StatusText(http.StatusMethodNotAllowed),
			},
		}
	}
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
}
