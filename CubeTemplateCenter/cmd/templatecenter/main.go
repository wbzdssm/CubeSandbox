// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package main

import (
	"flag"
	stdlog "log"
	"runtime/debug"

	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/config"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/recov"
	"github.com/tencentcloud/CubeSandbox/CubeMaster/pkg/base/version"
	"github.com/tencentcloud/CubeSandbox/CubeTemplateCenter/cmd/templatecenter/app"
)

var (
	versionFlag     = flag.Bool("v", false, "show version")
	longVersionFlag = flag.Bool("version", false, "show version")
)

func main() {
	flag.Parse()
	if *versionFlag || *longVersionFlag {
		version.ShowAndExit(true)
	}

	debug.SetGCPercent(90)
	a := app.New()

	if _, err := config.Init(); err != nil {
		stdlog.Fatalf("config init fail:%v", recov.DumpStacktrace(3, err))
		return
	}

	a.Run()
}
