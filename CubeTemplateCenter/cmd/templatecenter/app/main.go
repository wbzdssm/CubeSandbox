// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

// Package app provides the main entry point for the template center service.
package app

import (
	"context"
	"fmt"
	stdlog "log"
	"os"
	"os/signal"
	"time"

	"github.com/tencentcloud/CubeSandbox/CubeDB/dao"
	_ "github.com/tencentcloud/CubeSandbox/CubeDB/dao/driver/mysql"    // register mysql driver
	_ "github.com/tencentcloud/CubeSandbox/CubeDB/dao/driver/postgres" // register postgres driver
	"github.com/tencentcloud/CubeSandbox/CubeDB/migrate"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/config"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/log"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/recov"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/errorcode"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/localcache"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/nodemeta"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/templatecenter"
	"github.com/tencentcloud/CubeSandbox/CubeTemplateCenter/pkg/httpservice"
	CubeLog "github.com/tencentcloud/CubeSandbox/cubelog"
)

// App is the template center application.
type App struct{}

// New constructs a new App.
func New() *App {
	return &App{}
}

// Run boots the template center process.
func (a *App) Run() {
	var (
		start   = time.Now()
		signals = make(chan os.Signal, 2048)
	)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	cfg := config.GetConfig()

	if err := coreInit(ctx, cfg); err != nil {
		stdlog.Fatalf("core init fail:%v", recov.DumpStacktrace(3, err))
		return
	}

	srv, err := httpservice.New(ctx, cfg)
	if err != nil {
		CubeLog.WithContext(ctx).Errorf("templatecenter http server init:%v", err)
		return
	}

	done := handleSignals(ctx, signals, cancel)
	signal.Notify(signals, handledSignals...)

	recov.GoWithRecover(func() {
		srv.Run()
	})

	CubeLog.WithContext(ctx).Errorf("templatecenter successfully booted in %fs", time.Since(start).Seconds())
	<-done

	// Graceful shutdown: drain in-flight requests up to 1 minute.
	srv.Stop()
}

// coreInit wires the minimum set of dependencies the template center needs.
// It intentionally skips CubeMaster-only services (scheduler/sandbox/lifecycle)
// because template center does not own sandbox creation.
func coreInit(ctx context.Context, cfg *config.Config) error {
	log.Init(config.GetLogConfig())

	errorcode.InitCubeCodeRetryMap(cfg)

	if cfg.InstanceDBConfig == nil {
		return fmt.Errorf("instance_db_config is required for template center")
	}

	// Schema migration (same GET_LOCK-protected path as CubeMaster).
	if err := initDatabaseSchema(ctx, cfg); err != nil {
		return fmt.Errorf("dao migrate: %w", err)
	}

	// Template center needs the node view for distribution target selection.
	if err := nodemeta.Init(ctx); err != nil {
		return fmt.Errorf("nodemeta init: %w", err)
	}

	// localcache holds imageCache + templateDefinitionCache.
	if err := localcache.Init(ctx); err != nil {
		return fmt.Errorf("localcache init: %w", err)
	}

	// Templatecenter is the business core: pulls image, builds ext4, runs
	// fingerprint, writes rootfs_artifacts + image_jobs + template_definitions.
	// Use InitForTemplateCenter (not Init) so snapshot-side hooks
	// (sandbox.SetAfterDestroySandboxSuccessHook, sandboxspec hooks, snapshot
	// reconciler) are NOT registered — those belong to CubeMaster.
	if err := templatecenter.InitForTemplateCenter(ctx); err != nil {
		return fmt.Errorf("templatecenter init: %w", err)
	}

	return nil
}

// initDatabaseSchema opens the canonical dao handle and runs pending
// migrations. Mirrors CubeMaster's behavior so the two processes share
// schema ownership.
func initDatabaseSchema(ctx context.Context, cfg *config.Config) error {
	src := cfg.InstanceDBConfig
	if src == nil {
		return fmt.Errorf("dao: instance_db_config is not set")
	}
	daoCfg := dao.Config{
		Driver:                      src.Driver,
		Addr:                        src.Addr,
		User:                        src.User,
		Pwd:                         src.Pwd,
		DBName:                      src.DBName,
		ConnTimeoutSeconds:          src.ConnTimeout,
		ReadTimeoutSeconds:          src.ReadTimeout,
		WriteTimeoutSeconds:         src.WriteTimeout,
		MaxIdleConns:                src.MaxIdleConns,
		MaxOpenConns:                src.MaxOpenConns,
		MaxConnLifeTimeSeconds:      src.MaxConnLifeTimeSeconds,
		MigrationLockTimeoutSeconds: src.MigrationLockTimeoutSeconds,
	}
	if _, err := dao.Open(ctx, daoCfg); err != nil {
		return fmt.Errorf("dao open: %w", err)
	}
	if !migrate.AutoMigrationEnabled() {
		CubeLog.WithContext(ctx).Warnf(
			"CUBE_AUTO_MIGRATION=false: skipping schema migration; DDL must be " +
				"applied out-of-band by a privileged account")
		return nil
	}
	if err := dao.Migrate(ctx); err != nil {
		return fmt.Errorf("dao migrate: %w", err)
	}
	CubeLog.WithContext(ctx).Infof("dao schema migration completed (driver=%s db=%s)",
		daoCfg.Driver, daoCfg.DBName)
	return nil
}
