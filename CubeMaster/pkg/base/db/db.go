// Copyright (c) 2024 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

// Package db provides database access.
package db

import (
<<<<<<< HEAD
	"github.com/tencentcloud/CubeSandbox/CubeDB/dao"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/config"
=======
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/config"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/dao"
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
	"gorm.io/gorm"
)

// Init returns the global dao handle. Retained for backwards compatibility
// with v0.2.2-era callers (nodemeta / localcache / instancecache /
// templatecenter) that still pass a DBConfig. cfg is accepted but ignored —
// the connection is already established by dao.Open before any business
// package Init runs.
func Init(cfg *config.DBConfig) *gorm.DB {
	_ = cfg
	return dao.Default()
}
