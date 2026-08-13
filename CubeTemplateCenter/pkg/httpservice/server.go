// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

// Package httpservice exposes the template center HTTP API.
//
// Mirrors CubeMaster/pkg/server/server.go, but registers ONLY the routes
// that belong to the template center (see design §3.1 "templatecenter 管模板").
// Sandbox / snapshot / volume CRUD stay with CubeMaster.
package httpservice

import (
	"context"
	"net"
	"net/http"
	"os"
	"strconv"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/pkg/errors"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/config"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/log"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/recov"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/httpservice/cube"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/service/httpservice/middleware"
	CubeLog "github.com/tencentcloud/CubeSandbox/cubelog"
)

// Server wraps the internal HTTP server (gin engine + timeouts).
type Server struct {
	InternalHttpServer *internalHttp
}

// New constructs the template center HTTP server.
func New(ctx context.Context, cfg *config.Config) (*Server, error) {
	if cfg == nil || cfg.Common == nil {
		return nil, errors.New("config is nil")
	}
	s := &Server{}
	var err error
	s.InternalHttpServer, err = NewInternalHttp(ctx, cfg)
	if err != nil {
		return nil, err
	}

	config.AppendConfigWatcher(s)
	return s, nil
}

type internalHttp struct {
	*http.Server
	engine *gin.Engine
}

// newEngine builds the gin engine. Identical to CubeMaster/pkg/server/server.go
// — kept as a copy so TC does not need to import CubeMaster's server package
// (which would register CubeMaster-only routes).
func newEngine() *gin.Engine {
	gin.SetMode(gin.ReleaseMode)
	engine := gin.New()
	engine.RedirectTrailingSlash = false
	engine.HandleMethodNotAllowed = true
	engine.NoRoute(func(c *gin.Context) {
		http.NotFound(c.Writer, c.Request)
	})
	engine.NoMethod(func(c *gin.Context) {
		c.AbortWithStatus(http.StatusMethodNotAllowed)
	})
	return engine
}

// NewInternalHttp constructs the internal HTTP server with timeouts from cfg.
func NewInternalHttp(ctx context.Context, cfg *config.Config) (*internalHttp, error) {
	if cfg == nil || cfg.Common == nil {
		return nil, errors.New("config is nil")
	}

	engine := newEngine()
	s := &internalHttp{
		Server: &http.Server{
			Addr:         net.JoinHostPort(cfg.Common.HttpBind, strconv.Itoa(cfg.Common.HttpPort)),
			ReadTimeout:  time.Second * time.Duration(cfg.Common.ReadTimeout),
			WriteTimeout: time.Second * time.Duration(cfg.Common.WriteTimeout),
			IdleTimeout:  time.Second * time.Duration(cfg.Common.IdleTimeout),
			Handler:      engine,
		},
		engine: engine,
	}

	s.registerRoutes()
	return s, nil
}

// registerRoutes mounts the middleware and the template-center-owned routes.
//
// 5 点原则 §3.1: templatecenter 只挂模板相关路由. Sandbox / snapshot / volume
// stay with CubeMaster; this process must not expose them.
func (s *internalHttp) registerRoutes() {
	root := s.engine.Group("")
	root.Use(middleware.GinRequestMiddleware())
	root.GET("/metrics", gin.WrapH(promhttp.Handler()))

	// 镜像中心 12 端点 (design §4.1). cube.RegisterTemplateRoutes registers
	// only the template-related handlers under /cube/template* — see
	// CubeMaster/pkg/service/httpservice/cube/routes.go.
	cube.RegisterTemplateRoutes(root.Group(cube.CubeURI()))
}

func (s *internalHttp) Start() error {
	if err := s.ListenAndServe(); err != nil {
		if err == http.ErrServerClosed {
			return nil
		}
		return errors.WithStack(err)
	}
	return nil
}

// Run starts the HTTP server in a goroutine.
func (s *Server) Run() {
	if s.InternalHttpServer != nil {
		go func() {
			if err := s.InternalHttpServer.Start(); err != nil {
				CubeLog.Errorf("templatecenter ListenAndServe:%v", err)
			}
		}()
	}
}

// OnEvent handles config change notifications (log level updates).
func (s *Server) OnEvent(cfg *config.Config) {
	log.OnChangeConf(cfg.Log)
}

// Stop gracefully shuts down the HTTP server.
func (s *Server) Stop() {
	ppid := os.Getpid()
	CubeLog.Errorf("templatecenter stopped gracefully begin, pid %v", ppid)
	wg := sync.WaitGroup{}
	recov.GoWithWaitGroup(&wg, func() {
		if s.InternalHttpServer != nil {
			ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
			defer cancel()
			if err := s.InternalHttpServer.Shutdown(ctx); err != nil {
				CubeLog.Fatal("templatecenter InternalHttp Shutdown:", err)
			}
			select {
			case <-ctx.Done():
				CubeLog.Error("templatecenter InternalHttp Shutdown timeout")
			default:
				CubeLog.Error("templatecenter InternalHttp Shutdown succ")
			}
		}
	})
	wg.Wait()
}
