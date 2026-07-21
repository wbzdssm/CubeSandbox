// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package cube

import (
	"errors"
	"io"
<<<<<<< HEAD
	"strconv"
	"strings"

	"github.com/gin-gonic/gin"
=======
	"net/http"
	"strconv"
	"strings"

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

func handleListAction(c *gin.Context) {
	rt := CubeLog.GetTraceInfo(c.Request.Context())
=======
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox/types"
	"github.com/tencentcloud/CubeSandbox/cubelog"
)

func handleListAction(w http.ResponseWriter, r *http.Request, rt *CubeLog.RequestTrace) interface{} {
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	rsp := &types.ListCubeSandboxRes{
		Ret: &types.Ret{
			RetCode: int(errorcode.ErrorCode_Success),
			RetMsg:  errorcode.ErrorCode_Success.String(),
		},
	}
	defer func() {
		rt.RetCode = int64(rsp.Ret.RetCode)
	}()
	req := &types.ListCubeSandboxReq{}

<<<<<<< HEAD
	err := utils.DecodeHttpBody(c.Request.Body, req)
	if err != nil {
		if errors.Is(err, io.EOF) {
			req.RequestID = c.Query("requestID")
			req.HostID = c.Query("host_id")
			req.InstanceType = c.Query("instance_type")
			idx, err := strconv.ParseInt(c.Query("start_idx"), 10, 64)
			if err == nil {
				req.StartIdx = int(idx)
			}
			num, err := strconv.ParseInt(c.Query("size"), 10, 64)
=======
	err := utils.DecodeHttpBody(r.Body, req)
	if err != nil {
		if errors.Is(err, io.EOF) {
			querys := r.URL.Query()
			req.RequestID = querys.Get("requestID")
			req.HostID = querys.Get("host_id")
			req.InstanceType = querys.Get("instance_type")
			idx, err := strconv.ParseInt(querys.Get("start_idx"), 10, 64)
			if err == nil {
				req.StartIdx = int(idx)
			}
			num, err := strconv.ParseInt(querys.Get("size"), 10, 64)
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
			if err == nil {
				req.Size = int(num)
			}

			filters := make(map[string]string)
<<<<<<< HEAD
			filterParams := c.Query("filter.label_selector")
=======
			filterParams := querys.Get("filter.label_selector")
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
			for _, labels := range strings.Split(filterParams, ",") {
				if len(labels) > 0 {
					kv := strings.Split(labels, "=")
					if len(kv) >= 2 {
						filters[kv[0]] = kv[1]
					}
				}
			}
			if len(filters) > 0 {
				req.Filter = &types.CubeSandboxFilter{
					LabelSelector: filters,
				}
			}
		} else {
			rsp.Ret.RetCode = int(errorcode.ErrorCode_MasterParamsError)
			rsp.Ret.RetMsg = err.Error()
<<<<<<< HEAD
			common.WriteListAPI(c, rsp)
			return
=======
			return rsp
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
	rsp = sandbox.ListSandbox(ctx, req)
<<<<<<< HEAD
	common.WriteListAPI(c, rsp)
=======
	return rsp
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
}
