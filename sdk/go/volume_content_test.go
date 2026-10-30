// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0

package cubesandbox

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

const volumeContentTestToken = "volume-content-secret"
const volumeContentTestEntry = `{"name":"data.txt","type":"file","path":"/data.txt","size":3,"mode":420,"uid":0,"gid":0,"atime":"2026-10-30T01:02:03Z","mtime":"2026-10-30T01:02:03Z","ctime":"2026-10-30T01:02:03Z"}`

type volumeContentTransport func(*http.Request) (*http.Response, error)

func (f volumeContentTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	return f(req)
}

func newVolumeContentForTest(t *testing.T, endpoint string, opts ...ClientOption) *VolumeContent {
	t.Helper()
	client := NewClient(Config{VolumeAPIURL: endpoint, APIKey: "management-secret"}, opts...)
	t.Cleanup(func() { _ = client.Close() })
	content, err := client.VolumeContent(&VolumeInfo{VolumeID: "vol-test", Token: volumeContentTestToken})
	if err != nil {
		t.Fatal(err)
	}
	return content
}

func TestVolumeContentMasksToken(t *testing.T) {
	content := newVolumeContentForTest(t, "https://content.test")
	for _, value := range []any{content, *content} {
		for _, format := range []string{"%v", "%+v", "%#v"} {
			text := fmt.Sprintf(format, value)
			if strings.Contains(text, volumeContentTestToken) || !strings.Contains(text, "***") {
				t.Errorf("format %s must mask the token", format)
			}
		}
	}
}

type volumeContentOwnedReader struct {
	io.Reader
	closed bool
}

func (r *volumeContentOwnedReader) Close() error {
	r.closed = true
	return nil
}

func TestVolumeContentWritePreservesCallerReader(t *testing.T) {
	transportErr := errors.New("transport failed")
	for _, outcome := range []string{"success", "http-error", "transport-error"} {
		t.Run(outcome, func(t *testing.T) {
			calls := 0
			transport := volumeContentTransport(func(req *http.Request) (*http.Response, error) {
				calls++
				body, err := io.ReadAll(req.Body)
				if err != nil || string(body) != "payload" {
					t.Errorf("unexpected upload body: %q, %v", body, err)
				}
				_ = req.Body.Close()
				if outcome == "transport-error" {
					return nil, transportErr
				}
				status, response := http.StatusOK, volumeContentTestEntry
				if outcome == "http-error" {
					status, response = http.StatusForbidden, `{"message":"forbidden"}`
				}
				return &http.Response{StatusCode: status, Header: make(http.Header), Body: io.NopCloser(strings.NewReader(response)), Request: req}, nil
			})
			content := newVolumeContentForTest(t, "https://content.test", WithHTTPClient(&http.Client{Transport: transport}))
			reader := &volumeContentOwnedReader{Reader: strings.NewReader("payload")}
			_, err := content.WriteFile(context.Background(), "/data.txt", reader, VolumeMetadataOptions{})
			if (outcome == "success") != (err == nil) {
				t.Fatalf("unexpected upload result: %v", err)
			}
			if outcome == "http-error" && !errors.Is(err, ErrAuthentication) {
				t.Errorf("expected authentication error, got %v", err)
			}
			if outcome == "transport-error" && !errors.Is(err, transportErr) {
				t.Errorf("expected transport error, got %v", err)
			}
			if reader.closed {
				t.Error("upload closed caller-owned reader")
			}
			if calls != 1 {
				t.Errorf("upload retried: %d calls", calls)
			}
		})
	}
}

