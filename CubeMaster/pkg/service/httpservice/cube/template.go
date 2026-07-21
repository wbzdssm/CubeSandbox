// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package cube

import (
	"errors"
	"net/http"

<<<<<<< HEAD
	"github.com/gin-gonic/gin"
=======
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/log"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/errorcode"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/httpservice/common"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox/types"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/templatecenter"
	"github.com/tencentcloud/CubeSandbox/cubelog"
)

var deleteTemplateFn = templatecenter.DeleteTemplate
var getTemplateInfoFn = templatecenter.GetTemplateInfo
var getTemplateRequestFn = templatecenter.GetTemplateRequest
<<<<<<< HEAD
var resolveTemplateIdentifierFn = templatecenter.ResolveTemplateIdentifier
=======
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)

type templateResponse struct {
	*types.Res
	TemplateID                 string                         `json:"template_id,omitempty"`
	InstanceType               string                         `json:"instance_type,omitempty"`
	Version                    string                         `json:"version,omitempty"`
	Status                     string                         `json:"status,omitempty"`
	LastError                  string                         `json:"last_error,omitempty"`
	DisplayName                string                         `json:"display_name,omitempty"`
	CreatedAt                  string                         `json:"created_at,omitempty"`
	ImageInfo                  string                         `json:"image_info,omitempty"`
	JobID                      string                         `json:"job_id,omitempty"`
	Replicas                   []templatecenter.ReplicaStatus `json:"replicas,omitempty"`
	CreateRequest              *types.CreateCubeSandboxReq    `json:"create_request,omitempty"`
	CubeEgressCABaked          bool                           `json:"cube_egress_ca_baked,omitempty"`
	CubeEgressCAFingerprint    string                         `json:"cube_egress_ca_fingerprint,omitempty"`
	CubeEgressCATargetsWritten int                            `json:"cube_egress_ca_targets_written,omitempty"`
}

type templateListResponse struct {
	*types.Res
	Data []templateSummary `json:"data,omitempty"`
}

type templateSummary struct {
	TemplateID   string `json:"template_id,omitempty"`
	InstanceType string `json:"instance_type,omitempty"`
	Version      string `json:"version,omitempty"`
	Status       string `json:"status,omitempty"`
	LastError    string `json:"last_error,omitempty"`
<<<<<<< HEAD
	DisplayName  string `json:"display_name,omitempty"`
=======
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	CreatedAt    string `json:"created_at,omitempty"`
	ImageInfo    string `json:"image_info,omitempty"`
	JobID        string `json:"job_id,omitempty"`
}

type deleteTemplateRequest struct {
	RequestID    string `json:"RequestID,omitempty"`
	TemplateID   string `json:"template_id,omitempty"`
	InstanceType string `json:"instance_type,omitempty"`
	Sync         bool   `json:"sync,omitempty"`
}

<<<<<<< HEAD
func createTemplateGinHandler(c *gin.Context) {
	rt := CubeLog.GetTraceInfo(c.Request.Context())
	common.WriteAPI(c, createTemplate(c.Request, rt))
}

func getTemplateGinHandler(c *gin.Context) {
	rt := CubeLog.GetTraceInfo(c.Request.Context())
	common.WriteAPI(c, getTemplate(c.Request, rt))
}

func deleteTemplateGinHandler(c *gin.Context) {
	rt := CubeLog.GetTraceInfo(c.Request.Context())
	common.WriteAPI(c, deleteTemplate(c.Request, rt))
}

func deleteTemplate(r *http.Request, rt *CubeLog.RequestTrace) interface{} {
=======
func handleTemplateAction(w http.ResponseWriter, r *http.Request, rt *CubeLog.RequestTrace) interface{} {
	switch r.Method {
	case http.MethodPost:
		return createTemplate(w, r, rt)
	case http.MethodGet:
		return getTemplate(w, r, rt)
	case http.MethodDelete:
		return deleteTemplate(w, r, rt)
	default:
		return &types.Res{
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_MasterParamsError),
				RetMsg:  http.StatusText(http.StatusMethodNotAllowed),
			},
		}
	}
}

func deleteTemplate(w http.ResponseWriter, r *http.Request, rt *CubeLog.RequestTrace) interface{} {
	_ = w
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	req := &deleteTemplateRequest{}
	if err := common.GetBodyReq(r, req); err != nil {
		return &templateResponse{
			Res: &types.Res{Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_MasterParamsError),
				RetMsg:  err.Error(),
			}},
		}
	}
	if req.TemplateID == "" {
		return &templateResponse{
			Res: &types.Res{Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_MasterParamsError),
				RetMsg:  "template_id is required",
			}},
		}
	}
	ctx := log.WithLogger(r.Context(), log.G(r.Context()).WithFields(map[string]any{
		"RequestId":    req.RequestID,
		"InstanceType": req.InstanceType,
		"Action":       "DeleteTemplate",
		"TemplateID":   req.TemplateID,
	}))
