// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0

package e2e

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path"
	"regexp"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
	cubesandbox "github.com/tencentcloud/CubeSandbox/sdk/go"
	cos "github.com/tencentyun/cos-go-sdk-v5"
)

var probePayload = []byte("\x00\xffgo-volume-backend-probe-中文\n")
var safeVolumeID = regexp.MustCompile(`^[A-Za-z0-9_-]+$`)
var safeBucket = regexp.MustCompile(`^[a-z0-9][a-z0-9.-]*[a-z0-9]$`)
var safeRegion = regexp.MustCompile(`^[a-z0-9-]+$`)

type objectStore interface {
	put(context.Context, string) error
	open(context.Context, string) (io.ReadCloser, error)
	read(context.Context, string) ([]byte, error)
	remove(context.Context, string) error
}

type minioStore struct {
	client *minio.Client
	bucket string
}

func (s minioStore) put(ctx context.Context, key string) error {
	_, err := s.client.PutObject(ctx, s.bucket, key, bytes.NewReader(probePayload), int64(len(probePayload)), minio.PutObjectOptions{ContentType: "application/octet-stream"})
	return err
}

func (s minioStore) open(ctx context.Context, key string) (io.ReadCloser, error) {
	return s.client.GetObject(ctx, s.bucket, key, minio.GetObjectOptions{})
}

func (s minioStore) read(ctx context.Context, key string) ([]byte, error) {
	object, err := s.open(ctx, key)
	if err != nil {
		return nil, err
	}
	defer object.Close()
	return io.ReadAll(io.LimitReader(object, int64(len(probePayload))+1))
}

func (s minioStore) remove(ctx context.Context, key string) error {
	if err := s.client.RemoveObject(ctx, s.bucket, key, minio.RemoveObjectOptions{}); err != nil {
		return err
	}
	_, err := s.client.StatObject(ctx, s.bucket, key, minio.StatObjectOptions{})
	response := minio.ToErrorResponse(err)
	if response.StatusCode == http.StatusNotFound && (response.Code == "NoSuchKey" || response.Code == "NoSuchObject" || response.Code == "NotFound") {
		return nil
	}
	return errors.New("cannot confirm deletion of the probe object")
}

type cosStore struct {
	client *cos.Client
}

func (s cosStore) put(ctx context.Context, key string) error {
	_, err := s.client.Object.Put(ctx, key, bytes.NewReader(probePayload), nil)
	return err
}

func (s cosStore) open(ctx context.Context, key string) (io.ReadCloser, error) {
	response, err := s.client.Object.Get(ctx, key, nil)
	if err != nil {
		return nil, err
	}
	return response.Body, nil
}

func (s cosStore) read(ctx context.Context, key string) ([]byte, error) {
	body, err := s.open(ctx, key)
	if err != nil {
		return nil, err
	}
	defer body.Close()
	return io.ReadAll(io.LimitReader(body, int64(len(probePayload))+1))
}

func (s cosStore) remove(ctx context.Context, key string) error {
	if _, err := s.client.Object.Delete(ctx, key); err != nil {
		return err
	}
	_, err := s.client.Object.Head(ctx, key, nil)
	var response *cos.ErrorResponse
	if errors.As(err, &response) && response.Response != nil && response.Response.StatusCode == http.StatusNotFound && (response.Code == "NoSuchKey" || response.Code == "") {
		return nil
	}
	return errors.New("cannot confirm deletion of the probe object")
}

func storageSettings(t *testing.T, backend string) map[string]string {
	t.Helper()
	if testing.Short() || os.Getenv("CUBE_VOLUME_BACKEND_E2E") != "1" {
		t.Skip("requires CUBE_VOLUME_BACKEND_E2E=1; writes and deletes a unique probe object")
	}
	return loadStorageSettings(t, backend)
}

