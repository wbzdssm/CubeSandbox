// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0

package artifactticket

import (
	"errors"
	"strings"
	"testing"
	"time"
)

func TestSignVerifyRoundTrip(t *testing.T) {
	now := time.Unix(1_800_000_000, 0)
	want := Claims{
		ArtifactID: "artifact-1",
		NodeID:     "node-1",
		ExpiresAt:  now.Add(time.Hour).Unix(),
		Generation: 3,
		Purpose:    PurposeDownload,
	}
	token, err := Sign([]byte(strings.Repeat("k", 32)), want)
	if err != nil {
		t.Fatal(err)
	}
	got, err := Verify([]byte(strings.Repeat("k", 32)), token, now)
	if err != nil {
		t.Fatal(err)
	}
	want.Version = tokenVersion
	if got != want {
		t.Fatalf("claims = %+v, want %+v", got, want)
	}
}

func TestVerifyRejectsTamperingAndExpiry(t *testing.T) {
	now := time.Unix(1_800_000_000, 0)
	secret := []byte(strings.Repeat("s", 32))
	token, err := Sign(secret, Claims{
		ArtifactID: "artifact-1",
		NodeID:     "node-1",
		ExpiresAt:  now.Add(time.Minute).Unix(),
		Generation: 1,
		Purpose:    PurposeDownload,
	})
	if err != nil {
		t.Fatal(err)
	}
	parts := strings.Split(token, ".")
	replacement := byte('A')
	if parts[0][len(parts[0])-1] == replacement {
		replacement = 'B'
	}
	tampered := parts[0][:len(parts[0])-1] + string(replacement) + "." + parts[1]
	if _, err := Verify(secret, tampered, now); !errors.Is(err, ErrInvalidToken) {
		t.Fatalf("tampered token error = %v, want ErrInvalidToken", err)
	}
	if _, err := Verify(secret, token, now.Add(time.Minute)); !errors.Is(err, ErrExpiredToken) {
		t.Fatalf("expired token error = %v, want ErrExpiredToken", err)
	}
	if _, err := Verify([]byte(strings.Repeat("x", 32)), token, now); !errors.Is(err, ErrInvalidToken) {
		t.Fatalf("wrong-key error = %v, want ErrInvalidToken", err)
	}
}

func TestSignRejectsWeakSecretAndIncompleteClaims(t *testing.T) {
	valid := Claims{
		ArtifactID: "artifact-1",
		NodeID:     "node-1",
		ExpiresAt:  time.Now().Add(time.Hour).Unix(),
		Generation: 1,
		Purpose:    PurposeDownload,
	}
	if _, err := Sign([]byte("short"), valid); err == nil {
		t.Fatal("weak secret unexpectedly accepted")
	}
	valid.NodeID = ""
	if _, err := Sign([]byte(strings.Repeat("k", 32)), valid); !errors.Is(err, ErrInvalidToken) {
		t.Fatalf("incomplete claims error = %v, want ErrInvalidToken", err)
	}
}

func TestURLOrigin(t *testing.T) {
	got, err := URLOrigin("HTTPS://S3.Example.COM:443/bucket/key?X-Amz-Signature=secret")
	if err != nil {
		t.Fatal(err)
	}
	if got != "https://s3.example.com:443" {
		t.Fatalf("origin = %q", got)
	}
	for _, raw := range []string{"", "file:///tmp/rootfs", "https://user@example.com/file"} {
		if _, err := URLOrigin(raw); err == nil {
			t.Fatalf("URLOrigin(%q) unexpectedly succeeded", raw)
		}
	}
}