<<<<<<< HEAD
	// Alias resolution: resolve human-readable aliases to template IDs,
	// matching the GET handler (see getTemplate).
	resolvedTemplateID, err := resolveTemplateIdentifierFn(ctx, req.TemplateID)
	if err != nil {
		code := int(errorcode.ErrorCode_MasterInternalError)
		if errors.Is(err, templatecenter.ErrTemplateNotFound) {
			code = int(errorcode.ErrorCode_NotFound)
		}
		rt.RetCode = int64(code)
		return &templateResponse{
			Res: &types.Res{Ret: &types.Ret{
				RetCode: code,
				RetMsg:  err.Error(),
			}},
			TemplateID: req.TemplateID,
		}
	}
	err = deleteTemplateFn(ctx, resolvedTemplateID, req.InstanceType)
=======
	err := deleteTemplateFn(ctx, req.TemplateID, req.InstanceType)
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	if err != nil {
		code := int(errorcode.ErrorCode_MasterInternalError)
		switch {
		case errors.Is(err, templatecenter.ErrTemplateNotFound):
			code = int(errorcode.ErrorCode_NotFound)
		case errors.Is(err, templatecenter.ErrTemplateInUse):
			code = int(errorcode.ErrorCode_Conflict)
		case errors.Is(err, templatecenter.ErrTemplateAttemptInProgress):
			code = int(errorcode.ErrorCode_Conflict)
		case errors.Is(err, templatecenter.ErrTemplateCleanupLocatorMissing):
			code = int(errorcode.ErrorCode_NotFound)
		case errors.Is(err, templatecenter.ErrTemplateStoreNotInitialized):
			code = int(errorcode.ErrorCode_DBError)
		}
		rt.RetCode = int64(code)
		return &templateResponse{
			Res: &types.Res{
				RequestID: req.RequestID,
				Ret: &types.Ret{
					RetCode: code,
					RetMsg:  err.Error(),
				},
			},
			TemplateID: req.TemplateID,
		}
	}
	rt.RequestID = req.RequestID
	rt.InstanceType = req.InstanceType
	rt.RetCode = int64(errorcode.ErrorCode_Success)
	return &templateResponse{
		Res: &types.Res{
			RequestID: req.RequestID,
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_Success),
				RetMsg:  "success",
			},
		},
		TemplateID: req.TemplateID,
	}
}

<<<<<<< HEAD
func createTemplate(r *http.Request, rt *CubeLog.RequestTrace) interface{} {
=======
func createTemplate(w http.ResponseWriter, r *http.Request, rt *CubeLog.RequestTrace) interface{} {
	_ = w
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	req, err := constructCreateReq(r)
	if err != nil {
		return &templateResponse{
			Res: &types.Res{Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_MasterParamsError),
				RetMsg:  err.Error(),
			}},
		}
	}
	ctx := log.WithLogger(r.Context(), log.G(r.Context()).WithFields(map[string]any{
		"RequestId":    req.RequestID,
		"InstanceType": req.InstanceType,
		"Action":       "CreateTemplate",
	}))
	info, err := templatecenter.CreateTemplate(ctx, req)
	if err != nil {
		code := int(errorcode.ErrorCode_MasterInternalError)
		switch {
		case errors.Is(err, templatecenter.ErrTemplateIDRequired),
			errors.Is(err, templatecenter.ErrDuplicateTemplate),
			errors.Is(err, templatecenter.ErrNoTemplateNodes):
			code = int(errorcode.ErrorCode_MasterParamsError)
		case errors.Is(err, templatecenter.ErrTemplateStoreNotInitialized):
			code = int(errorcode.ErrorCode_DBError)
		}
		rt.RetCode = int64(code)
		return &templateResponse{
			Res: &types.Res{
				RequestID: req.RequestID,
				Ret: &types.Ret{
					RetCode: code,
					RetMsg:  err.Error(),
				},
			},
		}
	}
	rt.RequestID = req.RequestID
	rt.InstanceType = req.InstanceType
	rt.RetCode = int64(errorcode.ErrorCode_Success)
	return &templateResponse{
		Res: &types.Res{
			RequestID: req.RequestID,
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_Success),
				RetMsg:  "success",
			},
		},
		TemplateID:                 info.TemplateID,
		InstanceType:               info.InstanceType,
		Version:                    info.Version,
		Status:                     info.Status,
		LastError:                  info.LastError,
		JobID:                      info.JobID,
		Replicas:                   info.Replicas,
		CubeEgressCABaked:          info.CubeEgressCABaked,
		CubeEgressCAFingerprint:    info.CubeEgressCAFingerprint,
		CubeEgressCATargetsWritten: info.CubeEgressCATargetsWritten,
	}
}