func loadStorageSettings(t *testing.T, backend string) map[string]string {
	t.Helper()
	prefix := "CUBE_VOLUME_" + strings.ToUpper(backend) + "_"
	keys := []string{"VOLUME_ID", "BUCKET", "REGION"}
	if backend == "cos" {
		keys = append(keys, "SECRET_ID", "SECRET_KEY")
	} else {
		keys = append(keys, "ENDPOINT", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY")
	}
	values := map[string]string{}
	for _, key := range keys {
		values[key] = requiredEnv(t, prefix+key)
	}
	if !safeVolumeID.MatchString(values["VOLUME_ID"]) || !safeBucket.MatchString(values["BUCKET"]) || !safeRegion.MatchString(values["REGION"]) {
		t.Fatal("invalid volume ID, bucket or region")
	}
	return values
}

func makeStore(t *testing.T, backend string, values map[string]string) objectStore {
	t.Helper()
	base := http.DefaultTransport.(*http.Transport).Clone()
	base.Proxy = nil
	t.Cleanup(base.CloseIdleConnections)
	if backend == "cos" {
		endpoint, err := url.Parse("https://" + values["BUCKET"] + ".cos." + values["REGION"] + ".myqcloud.com")
		if err != nil {
			t.Fatal("invalid COS bucket endpoint")
		}
		client := cos.NewClient(&cos.BaseURL{BucketURL: endpoint}, &http.Client{
			Timeout:       30 * time.Second,
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
			Transport:     &cos.AuthorizationTransport{SecretID: values["SECRET_ID"], SecretKey: values["SECRET_KEY"], SessionToken: os.Getenv("CUBE_VOLUME_COS_SESSION_TOKEN"), Transport: base},
		})
		return cosStore{client: client}
	}
	endpoint, err := url.Parse(values["ENDPOINT"])
	if err != nil || endpoint.Hostname() == "" || (endpoint.Scheme != "http" && endpoint.Scheme != "https") || endpoint.User != nil || endpoint.Path != "" && endpoint.Path != "/" || strings.ContainsAny(values["ENDPOINT"], "?#") {
		t.Fatal("MinIO endpoint must be an HTTP(S) origin without credentials, path, query or fragment")
	}
	if endpoint.Scheme == "http" && endpoint.Hostname() != "127.0.0.1" && endpoint.Hostname() != "localhost" && endpoint.Hostname() != "::1" && os.Getenv("CUBE_VOLUME_MINIO_ALLOW_HTTP") != "1" {
		t.Fatal("use HTTPS, loopback, or explicitly allow a trusted test endpoint with CUBE_VOLUME_MINIO_ALLOW_HTTP=1")
	}
	client, err := minio.New(endpoint.Host, &minio.Options{
		Creds:  credentials.NewStaticV4(values["ACCESS_KEY_ID"], values["SECRET_ACCESS_KEY"], os.Getenv("CUBE_VOLUME_MINIO_SESSION_TOKEN")),
		Secure: endpoint.Scheme == "https", Region: values["REGION"], BucketLookup: minio.BucketLookupPath, Transport: base,
	})
	if err != nil {
		t.Fatal("cannot initialize the MinIO client")
	}
	return minioStore{client: client, bucket: values["BUCKET"]}
}

func runStorageProbe(t *testing.T, store objectStore, volumeID string) {
	t.Helper()
	filePath := "/sdk-direct-probe/go-" + randomID(t) + ".bin"
	key := "volumes/" + volumeID + filePath
	token := "probe-" + randomID(t)
	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	t.Cleanup(func() {
		cleanupCtx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		if err := store.remove(cleanupCtx, key); err != nil {
			t.Errorf("probe cleanup failed; check and remove only object %s", key)
		} else {
			t.Logf("deleted probe object %s; existing volume/bucket untouched", key)
		}
	})
	if err := store.put(ctx, key); err != nil {
		t.Fatal("probe upload failed; verify backend endpoint, credentials and object permissions")
	}
	stamp := time.Now().UTC()
	entry := cubesandbox.VolumeEntryStat{Name: path.Base(filePath), Path: filePath, Type: "file", Size: int64(len(probePayload)), Mode: 0o644, ATime: stamp, MTime: stamp, CTime: stamp}
	var fileReads atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		if req.Header.Get("Authorization") != "Bearer "+token {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		if req.Method != http.MethodGet || req.Header.Get("X-API-Key") != "" || req.Header.Get("X-Access-Token") != "" {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		prefix := "/volumecontent/" + volumeID + "/"
		query := req.URL.Query()
		if !strings.HasPrefix(req.URL.Path, prefix) || len(query["path"]) != 1 {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		operation, target := strings.TrimPrefix(req.URL.Path, prefix), query.Get("path")
		w.Header().Set("Content-Type", "application/json")
		if operation == "dir" && target == path.Dir(filePath) && query.Get("depth") == "1" {
			_ = json.NewEncoder(w).Encode([]cubesandbox.VolumeEntryStat{entry})
			return
		}
		if target != filePath {
			w.WriteHeader(http.StatusNotFound)
			return
		}
		switch operation {
		case "path":
			_ = json.NewEncoder(w).Encode(entry)
		case "file":
			fileReads.Add(1)
			data, err := store.read(req.Context(), key)
			if err != nil {
				w.WriteHeader(http.StatusBadGateway)
				return
			}
			w.Header().Set("Content-Type", "application/octet-stream")
			_, _ = w.Write(data)
		default:
			w.WriteHeader(http.StatusNotFound)
		}
	}))
	t.Cleanup(server.Close)
	content := newContent(t, server.URL, volumeID, token)
	digest := sha256.Sum256(probePayload)
	readChecks(t, ctx, content, filePath, hex.EncodeToString(digest[:]))
	invalid := newContent(t, server.URL, volumeID, "invalid-"+randomID(t))
	if _, err := invalid.Exists(ctx, filePath); !errors.Is(err, cubesandbox.ErrAuthentication) {
		t.Fatal("loopback gateway did not reject an invalid token")
	}
	if fileReads.Load() != 3 {
		t.Fatal("buffered, stream and early-close reads must reach the object store")
	}
	t.Log("SDK checks through LOOPBACK SHIM; metadata/token are simulated, not production gateway evidence")
}

func TestVolumeStorageMinIO(t *testing.T) {
	values := storageSettings(t, "minio")
	runStorageProbe(t, makeStore(t, "minio", values), values["VOLUME_ID"])
}

func TestVolumeStorageCOS(t *testing.T) {
	values := storageSettings(t, "cos")
	runStorageProbe(t, makeStore(t, "cos", values), values["VOLUME_ID"])
}

type memoryStore struct {
	objects map[string][]byte
	removed bool
}

func (s *memoryStore) put(_ context.Context, key string) error {
	s.objects[key] = bytes.Clone(probePayload)
	return nil
}

func (s *memoryStore) read(_ context.Context, key string) ([]byte, error) {
	value, ok := s.objects[key]
	if !ok {
		return nil, errors.New("object not found")
	}
	return bytes.Clone(value), nil
}

func (s *memoryStore) open(ctx context.Context, key string) (io.ReadCloser, error) {
	data, err := s.read(ctx, key)
	if err != nil {
		return nil, err
	}
	return io.NopCloser(bytes.NewReader(data)), nil
}

func (s *memoryStore) remove(_ context.Context, key string) error {
	delete(s.objects, key)
	s.removed = true
	return nil
}

func TestVolumeStorageLoopback(t *testing.T) {
	for _, backend := range []string{"minio", "cos"} {
		t.Run(backend, func(t *testing.T) {
			store := &memoryStore{objects: map[string][]byte{}}
			t.Cleanup(func() {
				if len(store.objects) != 0 || !store.removed {
					t.Error("offline probe did not clean up its object")
				}
			})
			runStorageProbe(t, store, "test-volume")
		})
	}
}
