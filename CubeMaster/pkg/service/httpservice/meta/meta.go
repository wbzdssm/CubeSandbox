// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package meta

import (
	"errors"
	"net/http"

<<<<<<< HEAD
	"github.com/gin-gonic/gin"
=======
	"github.com/gorilla/mux"
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/errorcode"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/nodemeta"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/httpservice/common"
	sandboxtypes "github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox/types"
	"gorm.io/gorm"
)

const (
	metaURI             = "/internal/meta"
	readyzAction        = "/readyz"
	registerNodeAction  = "/nodes/register"
	nodesAction         = "/nodes"
<<<<<<< HEAD
	nodeAction          = "/nodes/:node_id"
	nodeStatusAction    = "/nodes/:node_id/status"
	nodeLabelsAction    = "/nodes/:node_id/labels"
	nodeIsolationAction = "/nodes/:node_id/isolation"
=======
	nodeAction          = "/nodes/{node_id}"
	nodeStatusAction    = "/nodes/{node_id}/status"
	nodeLabelsAction    = "/nodes/{node_id}/labels"
	nodeIsolationAction = "/nodes/{node_id}/isolation"
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	versionMatrixAction = "/version-matrix"
)

type nodesResponse struct {
	RequestID string                   `json:"requestID,omitempty"`
	Ret       *sandboxtypes.Ret        `json:"ret,omitempty"`
	Data      []*nodemeta.NodeSnapshot `json:"data,omitempty"`
}

type nodeResponse struct {
	RequestID string                 `json:"requestID,omitempty"`
	Ret       *sandboxtypes.Ret      `json:"ret,omitempty"`
	Data      *nodemeta.NodeSnapshot `json:"data,omitempty"`
}

type versionMatrixResponse struct {
	RequestID string                  `json:"requestID,omitempty"`
	Ret       *sandboxtypes.Ret       `json:"ret,omitempty"`
	Data      *nodemeta.VersionMatrix `json:"data,omitempty"`
}

func MetaURI() string {
	return metaURI
}

func ReadyzAction() string {
	return readyzAction
}

func RegisterNodeAction() string {
	return registerNodeAction
}

func NodesAction() string {
	return nodesAction
}

func NodeAction() string {
	return nodeAction
}

func NodeStatusAction() string {
	return nodeStatusAction
}

func VersionMatrixAction() string {
	return versionMatrixAction
}

func NodeLabelsAction() string {
	return nodeLabelsAction
}

func NodeIsolationAction() string {
	return nodeIsolationAction
}

