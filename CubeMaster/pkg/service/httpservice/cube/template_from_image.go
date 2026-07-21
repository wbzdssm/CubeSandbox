// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package cube

import (
	"errors"
	"net/http"
<<<<<<< HEAD
	"os"
=======
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	"path/filepath"
	"strconv"
	"strings"

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
	"gorm.io/gorm"
)

var redoTemplateFromImageFn = templatecenter.SubmitRedoTemplateFromImage

<<<<<<< HEAD
func createTemplateFromImageGinHandler(c *gin.Context) {
	rt := CubeLog.GetTraceInfo(c.Request.Context())
	common.WriteAPI(c, createTemplateFromImage(c.Request, rt))
}

func getTemplateFromImageGinHandler(c *gin.Context) {
	rt := CubeLog.GetTraceInfo(c.Request.Context())
	common.WriteAPI(c, getTemplateFromImage(c.Request, rt))
}

func handleRedoTemplateAction(c *gin.Context) {
	rt := CubeLog.GetTraceInfo(c.Request.Context())
	req := &types.RedoTemplateFromImageReq{}
	if err := common.GetBodyReq(c.Request, req); err != nil {
		common.WriteAPI(c, &types.CreateTemplateFromImageRes{
=======
func handleTemplateFromImageAction(w http.ResponseWriter, r *http.Request, rt *CubeLog.RequestTrace) interface{} {
	switch r.Method {
	case http.MethodPost:
		return createTemplateFromImage(w, r, rt)
	case http.MethodGet:
		return getTemplateFromImage(w, r, rt)
	default:
		return &types.Res{
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_MasterParamsError),
				RetMsg:  http.StatusText(http.StatusMethodNotAllowed),
			},
		}
	}
}

func handleRedoTemplateAction(w http.ResponseWriter, r *http.Request, rt *CubeLog.RequestTrace) interface{} {
	if r.Method != http.MethodPost {
		return &types.Res{
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_MasterParamsError),
				RetMsg:  http.StatusText(http.StatusMethodNotAllowed),
			},
		}
	}
	_ = w
	req := &types.RedoTemplateFromImageReq{}
	if err := common.GetBodyReq(r, req); err != nil {
		return &types.CreateTemplateFromImageRes{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_MasterParamsError),
				RetMsg:  err.Error(),
			},
<<<<<<< HEAD
		})
		return
	}
	rt.RequestID = req.RequestID
	ctx := log.WithLogger(c.Request.Context(), log.G(c.Request.Context()).WithFields(map[string]any{
=======
		}
	}
	rt.RequestID = req.RequestID
	ctx := log.WithLogger(r.Context(), log.G(r.Context()).WithFields(map[string]any{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
		"RequestId":  req.RequestID,
		"Action":     "RedoTemplate",
		"TemplateID": req.TemplateID,
	}))
<<<<<<< HEAD
	job, err := redoTemplateFromImageFn(ctx, req, requestBaseURL(c.Request))
	if err != nil {
		common.WriteAPI(c, &types.CreateTemplateFromImageRes{
=======
	job, err := redoTemplateFromImageFn(ctx, req, requestBaseURL(r))
	if err != nil {
		return &types.CreateTemplateFromImageRes{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
			RequestID: req.RequestID,
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_MasterParamsError),
				RetMsg:  err.Error(),
			},
<<<<<<< HEAD
		})
		return
	}
	rt.RetCode = int64(errorcode.ErrorCode_Success)
	common.WriteAPI(c, &types.CreateTemplateFromImageRes{
=======
		}
	}
	rt.RetCode = int64(errorcode.ErrorCode_Success)
	return &types.CreateTemplateFromImageRes{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
		RequestID: req.RequestID,
		Ret: &types.Ret{
			RetCode: int(errorcode.ErrorCode_Success),
			RetMsg:  "success",
		},
		Job: job,
<<<<<<< HEAD
	})
}

func createTemplateFromImage(r *http.Request, rt *CubeLog.RequestTrace) interface{} {
=======
	}
}

