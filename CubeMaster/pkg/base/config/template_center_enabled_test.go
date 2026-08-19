// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0
//

package config

import "testing"

// conf builds a Config carrying only the template-center fields under test.
func conf(enabled bool, buildMode, routeMode string) *Config {
	return &Config{Common: &CommonConf{
		TemplateCenterEnabled: enabled,
		TemplateBuildMode:     buildMode,
		TemplateRouteMode:     routeMode,
	}}
}

func TestTemplateCenterEnabled(t *testing.T) {
	cases := []struct {
		name string
		cfg  *Config
		want bool
	}{
		{name: "nil config is disabled", cfg: nil, want: false},
		{name: "nil common is disabled", cfg: &Config{}, want: false},
		// The unset zero value must behave exactly like an explicit false.
		{name: "unset key is disabled", cfg: conf(false, "", ""), want: false},
		{name: "explicit false is disabled", cfg: conf(false, "remote", "proxy"), want: false},
		{name: "explicit true is enabled", cfg: conf(true, "", ""), want: true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := tc.cfg.TemplateCenterEnabled(); got != tc.want {
				t.Fatalf("TemplateCenterEnabled() = %t, want %t", got, tc.want)
			}
		})
	}
}

func TestTemplateBuildRemoteRequiresMasterSwitch(t *testing.T) {
	cases := []struct {
		name string
		cfg  *Config
		want bool
	}{
		// The central guarantee: a leftover "remote" alone must NOT enable
		// anything. Only the combination of master switch + mode does.
		{name: "remote mode without switch stays local", cfg: conf(false, "remote", ""), want: false},
		{name: "unset mode with switch stays local", cfg: conf(true, "", ""), want: false},
		{name: "local mode with switch stays local", cfg: conf(true, "local", ""), want: false},
		// A typo must never silently enable remote builds.
		{name: "typo mode with switch stays local", cfg: conf(true, "remot", ""), want: false},
		{name: "switch and remote mode enable it", cfg: conf(true, "remote", ""), want: true},
		{name: "mode match is case insensitive", cfg: conf(true, "Remote", ""), want: true},
		{name: "mode match trims whitespace", cfg: conf(true, " remote ", ""), want: true},
		{name: "nil config is local", cfg: nil, want: false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := tc.cfg.TemplateBuildRemote(); got != tc.want {
				t.Fatalf("TemplateBuildRemote() = %t, want %t", got, tc.want)
			}
		})
	}
}

func TestTemplateRouteProxyRequiresMasterSwitch(t *testing.T) {
	cases := []struct {
		name string
		cfg  *Config
		want bool
	}{
		{name: "proxy mode without switch stays local", cfg: conf(false, "", "proxy"), want: false},
		{name: "unset mode with switch stays local", cfg: conf(true, "", ""), want: false},
		{name: "local mode with switch stays local", cfg: conf(true, "", "local"), want: false},
		{name: "typo mode with switch stays local", cfg: conf(true, "", "prox"), want: false},
		{name: "switch and proxy mode enable it", cfg: conf(true, "", "proxy"), want: true},
		{name: "nil config is local", cfg: nil, want: false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := tc.cfg.TemplateRouteProxy(); got != tc.want {
				t.Fatalf("TemplateRouteProxy() = %t, want %t", got, tc.want)
			}
		})
	}
}

// TemplateCenterRequired drives endpoint validation: it must only fire when a
// template path actually depends on TC, not merely because the master switch
// is on.
func TestTemplateCenterRequired(t *testing.T) {
	cases := []struct {
		name string
		cfg  *Config
		want bool
	}{
		{name: "disabled master switch is not required", cfg: conf(false, "remote", "proxy"), want: false},
		// Master switch on but no mode set: TC is not in the request path, so an
		// empty endpoint is still fine (this is the pure "installed but local"
		// one-click default).
		{name: "switch on, no mode is not required", cfg: conf(true, "", ""), want: false},
		{name: "remote build is required", cfg: conf(true, "remote", ""), want: true},
		{name: "proxy route is required", cfg: conf(true, "", "proxy"), want: true},
		{name: "nil config is not required", cfg: nil, want: false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := tc.cfg.TemplateCenterRequired(); got != tc.want {
				t.Fatalf("TemplateCenterRequired() = %t, want %t", got, tc.want)
			}
		})
	}
}