<<<<<<< HEAD
func readyzGinHandler(c *gin.Context) {
=======
func ReadyzHandler(w http.ResponseWriter, r *http.Request) {
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	retCode := int(errorcode.ErrorCode_Success)
	retMsg := "ok"
	if !nodemeta.Ready() {
		retCode = int(errorcode.ErrorCode_MasterInternalError)
		retMsg = "metadata service not ready"
	}
<<<<<<< HEAD
	common.WriteAPI(c, &sandboxtypes.Res{
=======
	common.WriteResponse(w, http.StatusOK, &sandboxtypes.Res{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
		Ret: &sandboxtypes.Ret{
			RetCode: retCode,
			RetMsg:  retMsg,
		},
	})
}

<<<<<<< HEAD
func registerNodeGinHandler(c *gin.Context) {
	req := &nodemeta.RegisterNodeRequest{}
	if err := common.GetBodyReq(c.Request, req); err != nil {
		writeErr(c.Writer, http.StatusBadRequest, err)
		return
	}
	data, err := nodemeta.RegisterNode(c.Request.Context(), req)
	if err != nil {
		writeErr(c.Writer, http.StatusOK, err)
		return
	}
	common.WriteAPI(c, &nodeResponse{
=======
func RegisterNodeHandler(w http.ResponseWriter, r *http.Request) {
	req := &nodemeta.RegisterNodeRequest{}
	if err := common.GetBodyReq(r, req); err != nil {
		writeErr(w, http.StatusBadRequest, err)
		return
	}
	data, err := nodemeta.RegisterNode(r.Context(), req)
	if err != nil {
		writeErr(w, http.StatusOK, err)
		return
	}
	common.WriteResponse(w, http.StatusOK, &nodeResponse{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
		RequestID: req.RequestID,
		Ret:       successRet(),
		Data:      data,
	})
}

<<<<<<< HEAD
func updateNodeStatusGinHandler(c *gin.Context) {
	req := &nodemeta.UpdateNodeStatusRequest{}
	if err := common.GetBodyReq(c.Request, req); err != nil {
		writeErr(c.Writer, http.StatusBadRequest, err)
		return
	}
	nodeID := c.Param("node_id")
	data, err := nodemeta.UpdateNodeStatus(c.Request.Context(), nodeID, req)
	if err != nil {
		writeErr(c.Writer, http.StatusOK, err)
		return
	}
	common.WriteAPI(c, &nodeResponse{
=======
func UpdateNodeStatusHandler(w http.ResponseWriter, r *http.Request) {
	req := &nodemeta.UpdateNodeStatusRequest{}
	if err := common.GetBodyReq(r, req); err != nil {
		writeErr(w, http.StatusBadRequest, err)
		return
	}
	nodeID := mux.Vars(r)["node_id"]
	data, err := nodemeta.UpdateNodeStatus(r.Context(), nodeID, req)
	if err != nil {
		writeErr(w, http.StatusOK, err)
		return
	}
	common.WriteResponse(w, http.StatusOK, &nodeResponse{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
		RequestID: req.RequestID,
		Ret:       successRet(),
		Data:      data,
	})
}

<<<<<<< HEAD
func getNodeGinHandler(c *gin.Context) {
	nodeID := c.Param("node_id")
	data, err := nodemeta.GetNode(c.Request.Context(), nodeID)
	if err != nil {
		writeErr(c.Writer, http.StatusOK, err)
		return
	}
	common.WriteAPI(c, &nodeResponse{
=======
func GetNodeHandler(w http.ResponseWriter, r *http.Request) {
	nodeID := mux.Vars(r)["node_id"]
	data, err := nodemeta.GetNode(r.Context(), nodeID)
	if err != nil {
		writeErr(w, http.StatusOK, err)
		return
	}
	common.WriteResponse(w, http.StatusOK, &nodeResponse{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
		Ret:  successRet(),
		Data: data,
	})
}

<<<<<<< HEAD
func listNodesGinHandler(c *gin.Context) {
	data, err := nodemeta.ListNodes(c.Request.Context())
	if err != nil {
		writeErr(c.Writer, http.StatusOK, err)
		return
	}
	common.WriteAPI(c, &nodesResponse{
=======
func ListNodesHandler(w http.ResponseWriter, r *http.Request) {
	data, err := nodemeta.ListNodes(r.Context())
	if err != nil {
		writeErr(w, http.StatusOK, err)
		return
	}
	common.WriteResponse(w, http.StatusOK, &nodesResponse{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
		Ret:  successRet(),
		Data: data,
	})
}

<<<<<<< HEAD
func versionMatrixGinHandler(c *gin.Context) {
	data, err := nodemeta.GetVersionMatrix(c.Request.Context())
	if err != nil {
		writeErr(c.Writer, http.StatusOK, err)
		return
	}
	common.WriteAPI(c, &versionMatrixResponse{
=======
func VersionMatrixHandler(w http.ResponseWriter, r *http.Request) {
	data, err := nodemeta.GetVersionMatrix(r.Context())
	if err != nil {
		writeErr(w, http.StatusOK, err)
		return
	}
	common.WriteResponse(w, http.StatusOK, &versionMatrixResponse{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
		Ret:  successRet(),
		Data: data,
	})
}

<<<<<<< HEAD
func updateNodeLabelsGinHandler(c *gin.Context) {
	nodeID := c.Param("node_id")
	req := &nodemeta.UpdateNodeLabelsRequest{}
	if err := common.GetBodyReq(c.Request, req); err != nil {
		writeErr(c.Writer, http.StatusBadRequest, err)
		return
	}
	if err := nodemeta.UpdateNodeLabels(c.Request.Context(), nodeID, req.Labels); err != nil {
		writeErr(c.Writer, http.StatusOK, err)
		return
	}
	common.WriteAPI(c, &sandboxtypes.Res{
=======
func UpdateNodeLabelsHandler(w http.ResponseWriter, r *http.Request) {
	nodeID := mux.Vars(r)["node_id"]
	req := &nodemeta.UpdateNodeLabelsRequest{}
	if err := common.GetBodyReq(r, req); err != nil {
		writeErr(w, http.StatusBadRequest, err)
		return
	}
	if err := nodemeta.UpdateNodeLabels(r.Context(), nodeID, req.Labels); err != nil {
		writeErr(w, http.StatusOK, err)
		return
	}
	common.WriteResponse(w, http.StatusOK, &sandboxtypes.Res{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
		Ret: successRet(),
	})
}

<<<<<<< HEAD
func deleteNodeLabelGinHandler(c *gin.Context) {
	nodeID := c.Param("node_id")
	key := c.Query("key")
	if err := nodemeta.DeleteNodeLabel(c.Request.Context(), nodeID, key); err != nil {
		writeErr(c.Writer, http.StatusOK, err)
		return
	}
	common.WriteAPI(c, &sandboxtypes.Res{
=======
func DeleteNodeLabelHandler(w http.ResponseWriter, r *http.Request) {
	nodeID := mux.Vars(r)["node_id"]
	key := r.URL.Query().Get("key")
	if err := nodemeta.DeleteNodeLabel(r.Context(), nodeID, key); err != nil {
		writeErr(w, http.StatusOK, err)
		return
	}
	common.WriteResponse(w, http.StatusOK, &sandboxtypes.Res{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
		Ret: successRet(),
	})
}

<<<<<<< HEAD
// isolateNodeGinHandler cordons a node (PUT). Idempotent; no request body.
func isolateNodeGinHandler(c *gin.Context) {
	writeIsolation(c, true)
}

// unisolateNodeGinHandler removes the cordon (DELETE). Idempotent.
func unisolateNodeGinHandler(c *gin.Context) {
	writeIsolation(c, false)
}

func writeIsolation(c *gin.Context, disabled bool) {
	data, err := nodemeta.SetNodeSchedulingDisabled(c.Request.Context(), c.Param("node_id"), disabled)
	if err != nil {
		writeErr(c.Writer, http.StatusOK, err)
		return
	}
	common.WriteAPI(c, &nodeResponse{Ret: successRet(), Data: data})
=======
// IsolateNodeHandler cordons a node (PUT). Idempotent; no request body.
func IsolateNodeHandler(w http.ResponseWriter, r *http.Request) {
	writeIsolation(w, r, true)
}

// UnisolateNodeHandler removes the cordon (DELETE). Idempotent.
func UnisolateNodeHandler(w http.ResponseWriter, r *http.Request) {
	writeIsolation(w, r, false)
}

func writeIsolation(w http.ResponseWriter, r *http.Request, disabled bool) {
	data, err := nodemeta.SetNodeSchedulingDisabled(r.Context(), mux.Vars(r)["node_id"], disabled)
	if err != nil {
		writeErr(w, http.StatusOK, err)
		return
	}
	common.WriteResponse(w, http.StatusOK, &nodeResponse{Ret: successRet(), Data: data})
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
}

func successRet() *sandboxtypes.Ret {
	return &sandboxtypes.Ret{
		RetCode: int(errorcode.ErrorCode_Success),
		RetMsg:  errorcode.ErrorCode_Success.String(),
	}
}

func writeErr(w http.ResponseWriter, status int, err error) {
	retCode := int(errorcode.ErrorCode_MasterInternalError)
	switch {
	case errors.Is(err, gorm.ErrRecordNotFound):
		retCode = int(errorcode.ErrorCode_NotFound)
	case errors.Is(err, nodemeta.ErrLabelsJSONCorrupt), errors.Is(err, nodemeta.ErrSchedulingLabelRejected):
		retCode = int(errorcode.ErrorCode_MasterParamsError)
	}
	common.WriteResponse(w, status, &sandboxtypes.Res{
		Ret: &sandboxtypes.Ret{
			RetCode: retCode,
			RetMsg:  err.Error(),
		},
	})
}