func TestVolumeContentDirectOperations(t *testing.T) {
	path := "/a space/#中文.txt"
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if req.Header.Get("Authorization") != "Bearer "+volumeContentTestToken || req.Header.Get("X-API-Key") != "" || req.Header.Get("X-Access-Token") != "" {
			t.Error("content request must use only the volume bearer")
		}
		if req.URL.Query().Get("path") != path {
			t.Errorf("path was not preserved: %q", req.URL.Query().Get("path"))
		}
		switch req.Method + " " + req.URL.Path {
		case "GET /gateway/volumecontent/vol-test/file":
			_, _ = io.WriteString(w, "payload")
			return
		case "PUT /gateway/volumecontent/vol-test/file":
			body, err := io.ReadAll(req.Body)
			if err != nil || string(body) != "payload" || req.Header.Get("Content-Type") != "application/octet-stream" {
				t.Error("invalid upload")
			}
			fallthrough
		case "POST /gateway/volumecontent/vol-test/dir":
			for key, want := range map[string]string{"uid": "0", "gid": "0", "mode": "0", "force": "false"} {
				if req.URL.Query().Get(key) != want {
					t.Errorf("option %s was not preserved", key)
				}
			}
		case "GET /gateway/volumecontent/vol-test/dir":
			if req.URL.Query().Get("depth") != "0" {
				t.Error("depth=0 was not preserved")
			}
			_, _ = io.WriteString(w, "["+volumeContentTestEntry+"]")
			return
		case "PATCH /gateway/volumecontent/vol-test/path":
			body, _ := io.ReadAll(req.Body)
			if string(body) != `{"mode":0,"uid":0}` || req.Header.Get("Content-Type") != "application/json" {
				t.Errorf("invalid metadata update: %s", body)
			}
		case "GET /gateway/volumecontent/vol-test/path":
		case "DELETE /gateway/volumecontent/vol-test/path":
			w.WriteHeader(http.StatusNoContent)
			return
		default:
			t.Errorf("unexpected route: %s %s", req.Method, req.URL.Path)
		}
		_, _ = io.WriteString(w, volumeContentTestEntry)
	}))
	defer server.Close()
	content := newVolumeContentForTest(t, server.URL+"/gateway")
	ctx := context.Background()
	zero, force, depth := int64(0), false, 0
	opts := VolumeMetadataOptions{UID: &zero, GID: &zero, Mode: &zero, Force: &force}
	if data, err := content.ReadFile(ctx, path); err != nil || string(data) != "payload" {
		t.Fatalf("read: %q, %v", data, err)
	}
	if _, err := content.WriteFile(ctx, path, strings.NewReader("payload"), opts); err != nil {
		t.Fatal(err)
	}
	if _, err := content.MakeDir(ctx, path, opts); err != nil {
		t.Fatal(err)
	}
	if entries, err := content.List(ctx, path, &depth); err != nil || len(entries) != 1 {
		t.Fatalf("list: %v, %v", entries, err)
	}
	if _, err := content.UpdateMetadata(ctx, path, VolumeMetadataOptions{UID: &zero, Mode: &zero}); err != nil {
		t.Fatal(err)
	}
	if exists, err := content.Exists(ctx, path); err != nil || !exists {
		t.Fatalf("exists: %v, %v", exists, err)
	}
	if err := content.Remove(ctx, path); err != nil {
		t.Fatal(err)
	}
}

func TestVolumeContentErrorMappingAndRedirects(t *testing.T) {
	for _, tc := range []struct {
		name   string
		status int
		body   string
		want   error
	}{
		{"missing-path", 404, `{"code":"path_not_found","message":"missing"}`, nil},
		{"missing-gateway", 404, "404 page not found", ErrVolumeContentUnavailable},
		{"forbidden", 403, `{"message":"volume-content-secret"}`, ErrAuthentication},
		{"unavailable", 503, `{}`, ErrVolumeContentUnavailable},
		{"redirect", 307, `{}`, nil},
	} {
		t.Run(tc.name, func(t *testing.T) {
			calls := 0
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
				calls++
				w.Header().Set("Location", "/must-not-follow")
				w.WriteHeader(tc.status)
				_, _ = io.WriteString(w, tc.body)
			}))
			defer server.Close()
			content := newVolumeContentForTest(t, server.URL)
			exists, err := content.Exists(context.Background(), "/missing")
			if tc.status == http.StatusTemporaryRedirect {
				var apiErr *APIError
				if exists || !errors.As(err, &apiErr) || apiErr.StatusCode != tc.status || apiErr.Kind != apiErrorKindAPI {
					t.Errorf("expected redirect API error, got %v", err)
				}
			} else if exists || !errors.Is(err, tc.want) {
				t.Errorf("exists=%v, error=%v, want=%v", exists, err, tc.want)
			}
			if err != nil && strings.Contains(err.Error(), volumeContentTestToken) {
				t.Error("error exposed token")
			}
			if calls != 1 {
				t.Errorf("unexpected retry or redirect: %d calls", calls)
			}
		})
	}
}
