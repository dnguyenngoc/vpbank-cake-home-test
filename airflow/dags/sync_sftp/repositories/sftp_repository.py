"""SFTP file repository implementation."""

import io
import logging
from collections.abc import Callable
from datetime import datetime
from types import TracebackType
from typing import TYPE_CHECKING, BinaryIO

from airflow.providers.sftp.hooks.sftp import SFTPHook

if TYPE_CHECKING:
    from paramiko.sftp_client import SFTPClient

from sync_sftp.core.file_info import FileInfo
from sync_sftp.core.file_repository import FileRepository

logger = logging.getLogger(__name__)


class _SFTPFileReader(io.BytesIO):
    """File-like object that reads from SFTP in chunks."""

    def __init__(self, sftp_hook: SFTPHook, path: str):
        """Initialize reader."""
        super().__init__()
        self.hook = sftp_hook
        self.path = path
        self._sftp_client = None
        self._file_handle = None
        self._position = 0
        self._buffer = b""
        self._buffer_size = 8192  # 8KB chunks

    def __enter__(self) -> "_SFTPFileReader":
        """Context manager entry."""
        self._sftp_client = self.hook.get_conn()
        self._file_handle = self._sftp_client.open(self.path, "rb")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Context manager exit."""
        if self._file_handle:
            self._file_handle.close()
        if self._sftp_client:
            self._sftp_client.close()

    def read(self, size: int = -1) -> bytes:
        """Read data from SFTP file."""
        if not self._file_handle:
            raise ValueError("File not opened. Use as context manager.")
        if size == -1:
            # Read all remaining data
            data = self._file_handle.read()
            self._position += len(data)
            return data
        else:
            data = self._file_handle.read(size)
            self._position += len(data)
            return data

    def seek(self, pos: int, whence: int = 0) -> int:
        """Seek to position (limited support)."""
        if not self._file_handle:
            raise ValueError("File not opened. Use as context manager.")
        if whence == 0:  # SEEK_SET
            self._file_handle.seek(pos)
            self._position = pos
        elif whence == 1:  # SEEK_CUR
            new_pos = self._position + pos
            self._file_handle.seek(new_pos)
            self._position = new_pos
        elif whence == 2:  # SEEK_END
            self._file_handle.seek(pos, whence)
            self._position = self._file_handle.tell()
        return self._position

    def tell(self) -> int:
        """Get current position."""
        return self._position


class _SFTPFileWriter(io.BytesIO):
    """File-like object that writes to SFTP in chunks."""

    def __init__(self, sftp_hook: SFTPHook, path: str):
        """Initialize writer."""
        super().__init__()
        self.hook = sftp_hook
        self.path = path
        self._sftp_client = None
        self._file_handle = None
        self._buffer = io.BytesIO()

    def __enter__(self) -> "_SFTPFileWriter":
        """Context manager entry."""
        self._sftp_client = self.hook.get_conn()
        
        # Ensure parent directories exist
        parts = self.path.split("/")
        if len(parts) > 1:
            parent_dir = "/".join(parts[:-1])
            try:
                try:
                    self._sftp_client.stat(parent_dir)
                except FileNotFoundError:
                    # Directory doesn't exist, create it recursively
                    self._sftp_client.mkdir(parent_dir)
            except Exception:
                # Directory might already exist or other error, ignore
                pass
        
        self._file_handle = self._sftp_client.open(self.path, "wb")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Context manager exit - flush and close."""
        if self._buffer.tell() > 0:
            self._buffer.seek(0)
            if self._file_handle:
                self._file_handle.write(self._buffer.read())
        if self._file_handle:
            self._file_handle.close()
        if self._sftp_client:
            self._sftp_client.close()

    def write(self, data: bytes) -> int:
        """Write data to SFTP file."""
        if not self._file_handle:
            raise ValueError("File not opened. Use as context manager.")
        written = self._file_handle.write(data)
        return written

    def flush(self) -> None:
        """Flush buffer (no-op for SFTP)."""
        pass


