"""File information data class."""

from dataclasses import dataclass
from datetime import datetime


@dataclass
class FileInfo:
    """Represents file metadata.

    Attributes:
        path: Relative path of the file from base directory.
        size: File size in bytes.
        modified_at: Last modification timestamp.
        checksum: Optional file checksum (MD5, SHA256, etc.) for integrity checking.
    """

    path: str
    size: int
    modified_at: datetime
    checksum: str | None = None

    def __str__(self) -> str:
        """String representation."""
        return (
            f"FileInfo(path={self.path}, size={self.size}, "
            f"modified_at={self.modified_at})"
        )
