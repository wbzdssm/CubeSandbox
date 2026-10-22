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
