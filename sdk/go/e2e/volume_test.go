// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0

package e2e

import (
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"net/http"
	"net/url"
	"os"
	"path"
	"strings"
	"testing"
	"time"

	cubesandbox "github.com/tencentcloud/CubeSandbox/sdk/go"
)

func requiredEnv(t *testing.T, name string) string {
	t.Helper()
	value := os.Getenv(name)
	if value == "" || strings.ContainsAny(value, "<>\r\n") {
		t.Fatalf("set %s through the environment", name)
	}
	return value
}

func randomID(t *testing.T) string {
	t.Helper()
	var data [16]byte
	if _, err := rand.Read(data[:]); err != nil {
		t.Fatal("cannot generate a random probe ID")
	}
	return hex.EncodeToString(data[:])
}

type volumeOnlyTransport struct {
	base   http.RoundTripper
	prefix string
	host   string
	token  string
}

func (v volumeOnlyTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if req.Method != http.MethodGet || req.URL.Host != v.host || !strings.HasPrefix(req.URL.Path, v.prefix) {
		return nil, errors.New("direct-volume test attempted a non-volume or mutating request")
	}
	if req.Header.Get("Authorization") != "Bearer "+v.token || req.Header.Get("X-API-Key") != "" || req.Header.Get("X-Access-Token") != "" {
		return nil, errors.New("content request must use only the volume bearer")
	}
	return v.base.RoundTrip(req)
}

func newContent(t *testing.T, endpoint, volumeID, token string) *cubesandbox.VolumeContent {
	t.Helper()
	u, err := url.Parse(endpoint)
	if err != nil {
		t.Fatal("invalid content gateway URL")
	}
	base := http.DefaultTransport.(*http.Transport).Clone()
	base.Proxy = nil
	t.Cleanup(base.CloseIdleConnections)
	transport := volumeOnlyTransport{base: base, host: u.Host, token: token, prefix: strings.TrimRight(u.Path, "/") + "/volumecontent/" + volumeID + "/"}
	client := cubesandbox.NewClient(cubesandbox.Config{VolumeAPIURL: endpoint, RequestTimeout: 30 * time.Second}, cubesandbox.WithHTTPClient(&http.Client{Transport: transport}))
	t.Cleanup(func() { _ = client.Close() })
	content, err := client.VolumeContent(&cubesandbox.VolumeInfo{VolumeID: volumeID, Token: token})
	if err != nil {
		t.Fatal("invalid content gateway, volume ID or token; HTTPS is required outside loopback")
	}
	return content
}

func readChecks(t *testing.T, ctx context.Context, content *cubesandbox.VolumeContent, filePath, digest string) {
	t.Helper()
	data, err := content.ReadFile(ctx, filePath)
	if err != nil {
		t.Fatal("buffered volume read failed; check the gateway and scoped token")
	}
	want := sha256.Sum256(data)
	if digest != "" && hex.EncodeToString(want[:]) != strings.ToLower(digest) {
		t.Fatal("buffered read SHA-256 does not match expected data")
	}
	stream, err := content.ReadFileStream(ctx, filePath)
	if err != nil {
		t.Fatal("stream volume read failed")
	}
	hash := sha256.New()
	_, readErr := io.Copy(hash, stream)
	closeErr := stream.Close()
	if readErr != nil || closeErr != nil || hex.EncodeToString(hash.Sum(nil)) != hex.EncodeToString(want[:]) {
		t.Fatal("stream read failed or differs from the buffered read")
	}
	info, err := content.GetInfo(ctx, filePath)
	if err != nil || info.Type != "file" || info.Size != int64(len(data)) || info.Path != filePath {
		t.Fatal("volume file metadata does not match the read")
	}
	depth := 1
	entries, err := content.List(ctx, path.Dir(filePath), &depth)
	if err != nil {
		t.Fatal("volume directory listing failed")
	}
	found := false
	for _, entry := range entries {
		found = found || entry.Path == filePath
	}
	if !found {
		t.Fatal("directory listing does not contain the test file")
	}
	if exists, err := content.Exists(ctx, filePath); err != nil || !exists {
		t.Fatal("existing volume path was not found")
	}
	missing := path.Join(path.Dir(filePath), "missing-"+randomID(t))
	if exists, err := content.Exists(ctx, missing); err != nil || exists {
		t.Fatal("missing path must return false without hiding other errors")
	}
	if _, err := content.ReadFile(ctx, missing); !errors.Is(err, cubesandbox.ErrVolumePathNotFound) {
		t.Fatal("missing file must return the typed path-not-found error")
	}
	stream, err = content.ReadFileStream(ctx, filePath)
	if err != nil {
		t.Fatal("cannot open a stream for early-close verification")
	}
	_, readErr = stream.Read(make([]byte, 1))
	closeErr = stream.Close()
	if (readErr != nil && readErr != io.EOF) || closeErr != nil {
		t.Fatal("early stream close failed")
	}
	if exists, err := content.Exists(ctx, filePath); err != nil || !exists {
		t.Fatal("read after an early stream close failed")
	}
	t.Logf("bytes/stream SHA-256=%x; metadata/list/exists/404/early-close passed", want)
}

func TestVolumeGatewayReadOnly(t *testing.T) {
	if testing.Short() || os.Getenv("CUBE_VOLUME_GATEWAY_E2E") != "1" {
		t.Skip("requires CUBE_VOLUME_GATEWAY_E2E=1 and a deployed content gateway")
	}
	endpoint := requiredEnv(t, "CUBE_VOLUME_API_URL")
	volumeID := requiredEnv(t, "CUBE_VOLUME_ID")
	token := requiredEnv(t, "CUBE_VOLUME_CONTENT_TOKEN")
	filePath := requiredEnv(t, "CUBE_VOLUME_TEST_PATH")
	digest := os.Getenv("CUBE_VOLUME_TEST_SHA256")
	if digest != "" {
		decoded, err := hex.DecodeString(digest)
		if err != nil || len(decoded) != sha256.Size {
			t.Fatal("CUBE_VOLUME_TEST_SHA256 must be a 64-character hex digest")
		}
	}
	content := newContent(t, endpoint, volumeID, token)
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	readChecks(t, ctx, content, filePath, digest)
	invalid := newContent(t, endpoint, volumeID, "invalid-"+randomID(t))
	if _, err := invalid.Exists(ctx, filePath); !errors.Is(err, cubesandbox.ErrAuthentication) {
		t.Fatal("gateway must reject invalid tokens with 401/403, not report a missing path")
	}
	t.Log("deployed gateway read-only checks passed; server-side isolation and leases are not certified")
}
