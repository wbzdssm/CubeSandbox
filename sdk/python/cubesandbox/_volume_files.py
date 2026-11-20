# Copyright (c) 2026 Tencent Inc.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import stat
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal, overload

from typing_extensions import Unpack

from ._exceptions import VolumePathNotFoundException
from ._volume_config import VolumeApiParams
from ._volume_content import VolumeEntryStat, VolumeFileType, validate_volume_path

if TYPE_CHECKING:
    from ._volume import Volume


class FileType(str, Enum):
    FILE = "file"
    DIR = "dir"


@dataclass
class FileEntry:
    """Volume-relative listing entry. Owner and group are numeric IDs in string form."""

    name: str
    type: FileType
    path: str
    size: int
    mode: int
    permissions: str
    owner: str
    group: str
    modified_time: float
    symlink_target: str | None

    @classmethod
    def _from_stat(cls, entry: VolumeEntryStat) -> FileEntry:
        kind = {
            VolumeFileType.FILE: stat.S_IFREG,
            VolumeFileType.DIRECTORY: stat.S_IFDIR,
            VolumeFileType.SYMLINK: stat.S_IFLNK,
            VolumeFileType.UNKNOWN: 0,
        }[entry.type]
        return cls(
            name=entry.name,
            type=FileType.DIR if entry.type == VolumeFileType.DIRECTORY else FileType.FILE,
            path=entry.path.lstrip("/"),
            size=entry.size,
            mode=entry.mode,
            permissions=stat.filemode(kind | stat.S_IMODE(entry.mode)),
            owner=str(entry.uid),
            group=str(entry.gid),
            modified_time=entry.mtime.timestamp(),
            symlink_target=entry.target,
        )


def _content_path(path: str) -> str:
    if not isinstance(path, str) or path.startswith("/"):
        raise ValueError("expected a volume-relative POSIX path")
    return validate_volume_path("/" + path)


class VolumeFiles:
    """Read files already committed to the volume backend using volume-relative paths."""

    def __init__(self, volume: Volume):
        self._volume = volume

    @overload
    def read(self, path: str, format: Literal["text"] = "text", *, user: str | None = None,
             **opts: Unpack[VolumeApiParams]) -> str: ...

    @overload
    def read(self, path: str, format: Literal["bytes"], *, user: str | None = None,
             **opts: Unpack[VolumeApiParams]) -> bytes: ...

    @overload
    def read(self, path: str, format: Literal["stream"], *, user: str | None = None,
             **opts: Unpack[VolumeApiParams]) -> Iterator[bytes]: ...

    def read(self, path: str, format: Literal["text", "bytes", "stream"] = "text", *,
             user: str | None = None, **opts: Unpack[VolumeApiParams]) -> str | bytes | Iterator[bytes]:
        """Read text, raw bytes, or a stream of byte chunks using volume credentials."""
        if user is not None:
            raise ValueError("direct volume reads do not support a sandbox user")
        return self._volume.read_file(_content_path(path), format, **opts)

    def list(self, path: str = "", **opts: Unpack[VolumeApiParams]) -> list[FileEntry]:
        """List the directory at path, defaulting to the volume root."""
        return [FileEntry._from_stat(entry) for entry in self._volume.list(_content_path(path), **opts)]

    def exists(self, path: str, **opts: Unpack[VolumeApiParams]) -> bool:
        """Check for an entry, propagating errors accessing the volume itself."""
        try:
            self._volume.get_info(_content_path(path), **opts)
        except VolumePathNotFoundException:
            return False
        return True
