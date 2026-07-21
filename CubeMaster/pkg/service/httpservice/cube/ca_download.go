// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package cube

import (
	"errors"
	"net/http"
	"os"
	"path/filepath"
	"strconv"

<<<<<<< HEAD
	"github.com/gin-gonic/gin"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/errorcode"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/httpservice/common"
=======
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/errorcode"
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/sandbox/types"
	CubeLog "github.com/tencentcloud/CubeSandbox/cubelog"
)

// caRootDir is the host-side install location of the CubeEgress root
// CA on the master node — same path the data plane (CubeEgress nginx)
// reads, so the source of truth is single. compute nodes pull from
// here via /cube/ca/<filename>.
//
// Hardcoded by the same reasoning as design/cube-egress-ca-bake.md
// §D2: a per-deployment override would let the master serve one CA
// while the data plane uses another, silently breaking trust.
const caRootDir = "/etc/cube/ca"

// caServableFiles is the closed allow-list of filenames this endpoint
// will serve. The path traversal check below would also catch any
// attempt to escape /etc/cube/ca, but an explicit allow-list documents
// exactly what's exposed and keeps the policy in code rather than
// implicit in os.Open behavior.
var caServableFiles = map[string]struct{}{
	"cube-root-ca.crt": {},
	"cube-root-ca.key": {},
}

<<<<<<< HEAD
// openCAFile resolves, opens, and stats the requested CA file and writes the
// common Content-Type/Length headers. On error it writes the API error
// response and returns ok=false. On success the caller owns file (must Close).
func openCAFile(c *gin.Context) (filename string, file *os.File, stat os.FileInfo, ok bool) {
	// The filename is a gin path param (:filename), so it is already a
	// single path segment; the allow-list below is the real boundary and
	// rejects anything not on the documented list.
	requested := c.Param("filename")
	if _, allow := caServableFiles[requested]; !allow {
		common.WriteAPI(c, &types.Res{
=======
// handleCADownloadAction serves the CubeEgress root CA materials
// (cube-root-ca.crt / cube-root-ca.key) to compute nodes that need to
// run their own CubeEgress instance against templates baked with the
// same CA.
//
// Path: /cube/ca/<filename>. Other names → 404.
//
// Auth: NONE today. Anyone reachable on the master HTTP port can pull
// the MITM private key. Acceptable iff the master HTTP port is
// reachable only from inside the cluster network (the typical
// one-click deployment). Production hardening — bearer token in a
// header, request-source ACL, or mTLS — should land before this
// endpoint is exposed to anything wider; a verifyAuth(r) hook is the
// natural place to add it.
func handleCADownloadAction(w http.ResponseWriter, r *http.Request, rt *CubeLog.RequestTrace) interface{} {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		return &types.Res{
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_MasterParamsError),
				RetMsg:  http.StatusText(http.StatusMethodNotAllowed),
			},
		}
	}

	// Trim and basename so a malicious caller can't request
	// /cube/ca/../../etc/passwd. filepath.Base alone normalises any
	// traversal attempt to a single path element; the allow-list then
	// rejects anything not on the documented list.
	requested := filepath.Base(filepath.Clean(r.URL.Path))
	if _, ok := caServableFiles[requested]; !ok {
		return &types.Res{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
			Ret: &types.Ret{
				RetCode: int(errorcode.ErrorCode_NotFound),
				RetMsg:  http.StatusText(http.StatusNotFound),
			},
<<<<<<< HEAD
		})
		return "", nil, nil, false
=======
		}
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	}

	fullPath := filepath.Join(caRootDir, requested)
	f, err := os.Open(fullPath) // #nosec G304 — file name is from a closed allow-list
	if err != nil {
		// If the master itself doesn't have the CA on disk this is an
		// operational misconfiguration, not a client error. Surfacing
		// 404 rather than 500 keeps the failure noisy on the puller's
		// side (it can choose to retry later, e.g. while the operator
		// runs cube-egress-prepare.sh on the master).
		retCode := int(errorcode.ErrorCode_MasterInternalError)
		if errors.Is(err, os.ErrNotExist) {
			retCode = int(errorcode.ErrorCode_NotFound)
		}
<<<<<<< HEAD
		common.WriteAPI(c, &types.Res{
=======
		return &types.Res{
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
			Ret: &types.Ret{
				RetCode: retCode,
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
	defer f.Close()

	stat, err := f.Stat()
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
	return requested, f, st, true
}

// downloadCAGinHandler (GET) and headCAGinHandler (HEAD) serve the CubeEgress
// root CA materials (cube-root-ca.crt / cube-root-ca.key) to compute nodes
// that need to run their own CubeEgress instance against templates baked with
// the same CA.
//
// Path: /cube/ca/<filename>. Other names → 404.
//
// Auth: NONE today. Anyone reachable on the master HTTP port can pull
// the MITM private key. Acceptable iff the master HTTP port is
// reachable only from inside the cluster network (the typical
// one-click deployments). Production hardening — bearer token in a
// header, request-source ACL, or mTLS — should land before this
// endpoint is exposed to anything wider; a verifyAuth(r) hook is the
// natural place to add it.
func downloadCAGinHandler(c *gin.Context) {
	rt := CubeLog.GetTraceInfo(c.Request.Context())
	requested, f, stat, ok := openCAFile(c)
	if !ok {
		return
	}
	defer f.Close()
=======
		}
	}

	w.Header().Set("Content-Type", "application/octet-stream")
	w.Header().Set("Content-Length", strconv.FormatInt(stat.Size(), 10))
	if r.Method == http.MethodHead {
		rt.RetCode = int64(errorcode.ErrorCode_Success)
		return nil
	}
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	// http.ServeContent gives us range support for free; the CA files
	// are tiny (sub-kilobyte) so range isn't load-bearing, but it
	// keeps the response handling consistent with the artifact
	// download endpoint.
<<<<<<< HEAD
	http.ServeContent(c.Writer, c.Request, requested, stat.ModTime(), f)
	rt.RetCode = int64(errorcode.ErrorCode_Success)
}

func headCAGinHandler(c *gin.Context) {
	rt := CubeLog.GetTraceInfo(c.Request.Context())
	_, f, _, ok := openCAFile(c)
	if !ok {
		return
	}
	f.Close()
	rt.RetCode = int64(errorcode.ErrorCode_Success)
=======
	http.ServeContent(w, r, requested, stat.ModTime(), f)
	rt.RetCode = int64(errorcode.ErrorCode_Success)
	return nil
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
}
