// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0

package s3store

import (
	"strings"
	"testing"
)

func TestVersionedObjectKeyBindsContent(t *testing.T) {
	client := &Client{}
	first, err := client.VersionedObjectKey("artifact-1", strings.Repeat("a", 64))
	if err != nil {
		t.Fatal(err)
	}
	second, err := client.VersionedObjectKey("artifact-1", strings.Repeat("b", 64))
	if err != nil {
		t.Fatal(err)
	}
	if first == second || !strings.HasSuffix(first, ".ext4") {
		t.Fatalf("versioned keys = %q and %q", first, second)
	}
}

func TestStorageRefUsesNodeEndpointWithoutSecrets(t *testing.T) {
	client := &Client{cfg: Config{
		Driver:         "s3",
		Endpoint:       "http://control-minio:9000",
		NodeEndpoint:   "http://node-minio:9000",
		Bucket:         "artifact-bucket",
		AccessKey:      "access-key-must-not-appear",
		SecretKey:      "secret-key-must-not-appear",
		ArtifactPrefix: "/template-artifacts/",
	}}
	got := client.StorageRef()
	want := "s3://artifact-bucket/template-artifacts?endpoint=http%3A%2F%2Fnode-minio%3A9000"
	if got != want {
		t.Fatalf("storage ref = %q, want %q", got, want)
	}
	if strings.Contains(got, "access-key") || strings.Contains(got, "secret-key") {
		t.Fatalf("storage ref leaked credentials: %q", got)
	}
}

func TestStoredUserKeyRequiresConfiguredPrefix(t *testing.T) {
	client := &Client{cfg: Config{ArtifactPrefix: "templates"}}
	got, err := client.storedUserKey("templates/artifact-1.ext4", "artifact-1")
	if err != nil {
		t.Fatal(err)
	}
	if got != "artifact-1.ext4" {
		t.Fatalf("key = %q", got)
	}
	if _, err := client.storedUserKey("old-prefix/artifact-1.ext4", "artifact-1"); err == nil {
		t.Fatal("mismatched prefix unexpectedly accepted")
	}
}
