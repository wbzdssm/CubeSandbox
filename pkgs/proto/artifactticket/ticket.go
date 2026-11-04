// Copyright (c) 2026 Tencent Inc.
// SPDX-License-Identifier: Apache-2.0

// Package artifactticket defines the short-lived capability used when a
// Cubelet downloads a template rootfs directly from its artifact owner.
package artifactticket

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"strings"
	"time"
)

const (
	SourceS3             = "s3"
	SourceTemplateCenter = "template_center"
	PurposeDownload      = "download"
	PurposeRefresh       = "refresh"

	tokenVersion  = 1
	maxTokenBytes = 8 << 10
)

var (
	ErrInvalidToken = errors.New("invalid artifact download token")
	ErrExpiredToken = errors.New("artifact download token expired")
)

// Claims are signed by CubeMaster and verified by CubeTemplateCenter.
// NodeID binds a capability to the replica placement that requested it.
type Claims struct {
	Version    int    `json:"v"`
	ArtifactID string `json:"artifact_id"`
	NodeID     string `json:"node_id"`
	ExpiresAt  int64  `json:"expires_at"`
	Generation int64  `json:"generation"`
	Purpose    string `json:"purpose"`
}

// Ticket tells Cubelet where to fetch the bytes. Token is intentionally empty
// for S3: the presigned URL is already the capability and no internal token
// should be sent to an object store.
type Ticket struct {
	Source     string    `json:"source"`
	URL        string    `json:"url"`
	Token      string    `json:"token,omitempty"`
	ExpiresAt  time.Time `json:"expires_at"`
	ServerTime time.Time `json:"server_time"`
	ArtifactID string    `json:"artifact_id"`
	Generation int64     `json:"generation"`
	SizeBytes  int64     `json:"size_bytes"`
	SHA256     string    `json:"sha256"`
}

// Sign returns a self-contained HMAC token. The payload is readable but cannot
// be changed without the deployment-wide artifact download key.
func Sign(secret []byte, claims Claims) (string, error) {
	if err := validateSecret(secret); err != nil {
		return "", err
	}
	claims.Version = tokenVersion
	if err := validateClaims(claims); err != nil {
		return "", err
	}
	payload, err := json.Marshal(claims)
	if err != nil {
		return "", fmt.Errorf("marshal artifact download claims: %w", err)
	}
	encodedPayload := base64.RawURLEncoding.EncodeToString(payload)
	mac := hmac.New(sha256.New, secret)
	_, _ = mac.Write([]byte(encodedPayload))
	signature := base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
	return encodedPayload + "." + signature, nil
}

// Verify authenticates, decodes, and checks the expiry of a token.
func Verify(secret []byte, token string, now time.Time) (Claims, error) {
	var claims Claims
	if err := validateSecret(secret); err != nil {
		return claims, err
	}
	token = strings.TrimSpace(token)
	if token == "" || len(token) > maxTokenBytes {
		return claims, ErrInvalidToken
	}
	parts := strings.Split(token, ".")
	if len(parts) != 2 || parts[0] == "" || parts[1] == "" {
		return claims, ErrInvalidToken
	}
	provided, err := base64.RawURLEncoding.DecodeString(parts[1])
	if err != nil {
		return claims, ErrInvalidToken
	}
	mac := hmac.New(sha256.New, secret)
	_, _ = mac.Write([]byte(parts[0]))
	if !hmac.Equal(provided, mac.Sum(nil)) {
		return claims, ErrInvalidToken
	}
	payload, err := base64.RawURLEncoding.DecodeString(parts[0])
	if err != nil {
		return claims, ErrInvalidToken
	}
	decoder := json.NewDecoder(strings.NewReader(string(payload)))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&claims); err != nil {
		return Claims{}, ErrInvalidToken
	}
	if err := validateClaims(claims); err != nil {
		return Claims{}, ErrInvalidToken
	}
	if !now.Before(time.Unix(claims.ExpiresAt, 0)) {
		return Claims{}, ErrExpiredToken
	}
	return claims, nil
}

// ValidateSigningKey checks the deployment-wide HMAC key before services start.
func ValidateSigningKey(secret []byte) error {
	return validateSecret(secret)
}

func validateSecret(secret []byte) error {
	if len(secret) < 32 {
		return errors.New("artifact download signing key must contain at least 32 bytes")
	}
	return nil
}

func validateClaims(claims Claims) error {
	if claims.Version != tokenVersion ||
		strings.TrimSpace(claims.ArtifactID) == "" ||
		strings.TrimSpace(claims.NodeID) == "" ||
		claims.ExpiresAt <= 0 ||
		claims.Generation <= 0 ||
		(claims.Purpose != PurposeDownload && claims.Purpose != PurposeRefresh) {
		return ErrInvalidToken
	}
	return nil
}

// URLOrigin returns a canonical scheme://host origin for redirect and ticket
// binding checks. Paths, queries, fragments, and userinfo are rejected or
// discarded deliberately so credentials never become part of the comparison.
func URLOrigin(raw string) (string, error) {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || u.Host == "" || u.User != nil ||
		(u.Scheme != "http" && u.Scheme != "https") {
		return "", errors.New("invalid artifact download URL")
	}
	return strings.ToLower(u.Scheme) + "://" + strings.ToLower(u.Host), nil
}
