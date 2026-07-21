// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package cube

import (
	"errors"
<<<<<<< HEAD
	"strings"

	"github.com/gin-gonic/gin"
=======
	"net/http"
	"strings"

>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/errorcode"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/httpservice/common"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox/types"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/templatecenter"
	"github.com/tencentcloud/CubeSandbox/cubelog"
)

type templateCompatResponse struct {
	*types.Res
	Data *templatecenter.TemplateCompatMatrix `json:"data,omitempty"`
}

type templateCompatActionRequest struct {
	Action     string   `json:"action,omitempty"`
	TemplateID string   `json:"template_id,omitempty"`
	NodeIDs    []string `json:"node_ids,omitempty"`
	AllNodes   bool     `json:"all_nodes,omitempty"`
}

type templateCompatAdoptResponse struct {
	*types.Res
	Updated int `json:"updated"`
}

<<<<<<< HEAD
func getTemplateCompatGinHandler(c *gin.Context) {
	rt := CubeLog.GetTraceInfo(c.Request.Context())
	matrix, err := templatecenter.GetCompatMatrix(c.Request.Context())
	if err != nil {
		rt.RetCode = int64(errorcode.ErrorCode_Unknown)
		common.WriteAPI(c, &templateCompatResponse{
			Res:  &types.Res{Ret: &types.Ret{RetCode: int(errorcode.ErrorCode_Unknown), RetMsg: err.Error()}},
			Data: nil,
		})
		return
	}
	rt.RetCode = int64(errorcode.ErrorCode_Success)
	common.WriteAPI(c, &templateCompatResponse{
		Res:  &types.Res{Ret: &types.Ret{RetCode: int(errorcode.ErrorCode_Success), RetMsg: "success"}},
		Data: matrix,
	})
}

