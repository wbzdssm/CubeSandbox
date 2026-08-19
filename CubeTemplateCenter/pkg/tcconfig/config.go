// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

// Package tcconfig holds CubeTemplateCenter's own feature switches.
//
// They live here rather than in CubeMaster's config struct because they are
// read by the TC process only, and because TC deliberately does NOT reuse
// CubeMaster's template_build_mode / template_route_mode values -- doing so
// would couple the two processes' rollout state together.
package tcconfig

import (
	"os"
	"strconv"
	"strings"
)

// envServePublicTemplateAPI turns the public template control-plane endpoints
// on inside TC.
//
// Default OFF, which is the current iteration's contract: CubeMaster owns the
// template API and TC only builds what CubeMaster tells it to
// (POST /tc/api/v1/build). Leaving it off also removes any chance of a shadow
// entry point -- if TC served /cube/template* while CubeMaster still owned the
// template flow, both processes would write the same tables concurrently.
//
// Turn it ON to:
//   - point cubemastercli straight at TC:
//     cubemastercli --address <tc-host> --port 8090 tpl create-from-image ...
//   - receive traffic from CubeMaster's template_route_mode=proxy
//   - preview the next iteration, where TC owns templates outright
//
// Enabling it makes TC run the FULL pipeline in-process, including artifact
// distribution, which is why it also forces the node view to be loaded
// (see cmd/templatecenter/app.coreInit).
const envServePublicTemplateAPI = "CUBE_TC_SERVE_TEMPLATE_API"

// ServePublicTemplateAPI reports whether TC should mount the public template
// control-plane routes. Accepts 1/t/T/true/TRUE/yes-style values via
// strconv.ParseBool; anything unparseable is treated as false.
func ServePublicTemplateAPI() bool {
	v, err := strconv.ParseBool(strings.TrimSpace(os.Getenv(envServePublicTemplateAPI)))
	return err == nil && v
}
