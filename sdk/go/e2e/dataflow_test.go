// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0

package e2e

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"io"
	"os"
	"strings"
	"testing"
	"time"

	cubesandbox "github.com/tencentcloud/CubeSandbox/sdk/go"
)

const maxDataflowBytes = 64 * 1024 * 1024

type readOnlyStore interface {
	open(context.Context, string) (io.ReadCloser, error)
}

type objectDigest struct {
	size int64
	hash [sha256.Size]byte
}

func dataflowObjectKey(volumeID, filePath string) (string, error) {
	if !safeVolumeID.MatchString(volumeID) || !strings.HasPrefix(filePath, "/") || strings.ContainsAny(filePath, "\x00\\\r\n") {
		return "", errors.New("invalid volume ID or absolute file path")
	}
	for _, segment := range strings.Split(filePath[1:], "/") {
		if segment == "" || segment == "." || segment == ".." {
			return "", errors.New("test file path must not contain empty or dot segments")
		}
	}
	return "volumes/" + volumeID + filePath, nil
}

func digestObject(ctx context.Context, store readOnlyStore, key string, limit int64) (objectDigest, error) {
	body, err := store.open(ctx, key)
	if err != nil {
		return objectDigest{}, errors.New("cannot open the existing backend object")
	}
	hash := sha256.New()
	size, readErr := io.Copy(hash, io.LimitReader(body, limit+1))
	closeErr := body.Close()
	if readErr != nil || closeErr != nil {
		return objectDigest{}, errors.New("backend object stream failed or could not be closed")
	}
	if size > limit {
		return objectDigest{}, errors.New("test object exceeds the dataflow size limit")
	}
	result := objectDigest{size: size}
	copy(result.hash[:], hash.Sum(nil))
	return result, nil
}

func runRealDataflow(t *testing.T, backend string) {
	t.Helper()
	if testing.Short() || os.Getenv("CUBE_VOLUME_DATAFLOW_E2E") != "1" {
		t.Skip("requires CUBE_VOLUME_DATAFLOW_E2E=1, real storage and a deployed content gateway; no shim")
	}
	values := loadStorageSettings(t, backend)
	prefix := "CUBE_VOLUME_" + strings.ToUpper(backend) + "_"
	endpoint := requiredEnv(t, prefix+"API_URL")
	token := requiredEnv(t, prefix+"CONTENT_TOKEN")
	filePath := requiredEnv(t, prefix+"TEST_PATH")
	key, err := dataflowObjectKey(values["VOLUME_ID"], filePath)
	if err != nil {
		t.Fatal(err)
	}
	expected := os.Getenv(prefix + "TEST_SHA256")
	if expected != "" {
		decoded, err := hex.DecodeString(expected)
		if err != nil || len(decoded) != sha256.Size {
			t.Fatalf("%sTEST_SHA256 must be a 64-character hex digest", prefix)
		}
	}
	content := newContent(t, endpoint, values["VOLUME_ID"], token)
	store := makeStore(t, backend, values)
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()

	before, err := digestObject(ctx, store, key, maxDataflowBytes)
	if err != nil {
		t.Fatal(err)
	}
	if before.size == 0 {
		t.Fatal("select a non-empty stable file, not a volume directory marker")
	}
	digest := hex.EncodeToString(before.hash[:])
	if expected != "" && digest != strings.ToLower(expected) {
		t.Fatal("direct backend SHA-256 does not match the supplied expected digest")
	}
	t.Logf("source=%s object=%s bytes=%d SHA-256=%s", backend, key, before.size, digest)

	info, err := content.GetInfo(ctx, filePath)
	if err != nil {
		var apiErr *cubesandbox.APIError
		if errors.As(err, &apiErr) {
			t.Fatalf("real gateway stat failed (HTTP %d); check /volumecontent support and a gateway-issued token; no shim fallback", apiErr.StatusCode)
		}
		t.Fatal("real gateway stat failed; check endpoint, TLS and connectivity; no shim fallback")
	}
	if info.Type != "file" || info.Path != filePath || info.Size != before.size {
		t.Fatal("gateway metadata does not match the real backend object")
	}
	readChecks(t, ctx, content, filePath, digest)
	invalid := newContent(t, endpoint, values["VOLUME_ID"], "invalid-"+randomID(t))
	if _, err := invalid.Exists(ctx, filePath); !errors.Is(err, cubesandbox.ErrAuthentication) {
		t.Fatal("real gateway must reject invalid tokens with 401/403")
	}

	after, err := digestObject(ctx, store, key, maxDataflowBytes)
	if err != nil {
		t.Fatal(err)
	}
	if before != after {
		t.Fatal("backend object changed during validation; use a stable file")
	}
	t.Log("PASS: direct backend == Go SDK bytes == Go SDK stream; existing data unchanged; external gateway, no test shim")
}

func TestVolumeDataflowMinIO(t *testing.T) {
	runRealDataflow(t, "minio")
}

func TestVolumeDataflowCOS(t *testing.T) {
	runRealDataflow(t, "cos")
}

func TestDataflowObjectKey(t *testing.T) {
	for _, filePath := range []string{"relative", "/", "//data", "/data/", "/a/../b", "/a/./b", "/nul\x00", "/back\\slash", "/line\n"} {
		if _, err := dataflowObjectKey("test-volume", filePath); err == nil {
			t.Errorf("accepted unsafe test path %q", filePath)
		}
	}
	if _, err := dataflowObjectKey("../other", "/file"); err == nil {
		t.Error("accepted an unsafe volume ID")
	}
	key, err := dataflowObjectKey("test-volume", "/a space/#中文.bin")
	if err != nil || key != "volumes/test-volume/a space/#中文.bin" {
		t.Fatal("dataflow key mapping must preserve the exact file path")
	}
}

func TestDataflowDigestFullObject(t *testing.T) {
	payload := strings.Repeat("full-object-beyond-probe-size-", 4096)
	store := &memoryStore{objects: map[string][]byte{"existing": []byte(payload)}}
	ctx := context.Background()
	result, err := digestObject(ctx, store, "existing", int64(len(payload)))
	if err != nil || result.size != int64(len(payload)) || result.hash != sha256.Sum256([]byte(payload)) {
		t.Fatal("dataflow must hash the entire object, not the small probe prefix")
	}
	if _, err := digestObject(ctx, store, "existing", int64(len(payload))-1); err == nil {
		t.Fatal("oversized objects must fail rather than validate a truncated prefix")
	}
	if _, err := digestObject(ctx, store, "missing", int64(len(payload))); err == nil {
		t.Fatal("missing source object must fail")
	}
	if store.removed || len(store.objects) != 1 || string(store.objects["existing"]) != payload {
		t.Fatal("dataflow digest must never modify the source object")
	}
}