func updateTemplateCompatGinHandler(c *gin.Context) {
	rt := CubeLog.GetTraceInfo(c.Request.Context())
	req := &templateCompatActionRequest{}
	if err := common.GetBodyReq(c.Request, req); err != nil {
		rt.RetCode = int64(errorcode.ErrorCode_MasterParamsError)
		common.WriteAPI(c, &types.Res{Ret: &types.Ret{RetCode: int(errorcode.ErrorCode_MasterParamsError), RetMsg: err.Error()}})
		return
	}
	switch strings.TrimSpace(req.Action) {
	case "adopt_baseline":
		updated, err := templatecenter.AdoptCompatBaseline(c.Request.Context(), req.TemplateID)
		if err != nil {
			retCode := errorcode.ErrorCode_Unknown
			if errors.Is(err, templatecenter.ErrTemplateNotFound) {
				retCode = errorcode.ErrorCode_NotFound
			}
			rt.RetCode = int64(retCode)
			common.WriteAPI(c, &types.Res{Ret: &types.Ret{RetCode: int(retCode), RetMsg: err.Error()}})
			return
		}
		rt.RetCode = int64(errorcode.ErrorCode_Success)
		common.WriteAPI(c, &templateCompatAdoptResponse{
			Res:     &types.Res{Ret: &types.Ret{RetCode: int(errorcode.ErrorCode_Success), RetMsg: "success"}},
			Updated: updated,
		})
		return
	case "rescan":
		if !req.AllNodes && len(req.NodeIDs) == 0 {
			rt.RetCode = int64(errorcode.ErrorCode_MasterParamsError)
			common.WriteAPI(c, &types.Res{Ret: &types.Ret{RetCode: int(errorcode.ErrorCode_MasterParamsError), RetMsg: "node_ids is required unless all_nodes is true"}})
			return
		}
	default:
		rt.RetCode = int64(errorcode.ErrorCode_MasterParamsError)
		common.WriteAPI(c, &types.Res{Ret: &types.Ret{RetCode: int(errorcode.ErrorCode_MasterParamsError), RetMsg: "unsupported template compat action"}})
		return
	}
	if err := templatecenter.RescanCompat(c.Request.Context(), req.NodeIDs); err != nil {
		rt.RetCode = int64(errorcode.ErrorCode_Unknown)
		common.WriteAPI(c, &types.Res{Ret: &types.Ret{RetCode: int(errorcode.ErrorCode_Unknown), RetMsg: err.Error()}})
		return
	}
	rt.RetCode = int64(errorcode.ErrorCode_Success)
	common.WriteAPI(c, &types.Res{Ret: &types.Ret{RetCode: int(errorcode.ErrorCode_Success), RetMsg: "success"}})
=======
func handleTemplateCompatAction(w http.ResponseWriter, r *http.Request, rt *CubeLog.RequestTrace) interface{} {
	_ = w
	switch r.Method {
	case http.MethodGet:
		matrix, err := templatecenter.GetCompatMatrix(r.Context())
		if err != nil {
			rt.RetCode = int64(errorcode.ErrorCode_Unknown)
			return &templateCompatResponse{
				Res:  &types.Res{Ret: &types.Ret{RetCode: int(errorcode.ErrorCode_Unknown), RetMsg: err.Error()}},
				Data: nil,
			}
		}
		rt.RetCode = int64(errorcode.ErrorCode_Success)
		return &templateCompatResponse{
			Res:  &types.Res{Ret: &types.Ret{RetCode: int(errorcode.ErrorCode_Success), RetMsg: "success"}},
			Data: matrix,
		}
	case http.MethodPost:
		req := &templateCompatActionRequest{}
		if err := common.GetBodyReq(r, req); err != nil {
			rt.RetCode = int64(errorcode.ErrorCode_MasterParamsError)
			return &types.Res{Ret: &types.Ret{RetCode: int(errorcode.ErrorCode_MasterParamsError), RetMsg: err.Error()}}
		}
		switch strings.TrimSpace(req.Action) {
		case "adopt_baseline":
			updated, err := templatecenter.AdoptCompatBaseline(r.Context(), req.TemplateID)
			if err != nil {
				retCode := errorcode.ErrorCode_Unknown
				if errors.Is(err, templatecenter.ErrTemplateNotFound) {
					retCode = errorcode.ErrorCode_NotFound
				}
				rt.RetCode = int64(retCode)
				return &types.Res{Ret: &types.Ret{RetCode: int(retCode), RetMsg: err.Error()}}
			}
			rt.RetCode = int64(errorcode.ErrorCode_Success)
			return &templateCompatAdoptResponse{
				Res:     &types.Res{Ret: &types.Ret{RetCode: int(errorcode.ErrorCode_Success), RetMsg: "success"}},
				Updated: updated,
			}
		case "rescan":
			if !req.AllNodes && len(req.NodeIDs) == 0 {
				rt.RetCode = int64(errorcode.ErrorCode_MasterParamsError)
				return &types.Res{Ret: &types.Ret{RetCode: int(errorcode.ErrorCode_MasterParamsError), RetMsg: "node_ids is required unless all_nodes is true"}}
			}
		default:
			rt.RetCode = int64(errorcode.ErrorCode_MasterParamsError)
			return &types.Res{Ret: &types.Ret{RetCode: int(errorcode.ErrorCode_MasterParamsError), RetMsg: "unsupported template compat action"}}
		}
		if err := templatecenter.RescanCompat(r.Context(), req.NodeIDs); err != nil {
			rt.RetCode = int64(errorcode.ErrorCode_Unknown)
			return &types.Res{Ret: &types.Ret{RetCode: int(errorcode.ErrorCode_Unknown), RetMsg: err.Error()}}
		}
		rt.RetCode = int64(errorcode.ErrorCode_Success)
		return &types.Res{Ret: &types.Ret{RetCode: int(errorcode.ErrorCode_Success), RetMsg: "success"}}
	default:
		rt.RetCode = int64(errorcode.ErrorCode_MasterParamsError)
		return &types.Res{Ret: &types.Ret{RetCode: int(errorcode.ErrorCode_MasterParamsError), RetMsg: http.StatusText(http.StatusMethodNotAllowed)}}
	}
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
}
