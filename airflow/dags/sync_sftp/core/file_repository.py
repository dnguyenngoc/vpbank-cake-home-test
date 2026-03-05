"""Abstract file repository interface."""

from abc import ABC, abstractmethod
from typing import BinaryIO

from sync_sftp.core.file_info import FileInfo


class FileRepository(ABC):
    """Abstract interface for file storage operations.

    This abstraction allows the sync logic to work with any storage backend
    (SFTP, S3, GCS, etc.) without modification. To switch from SFTP to Object Storage,
    simply implement this interface for the new backend.
    """

    @abstractmethod
    def list_files(self, base_path: str = "") -> list[FileInfo]:
        """List all files under the given base path recursively.

        Args:
            base_path: Base directory path to list files from. Empty string means root.

        Returns:
            List of FileInfo objects representing all files found.

        Raises:
            ConnectionError: If unable to connect to the storage backend.
            PermissionError: If access is denied.
        """
        pass

    @abstractmethod
    def open_read(self, file_path: str) -> BinaryIO:
        """Open a file for reading in binary mode.

        Args:
            file_path: Relative path to the file.

        Returns:
            Binary file-like object that supports read() and can be used as
            a context manager.

        Raises:
            FileNotFoundError: If the file doesn't exist.
            PermissionError: If read access is denied.
        """
        pass

    @abstractmethod
    def open_write(self, file_path: str) -> BinaryIO:
        """Open a file for writing in binary mode.

        Creates parent directories if they don't exist.

        Args:
            file_path: Relative path to the file.

        Returns:
            Binary file-like object that supports write() and can be used as
            a context manager.

        Raises:
            PermissionError: If write access is denied.
            OSError: If unable to create parent directories.
        """
        pass

    @abstractmethod
    def exists(self, file_path: str) -> bool:
        """Check if a file exists.

        Args:
            file_path: Relative path to the file.

        Returns:
            True if file exists, False otherwise.
        """
        pass

    @abstractmethod
    def get_file_info(self, file_path: str) -> FileInfo | None:
        """Get file information if it exists.

        Args:
            file_path: Relative path to the file.

        Returns:
            FileInfo if file exists, None otherwise.
        """
        pass