func createTemplateFromImage(w http.ResponseWriter, r *http.Request, rt *CubeLog.RequestTrace) interface{} {
	_ = w
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	req := &types.CreateTemplateFromImageReq{}
	if err := common.GetBodyReq(r, req); err != nil {
		return &types.CreateTemplateFromImageRes{
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_MasterParamsError),
				RetMsg:  err.Error(),
			},
		}
	}
	rt.RequestID = req.RequestID
	ctx := log.WithLogger(r.Context(), log.G(r.Context()).WithFields(map[string]any{
		"RequestId":    req.RequestID,
		"InstanceType": req.InstanceType,
		"Action":       "CreateTemplateFromImage",
		"TemplateID":   req.TemplateID,
	}))
	job, err := templatecenter.SubmitTemplateFromImage(ctx, req, requestBaseURL(r))
	if err != nil {
		return &types.CreateTemplateFromImageRes{
			RequestID: req.RequestID,
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_MasterParamsError),
				RetMsg:  err.Error(),
			},
		}
	}
	rt.RetCode = int64(errorcode.ErrorCode_Success)
	return &types.CreateTemplateFromImageRes{
		RequestID: req.RequestID,
		Ret: &types.Ret{
			RetCode: int(errorcode.ErrorCode_Success),
			RetMsg:  "success",
		},
		Job: job,
	}
}

<<<<<<< HEAD
func getTemplateFromImage(r *http.Request, rt *CubeLog.RequestTrace) interface{} {
=======
func getTemplateFromImage(w http.ResponseWriter, r *http.Request, rt *CubeLog.RequestTrace) interface{} {
	_ = w
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	jobID := strings.TrimSpace(r.URL.Query().Get("job_id"))
	if jobID == "" {
		return &types.CreateTemplateFromImageRes{
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_MasterParamsError),
				RetMsg:  "job_id is required",
			},
		}
	}
	job, err := templatecenter.GetTemplateImageJobInfo(r.Context(), jobID)
	if err != nil {
		code := int(errorcode.ErrorCode_MasterInternalError)
		if errors.Is(err, templatecenter.ErrTemplateStoreNotInitialized) {
			code = int(errorcode.ErrorCode_DBError)
		}
		return &types.CreateTemplateFromImageRes{
			Ret: &types.Ret{
				RetCode: code,
				RetMsg:  err.Error(),
			},
		}
	}
	rt.RetCode = int64(errorcode.ErrorCode_Success)
	return &types.CreateTemplateFromImageRes{
		Ret: &types.Ret{
			RetCode: int(errorcode.ErrorCode_Success),
			RetMsg:  "success",
		},
		Job: job,
	}
}

<<<<<<< HEAD
// openTemplateArtifactForDownload resolves, opens, and stats the template
// rootfs artifact identified by the artifact_id/token query params and writes
// the common response headers (Content-Type/Length, ETag, X-Cube-Artifact-Id).
// On error it writes the API error response and returns ok=false. On success
// the caller owns file (must Close).
func openTemplateArtifactForDownload(c *gin.Context) (name string, file *os.File, stat os.FileInfo, ok bool) {
	artifactID := strings.TrimSpace(c.Query("artifact_id"))
	token := strings.TrimSpace(c.Query("token"))
	record, f, err := templatecenter.OpenRootfsArtifact(c.Request.Context(), artifactID, token)
	if err != nil {
		common.WriteAPI(c, &types.Res{
=======
func handleTemplateArtifactDownloadAction(w http.ResponseWriter, r *http.Request, rt *CubeLog.RequestTrace) interface{} {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		return &types.Res{
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_MasterParamsError),
				RetMsg:  http.StatusText(http.StatusMethodNotAllowed),
			},
		}
	}
	artifactID := strings.TrimSpace(r.URL.Query().Get("artifact_id"))
	token := strings.TrimSpace(r.URL.Query().Get("token"))
	record, file, err := templatecenter.OpenRootfsArtifact(r.Context(), artifactID, token)
	if err != nil {
		return &types.Res{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_NotFound),
				RetMsg:  err.Error(),
			},
<<<<<<< HEAD
		})
		return "", nil, nil, false
	}
	st, err := f.Stat()
	if err != nil {
		f.Close()
		common.WriteAPI(c, &types.Res{
=======
		}
	}
	defer file.Close()
	stat, err := file.Stat()
	if err != nil {
		return &types.Res{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_MasterInternalError),
				RetMsg:  err.Error(),
			},
<<<<<<< HEAD
		})
		return "", nil, nil, false
	}
	c.Writer.Header().Set("Content-Type", "application/octet-stream")
	c.Writer.Header().Set("Content-Length", strconv.FormatInt(st.Size(), 10))
	c.Writer.Header().Set("ETag", record.Ext4SHA256)
	c.Writer.Header().Set("X-Cube-Artifact-Id", record.ArtifactID)
	return filepath.Base(record.Ext4Path), f, st, true
}

