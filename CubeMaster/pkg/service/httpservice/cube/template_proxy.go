// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package cube

import (
	"fmt"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/config"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/log"
)

// Template route modes. See config.CommonConf.TemplateRouteMode.
const (
	templateRouteModeLocal = "local"
	templateRouteModeProxy = "proxy"
)

// templateProxyTimeout bounds a single proxied request. Template creation
// returns as soon as the job row is written (the build is async), so this only
// needs to cover request handling -- not a multi-minute build. The artifact
// download endpoint is deliberately NOT proxied (see RegisterCubeRoutes), so
// no multi-GB transfer flows through here.
const templateProxyTimeout = 60 * time.Second

var (
	templateProxyOnce sync.Once
	templateProxy     *httputil.ReverseProxy
	templateProxyErr  error
)

// templateRouteProxyEnabled reports whether the public template endpoints
// should be reverse-proxied to CubeTemplateCenter instead of being served
// in-process.
func templateRouteProxyEnabled() bool {
	cfg := config.GetConfig()
	if cfg == nil || cfg.Common == nil {
		return false
	}
	return strings.EqualFold(strings.TrimSpace(cfg.Common.TemplateRouteMode), templateRouteModeProxy)
}

// templateProxyHandler forwards a template request to CubeTemplateCenter and
// returns its response verbatim, so callers cannot tell who served them.
//
// This is the mechanism that lets template ownership move to
// CubeTemplateCenter without changing a single caller: SDKs and cubemastercli
// keep hitting CubeMaster's /cube/template* endpoints.
func templateProxyHandler(c *gin.Context) {
	proxy, err := getTemplateProxy()
	if err != nil {
		log.G(c.Request.Context()).Errorf("template proxy unavailable: %v", err)
		c.JSON(http.StatusServiceUnavailable, gin.H{
			"error": "template_route_mode=proxy but the template center endpoint is unusable: " + err.Error(),
		})
		return
	}
	proxy.ServeHTTP(c.Writer, c.Request)
}

// getTemplateProxy builds the reverse proxy once and caches it. The endpoint is
// read at first use rather than at init time because config load order is not
// guaranteed relative to route registration.
func getTemplateProxy() (*httputil.ReverseProxy, error) {
	templateProxyOnce.Do(func() {
		cfg := config.GetConfig()
		raw := ""
		if cfg != nil && cfg.Common != nil {
			raw = strings.TrimSpace(cfg.Common.TemplateCenterEndpoint)
		}
		if raw == "" {
			templateProxyErr = fmt.Errorf("template_center_endpoint is empty")
			return
		}
		if !strings.Contains(raw, "://") {
			raw = "http://" + raw
		}
		target, err := url.Parse(raw)
		if err != nil {
			templateProxyErr = fmt.Errorf("parse template_center_endpoint %q: %w", raw, err)
			return
		}
		if target.Host == "" {
			templateProxyErr = fmt.Errorf("template_center_endpoint %q has no host", raw)
			return
		}

		templateProxy = &httputil.ReverseProxy{
			Director: func(req *http.Request) {
				req.URL.Scheme = target.Scheme
				req.URL.Host = target.Host
				// Preserve the original path: CubeTemplateCenter serves the
				// same /cube/template* routes, so this is a pure host swap.
				//
				// Host is rewritten to the target as well; keeping the client's
				// Host header would break vhost-based routing in front of TC.
				req.Host = target.Host
				// Record who forwarded, for traceability in TC's access log.
				req.Header.Set("X-Forwarded-By", "cubemaster-template-proxy")
			},
			Transport: &http.Transport{
				DialContext: (&net.Dialer{
					Timeout:   5 * time.Second,
					KeepAlive: 30 * time.Second,
				}).DialContext,
				ResponseHeaderTimeout: templateProxyTimeout,
				IdleConnTimeout:       90 * time.Second,
				MaxIdleConnsPerHost:   8,
			},
			ErrorHandler: func(w http.ResponseWriter, r *http.Request, err error) {
				log.G(r.Context()).Errorf("template proxy to %s failed: method=%s path=%s err=%v",
					target.Host, r.Method, r.URL.Path, err)
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusBadGateway)
				_, _ = w.Write([]byte(`{"error":"template center unreachable"}`))
			},
		}
	})
	if templateProxyErr != nil {
		return nil, templateProxyErr
	}
	return templateProxy, nil
}