<<<<<<< HEAD
func getTemplate(r *http.Request, rt *CubeLog.RequestTrace) interface{} {
=======
func getTemplate(w http.ResponseWriter, r *http.Request, rt *CubeLog.RequestTrace) interface{} {
	_ = w
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	templateID := r.URL.Query().Get("template_id")
	includeRequest := r.URL.Query().Get("include_request") == "true" || r.URL.Query().Get("include_request") == "1"
	if templateID == "" {
		return listTemplates(r, rt)
	}
<<<<<<< HEAD
	resolvedTemplateID, err := resolveTemplateIdentifierFn(r.Context(), templateID)
	if err != nil {
		code := int(errorcode.ErrorCode_MasterInternalError)
		if errors.Is(err, templatecenter.ErrTemplateNotFound) {
			code = int(errorcode.ErrorCode_NotFound)
		}
		rt.RetCode = int64(code)
		return &templateResponse{
			Res: &types.Res{Ret: &types.Ret{
				RetCode: code,
				RetMsg:  err.Error(),
			}},
			TemplateID: templateID,
		}
	}
	info, err := getTemplateInfoFn(r.Context(), resolvedTemplateID)
=======
	info, err := getTemplateInfoFn(r.Context(), templateID)
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	if err != nil {
		code := int(errorcode.ErrorCode_MasterInternalError)
		if errors.Is(err, templatecenter.ErrTemplateNotFound) {
			code = int(errorcode.ErrorCode_NotFound)
		}
		rt.RetCode = int64(code)
		return &templateResponse{
			Res: &types.Res{Ret: &types.Ret{
				RetCode: code,
				RetMsg:  err.Error(),
			}},
			TemplateID: templateID,
		}
	}
	var createReq *types.CreateCubeSandboxReq
	if includeRequest {
<<<<<<< HEAD
		createReq, err = getTemplateRequestFn(r.Context(), resolvedTemplateID)
=======
		createReq, err = getTemplateRequestFn(r.Context(), templateID)
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
		if err != nil {
			code := int(errorcode.ErrorCode_MasterInternalError)
			if errors.Is(err, templatecenter.ErrTemplateNotFound) {
				code = int(errorcode.ErrorCode_NotFound)
			}
			rt.RetCode = int64(code)
			return &templateResponse{
				Res: &types.Res{Ret: &types.Ret{
					RetCode: code,
					RetMsg:  err.Error(),
				}},
				TemplateID: templateID,
			}
		}
	}
	rt.RetCode = int64(errorcode.ErrorCode_Success)
	return &templateResponse{
		Res: &types.Res{
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_Success),
				RetMsg:  "success",
			},
		},
		TemplateID:                 info.TemplateID,
		InstanceType:               info.InstanceType,
		Version:                    info.Version,
		Status:                     info.Status,
		LastError:                  info.LastError,
		DisplayName:                info.DisplayName,
		CreatedAt:                  info.CreatedAt,
		ImageInfo:                  info.ImageInfo,
		JobID:                      info.JobID,
		Replicas:                   info.Replicas,
		CreateRequest:              createReq,
		CubeEgressCABaked:          info.CubeEgressCABaked,
		CubeEgressCAFingerprint:    info.CubeEgressCAFingerprint,
		CubeEgressCATargetsWritten: info.CubeEgressCATargetsWritten,
	}
}

func listTemplates(r *http.Request, rt *CubeLog.RequestTrace) interface{} {
	infos, err := templatecenter.ListTemplates(r.Context())
	if err != nil {
		code := int(errorcode.ErrorCode_MasterInternalError)
		if errors.Is(err, templatecenter.ErrTemplateStoreNotInitialized) {
			code = int(errorcode.ErrorCode_DBError)
		}
		rt.RetCode = int64(code)
		return &templateListResponse{
			Res: &types.Res{Ret: &types.Ret{
				RetCode: code,
				RetMsg:  err.Error(),
			}},
		}
	}
	rsp := &templateListResponse{
		Res: &types.Res{Ret: &types.Ret{
			RetCode: int(errorcode.ErrorCode_Success),
			RetMsg:  "success",
		}},
		Data: make([]templateSummary, 0, len(infos)),
	}
	for _, info := range infos {
		rsp.Data = append(rsp.Data, templateSummary{
			TemplateID:   info.TemplateID,
			InstanceType: info.InstanceType,
			Version:      info.Version,
			Status:       info.Status,
			LastError:    info.LastError,
<<<<<<< HEAD
			DisplayName:  info.DisplayName,
=======
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
			CreatedAt:    info.CreatedAt,
			ImageInfo:    info.ImageInfo,
			JobID:        info.JobID,
		})
	}
	rt.RetCode = int64(errorcode.ErrorCode_Success)
	return rsp
}
