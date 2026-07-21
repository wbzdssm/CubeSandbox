// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package cube

import (
	"errors"
	"io"
<<<<<<< HEAD
	"strconv"

	"github.com/gin-gonic/gin"
=======
	"net/http"
	"strconv"

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

func handleInfoAction(c *gin.Context) {
	rt := CubeLog.GetTraceInfo(c.Request.Context())
	req := &types.GetCubeSandboxReq{}

	err := utils.DecodeHttpBody(c.Request.Body, req)
	if err != nil {
		if errors.Is(err, io.EOF) {
			req.RequestID = c.Query("requestID")
			req.HostID = c.Query("host_id")
			req.SandboxID = c.Query("sandbox_id")
			req.InstanceType = c.Query("instance_type")
			if containerPort := c.Query("container_port"); containerPort != "" {
=======
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox/types"
	"github.com/tencentcloud/CubeSandbox/cubelog"
)

func handleInfoAction(w http.ResponseWriter, r *http.Request, rt *CubeLog.RequestTrace) interface{} {
	req := &types.GetCubeSandboxReq{}

	err := utils.DecodeHttpBody(r.Body, req)
	if err != nil {
		if errors.Is(err, io.EOF) {
			querys := r.URL.Query()
			req.RequestID = querys.Get("requestID")
			req.HostID = querys.Get("host_id")
			req.SandboxID = querys.Get("sandbox_id")
			req.InstanceType = querys.Get("instance_type")
			if containerPort := querys.Get("container_port"); containerPort != "" {
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
				port, _ := strconv.ParseInt(containerPort, 10, 32)
				req.ContainerPort = int32(port)
			}
		} else {
			rt.RetCode = int64(errorcode.ErrorCode_MasterParamsError)
<<<<<<< HEAD
			common.WriteAPI(c, &types.Res{
=======
			return &types.Res{
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
	rsp := sandbox.SandboxInfo(ctx, req)
	rt.RetCode = int64(rsp.Ret.RetCode)
<<<<<<< HEAD
	common.WriteAPI(c, rsp)
=======
	return rsp
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
}