func downloadTemplateArtifactGinHandler(c *gin.Context) {
	rt := CubeLog.GetTraceInfo(c.Request.Context())
	name, file, stat, ok := openTemplateArtifactForDownload(c)
	if !ok {
		return
	}
	defer file.Close()
	http.ServeContent(c.Writer, c.Request, name, stat.ModTime(), file)
	rt.RetCode = int64(errorcode.ErrorCode_Success)
}

func headTemplateArtifactGinHandler(c *gin.Context) {
	rt := CubeLog.GetTraceInfo(c.Request.Context())
	_, file, _, ok := openTemplateArtifactForDownload(c)
	if !ok {
		return
	}
	file.Close()
	rt.RetCode = int64(errorcode.ErrorCode_Success)
}

func handleRootfsArtifactAction(c *gin.Context) {
	rt := CubeLog.GetTraceInfo(c.Request.Context())
	artifactID := strings.TrimSpace(c.Query("artifact_id"))
	if artifactID == "" {
		common.WriteAPI(c, &types.CreateTemplateFromImageRes{
=======
		}
	}
	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Length", strconv.FormatInt(stat.Size(), 10))
	w.Header().Set("ETag", record.Ext4SHA256)
	w.Header().Set("X-Cube-Artifact-Id", record.ArtifactID)
	if r.Method == http.MethodHead {
		rt.RetCode = int64(errorcode.ErrorCode_Success)
		return nil
	}
	http.ServeContent(w, r, filepath.Base(record.Ext4Path), stat.ModTime(), file)
	rt.RetCode = int64(errorcode.ErrorCode_Success)
	return nil
}

func handleRootfsArtifactAction(w http.ResponseWriter, r *http.Request, rt *CubeLog.RequestTrace) interface{} {
	_ = w
	if r.Method != http.MethodGet {
		return &types.Res{
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_MasterParamsError),
				RetMsg:  http.StatusText(http.StatusMethodNotAllowed),
			},
		}
	}
	artifactID := strings.TrimSpace(r.URL.Query().Get("artifact_id"))
	if artifactID == "" {
		return &types.CreateTemplateFromImageRes{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_MasterParamsError),
				RetMsg:  "artifact_id is required",
			},
<<<<<<< HEAD
		})
		return
	}
	info, err := templatecenter.GetRootfsArtifactInfo(c.Request.Context(), artifactID)
=======
		}
	}
	info, err := templatecenter.GetRootfsArtifactInfo(r.Context(), artifactID)
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	if err != nil {
		code := int(errorcode.ErrorCode_MasterInternalError)
		if errors.Is(err, gorm.ErrRecordNotFound) {
			code = int(errorcode.ErrorCode_NotFound)
		}
<<<<<<< HEAD
		common.WriteAPI(c, &types.CreateTemplateFromImageRes{
=======
		return &types.CreateTemplateFromImageRes{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
			Ret: &types.Ret{
				RetCode: code,
				RetMsg:  err.Error(),
			},
<<<<<<< HEAD
		})
		return
	}
	rt.RetCode = int64(errorcode.ErrorCode_Success)
	common.WriteAPI(c, &types.CreateTemplateFromImageRes{
=======
		}
	}
	rt.RetCode = int64(errorcode.ErrorCode_Success)
	return &types.CreateTemplateFromImageRes{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
		Ret: &types.Ret{
			RetCode: int(errorcode.ErrorCode_Success),
			RetMsg:  "success",
		},
		Job: &types.TemplateImageJobInfo{
			ArtifactID:     info.ArtifactID,
			ArtifactStatus: info.Status,
			Artifact:       info,
		},
<<<<<<< HEAD
	})
=======
	}
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
}

func requestBaseURL(r *http.Request) string {
	scheme := "http"
	if r.TLS != nil {
		scheme = "https"
	}
	return scheme + "://" + r.Host
}