class SFTPFileRepository(FileRepository):
    """SFTP implementation of FileRepository.

    Uses Airflow's SFTPHook to interact with SFTP servers.
    Supports streaming for large files to avoid loading entire file into memory.
    """

    def __init__(self, sftp_conn_id: str, base_path: str = ""):
        """Initialize SFTP repository.

        Args:
            sftp_conn_id: Airflow connection ID for SFTP credentials.
            base_path: Base directory path on SFTP server (optional).
        """
        self.sftp_conn_id = sftp_conn_id
        self.base_path = base_path.rstrip("/")
        self._hook: SFTPHook | None = None

    def _get_hook(self) -> SFTPHook:
        """Get or create SFTP hook."""
        if self._hook is None:
            self._hook = SFTPHook(ssh_conn_id=self.sftp_conn_id)
        return self._hook

    def _full_path(self, relative_path: str) -> str:
        """Convert relative path to full path on SFTP server."""
        if not self.base_path:
            return relative_path.lstrip("/")
        return f"{self.base_path}/{relative_path.lstrip('/')}"

    def _relative_path(self, full_path: str) -> str:
        """Convert full path to relative path."""
        if not self.base_path:
            return full_path.lstrip("/")
        if full_path.startswith(self.base_path):
            return full_path[len(self.base_path) :].lstrip("/")
        return full_path.lstrip("/")

    def _process_directory_item(
        self,
        item: str,
        path: str,
        relative_base: str,
        sftp_client: "SFTPClient",
        files: list[FileInfo],
        walk_func: Callable[[str, str], None],
    ) -> None:
        """Process a single item in directory (file or subdirectory).

        Args:
            item: Item name.
            path: Current directory path.
            relative_base: Relative base path for file paths.
            sftp_client: SFTP client connection.
            files: List to append found files to.
            walk_func: Function to recursively walk subdirectories.
        """
        if item in [".", ".."]:
            return

        # Build full path
        if not path or path == ".":
            full_item_path = item
        else:
            full_item_path = f"{path}/{item}"

        # Build relative path
        if relative_base:
            relative_item_path = f"{relative_base}/{item}"
        else:
            relative_item_path = item

        try:
            # Use SFTP client to get file attributes
            attrs = sftp_client.stat(full_item_path)
            if attrs.st_mode & 0o170000 == 0o040000:  # Directory
                logger.debug(f"Found directory: {full_item_path}")
                walk_func(full_item_path, relative_item_path)
            else:  # File
                modified_at = datetime.fromtimestamp(attrs.st_mtime)
                file_info = FileInfo(
                    path=relative_item_path,
                    size=attrs.st_size,
                    modified_at=modified_at,
                )
                logger.debug(
                    f"Found file: {relative_item_path} "
                    f"(size: {attrs.st_size})"
                )
                files.append(file_info)
        except Exception as e:
            # Skip files we can't access
            logger.warning(f"Could not access {full_item_path}: {e}")

    def list_files(self, base_path: str = "") -> list[FileInfo]:
        """List all files recursively under base_path."""
        hook = self._get_hook()
        files: list[FileInfo] = []
        # Determine search path
        # When no base_path specified, start from home directory
        if base_path:
            search_path = self._full_path(base_path)
        elif self.base_path:
            search_path = self.base_path
        else:
            # Use "." for current directory (home directory)
            search_path = "."

        # Get SFTP client once and reuse for entire operation
        sftp_client = hook.get_conn()

        def _walk_directory(path: str, relative_base: str = "") -> None:
            """Recursively walk directory and collect files."""
            try:
                # Use "." for current directory if path is empty
                list_path = path if path else "."
                logger.debug(f"Listing directory: {list_path}")

                # Use SFTP client directly to list directory
                items = sftp_client.listdir(list_path)
                if not items:
                    logger.debug(f"No items found in {list_path}")
                    return
                logger.debug(f"Found {len(items)} items in {list_path}: {items}")

                for item in items:
                    self._process_directory_item(
                        item, path, relative_base, sftp_client, files, _walk_directory
                    )
            except Exception as e:
                # Skip directories we can't access
                logger.warning(f"Could not access directory {path}: {e}")

        try:
            logger.info(f"Starting to list files from path: '{search_path}'")
            _walk_directory(search_path, base_path)
            logger.info(f"Found {len(files)} files total")
        finally:
            # Close SFTP client connection
            sftp_client.close()

        return files

    def open_read(self, file_path: str) -> BinaryIO:
        """Open file for reading with streaming support."""
        hook = self._get_hook()
        full_path = self._full_path(file_path)
        return _SFTPFileReader(hook, full_path)

    def open_write(self, file_path: str) -> BinaryIO:
        """Open file for writing with streaming support."""
        hook = self._get_hook()
        full_path = self._full_path(file_path)
        # Directory creation is handled in _SFTPFileWriter.__enter__
        return _SFTPFileWriter(hook, full_path)

    def exists(self, file_path: str) -> bool:
        """Check if file exists."""
        hook = self._get_hook()
        full_path = self._full_path(file_path)
        sftp_client = hook.get_conn()
        try:
            sftp_client.stat(full_path)
            return True
        except Exception:
            return False
        finally:
            sftp_client.close()

    def get_file_info(self, file_path: str) -> FileInfo | None:
        """Get file information if it exists."""
        if not self.exists(file_path):
            return None

        hook = self._get_hook()
        full_path = self._full_path(file_path)
        sftp_client = hook.get_conn()
        try:
            attrs = sftp_client.stat(full_path)
            modified_at = datetime.fromtimestamp(attrs.st_mtime)
            return FileInfo(
                path=file_path,
                size=attrs.st_size,
                modified_at=modified_at,
            )
        except Exception:
            return None
        finally:
            sftp_client.close()