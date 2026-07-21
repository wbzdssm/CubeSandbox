// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package cube

import (
<<<<<<< HEAD
	"github.com/gin-gonic/gin"
=======
	"net/http"

>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	"github.com/tencentcloud/CubeSandbox/CubeMaster/api/services/cubebox/v1"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/config"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/log"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/node"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/utils"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/errorcode"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/localcache"
<<<<<<< HEAD
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/httpservice/common"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox/types"
	CubeLog "github.com/tencentcloud/CubeSandbox/cubelog"
)

func handleListInventoryAction(c *gin.Context) {
	rt := CubeLog.GetTraceInfo(c.Request.Context())
=======
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox/types"
	"github.com/tencentcloud/CubeSandbox/cubelog"
)

func handleListInventoryAction(w http.ResponseWriter, r *http.Request, rt *CubeLog.RequestTrace) interface{} {
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	rsp := &types.ListInventoryRes{
		Ret: &types.Ret{
			RetCode: int(errorcode.ErrorCode_Success),
			RetMsg:  errorcode.ErrorCode_Success.String(),
		},
	}
	defer func() {
		rt.RetCode = int64(rsp.Ret.RetCode)
	}()
	req := &types.ListInventoryReq{}
<<<<<<< HEAD
	if err := utils.DecodeHttpBody(c.Request.Body, req); err != nil {
		rsp.Ret.RetCode = int(errorcode.ErrorCode_MasterParamsError)
		rsp.Ret.RetMsg = err.Error()
		common.WriteAPI(c, rsp)
		return
=======
	if err := utils.DecodeHttpBody(r.Body, req); err != nil {
		rsp.Ret.RetCode = int(errorcode.ErrorCode_MasterParamsError)
		rsp.Ret.RetMsg = err.Error()
		return rsp
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	}

	rt.RequestID = req.RequestID
	rsp.RequestID = req.RequestID
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
	log.G(ctx).Infof("handleListInventoryAction:%+v", utils.InterfaceToString(req))
	allNodes := localcache.GetHealthyNodesByInstanceType(-1, req.InstanceType)
	inventoryType := map[string]*types.InstanceTypeQuotaItem{}
	zoneFilter := []string{}
	cpuTypeFilter := []string{}

	for _, v := range req.Filters {
		if v.Name == "zone" {
			zoneFilter = v.Values
		}
		if v.Name == "cpu-type" {
			cpuTypeFilter = v.Values
		}
	}

	for _, n := range allNodes {
		if len(zoneFilter) > 0 {
			if !utils.Contains(n.Zone, zoneFilter) {
				continue
			}
		}
		if len(cpuTypeFilter) > 0 {
			if !utils.Contains(n.CPUType, cpuTypeFilter) {
				continue
			}
		}

		node_max_mem_reserved_in_mb := getNodeMaxMemReservedConf(n)
		info := &types.InstanceTypeQuotaItem{
			Zone:    n.Zone,
			CPUType: n.CPUType,
			Memory:  n.QuotaMem - n.QuotaMemUsage - node_max_mem_reserved_in_mb,
			CPU:     int64((n.QuotaCpu - n.QuotaCpuUsage) / 1000),
		}
		mergeInventory(inventoryType, info)
	}
	for _, v := range inventoryType {
		rsp.Data = append(rsp.Data, v)
	}
	log.G(ctx).Infof("handleListInventoryAction success:%s", utils.InterfaceToString(rsp))
<<<<<<< HEAD
	common.WriteAPI(c, rsp)
=======
	return rsp
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
}

func mergeInventory(all map[string]*types.InstanceTypeQuotaItem, in *types.InstanceTypeQuotaItem) {
	key := in.Zone + in.CPUType
	if old, ok := all[key]; !ok {
		all[key] = in
	} else {
		old.CPU += in.CPU
		old.Memory += in.Memory
	}
}

func getNodeMaxMemReservedConf(n *node.Node) int64 {
	if n == nil {
		return 0
	}
	if sconf := config.GetConfig().Scheduler; sconf != nil {
		return sconf.GetEffectiveNodeMaxMemReservedInMB(n.InstanceType, n.QuotaMem)
	}
	return 0
}
