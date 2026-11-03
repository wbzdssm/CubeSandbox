// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0

package e2e

import (
	"bytes"
	"context"
	"hash/crc64"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"strings"
	"testing"

	cos "github.com/tencentyun/cos-go-sdk-v5"
)

func TestVolumeStorageAdapters(t *testing.T) {
	for _, backend := range []string{"minio", "cos"} {
		t.Run(backend, func(t *testing.T) {
			key := "volumes/test-volume/sdk-direct-probe/adapter.bin"
			methods := []string{}
			expectedPath := "/" + key
			if backend == "minio" {
				expectedPath = "/test-bucket" + expectedPath
			}
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
				methods = append(methods, req.Method)
				if req.URL.Path != expectedPath || req.Header.Get("Authorization") == "" {
					t.Error("backend request must be signed and target only the probe key")
				}
				if backend == "cos" {
					w.Header().Set("x-cos-hash-crc64ecma", strconv.FormatUint(crc64.Checksum(probePayload, crc64.MakeTable(crc64.ECMA)), 10))
				}
				switch req.Method {
				case http.MethodPut:
					// MinIO signs a streaming upload using AWS chunk framing.
					body, _ := io.ReadAll(req.Body)
					if !bytes.Contains(body, probePayload) {
						t.Error("backend upload did not contain the probe payload")
					}
					w.Header().Set("ETag", `"probe-etag"`)
				case http.MethodGet:
					w.Header().Set("ETag", `"probe-etag"`)
					w.Header().Set("Last-Modified", "Mon, 02 Nov 2026 00:00:00 GMT")
					_, _ = w.Write(probePayload)
				case http.MethodDelete:
					w.WriteHeader(http.StatusNoContent)
				case http.MethodHead:
					// A missing object's HEAD response has no XML error body.
					w.WriteHeader(http.StatusNotFound)
				default:
					t.Error("unexpected backend operation")
					w.WriteHeader(http.StatusBadRequest)
				}
			}))
			defer server.Close()
			var store objectStore
			if backend == "minio" {
				store = makeStore(t, backend, map[string]string{"BUCKET": "test-bucket", "REGION": "us-east-1", "ENDPOINT": server.URL, "ACCESS_KEY_ID": "test-access", "SECRET_ACCESS_KEY": "test-secret"})
			} else {
				endpoint, _ := url.Parse(server.URL)
				store = cosStore{client: cos.NewClient(&cos.BaseURL{BucketURL: endpoint}, &http.Client{Transport: &cos.AuthorizationTransport{SecretID: "test-access", SecretKey: "test-secret"}})}
			}
			ctx := context.Background()
			if err := store.put(ctx, key); err != nil {
				t.Fatalf("offline backend adapter upload failed: %v", err)
			}
			if data, err := store.read(ctx, key); err != nil || !bytes.Equal(data, probePayload) {
				t.Fatal("offline backend adapter read failed")
			}
			if err := store.remove(ctx, key); err != nil {
				t.Fatal("offline backend adapter deletion verification failed")
			}
			if strings.Join(methods, ",") != "PUT,GET,DELETE,HEAD" {
				t.Fatalf("unexpected backend operation sequence: %v", methods)
			}
		})
	}
}
