// SPDX-License-Identifier: Apache-2.0
// Copyright (C) 2026 Tencent. All rights reserved.

<<<<<<< HEAD
// Lightweight WebUI session storage. JWT access/refresh tokens are sent as
// `Authorization: Bearer <accessToken>` (see lib/api.ts) and validated by
// CubeOps's /auth/session endpoint.

const ACCESS_TOKEN_KEY = 'cube.accessToken';
const REFRESH_TOKEN_KEY = 'cube.refreshToken';
=======
// Lightweight WebUI session storage. The token is sent as `X-Session-Token`
// (see lib/api.ts) and validated by CubeAPI's /auth/session endpoint.

const TOKEN_KEY = 'cube.session';
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
const USER_KEY = 'cube.sessionUser';
const AUTH_STATUS_KEY = 'cube.authStatus';

export type AuthStatus = 'allowed' | 'guest';

export function getSessionToken(): string {
<<<<<<< HEAD
  return localStorage.getItem(ACCESS_TOKEN_KEY) ?? '';
}

export function getRefreshToken(): string {
  return localStorage.getItem(REFRESH_TOKEN_KEY) ?? '';
=======
  return localStorage.getItem(TOKEN_KEY) ?? '';
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
}

export function getSessionUser(): string {
  return localStorage.getItem(USER_KEY) ?? '';
}

<<<<<<< HEAD
export function setSession(accessToken: string, refreshToken: string, username: string): void {
  localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
  localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
=======
export function setSession(token: string, username: string): void {
  localStorage.setItem(TOKEN_KEY, token);
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
  localStorage.setItem(USER_KEY, username);
  setLastAuthStatus('allowed');
}

export function clearSession(): void {
<<<<<<< HEAD
  localStorage.removeItem(ACCESS_TOKEN_KEY);
  localStorage.removeItem(REFRESH_TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  // Legacy cleanup
  localStorage.removeItem('cube.session');
=======
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
  setLastAuthStatus('guest');
}

export function getLastAuthStatus(): AuthStatus | null {
  const value = sessionStorage.getItem(AUTH_STATUS_KEY);
  return value === 'allowed' || value === 'guest' ? value : null;
}

export function setLastAuthStatus(status: AuthStatus): void {
  sessionStorage.setItem(AUTH_STATUS_KEY, status);
}
