# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import os
<<<<<<< HEAD
from dataclasses import dataclass, field
=======
import warnings
from dataclasses import dataclass, field
from urllib.parse import urlsplit
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)


@dataclass
class Config:
    """SDK configuration. All fields can be set via environment variables."""

    api_url: str = field(
        default_factory=lambda: os.environ.get("CUBE_API_URL", "http://127.0.0.1:3000")
    )
<<<<<<< HEAD
=======
    api_key: str | None = field(
        default_factory=lambda: os.environ.get("CUBE_API_KEY")
    )
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
    template_id: str | None = field(
        default_factory=lambda: os.environ.get("CUBE_TEMPLATE_ID")
    )
    proxy_node_ip: str | None = field(
        default_factory=lambda: os.environ.get("CUBE_PROXY_NODE_IP")
    )
    proxy_port: int = field(
        default_factory=lambda: int(os.environ.get("CUBE_PROXY_PORT_HTTP", "80"))
    )
    sandbox_domain: str = field(
        default_factory=lambda: os.environ.get("CUBE_SANDBOX_DOMAIN", "cube.app")
    )
    timeout: int = 300
    request_timeout: float = 30.0

    def __post_init__(self) -> None:
<<<<<<< HEAD
        self.api_url = self.api_url.rstrip("/")
=======
        self.api_url = self.api_url.rstrip("/")
        self._warn_if_api_key_sent_in_cleartext()

    def _warn_if_api_key_sent_in_cleartext(self) -> None:
        """Warn when an API key would be sent over plain HTTP to a remote host.

        Sending ``X-API-Key`` over ``http://`` to a non-loopback address exposes
        the key to anyone on the network path. Local development against
        ``http://127.0.0.1`` is the common case and stays silent; only a remote
        cleartext endpoint triggers the warning (we warn rather than raise so the
        SDK keeps working against a plain-HTTP backend when the caller accepts
        the risk).
        """
        if not self.api_key or not self.api_url.startswith("http://"):
            return
        host = urlsplit(self.api_url).hostname or ""
        is_loopback = (
            host in ("localhost", "127.0.0.1", "::1") or host.startswith("127.")
        )
        if not is_loopback:
            warnings.warn(
                f"CUBE_API_KEY is being sent over plain HTTP to a non-loopback "
                f"host ({host!r}); use an https:// CUBE_API_URL to avoid "
                f"transmitting the API key in cleartext.",
                stacklevel=3,
            )
>>>>>>> e47b8a2 (fix(sdk/python): address review on Volume API)
