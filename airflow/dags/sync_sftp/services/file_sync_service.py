"""File synchronization service."""

import hashlib
import logging
from collections.abc import Callable
from typing import BinaryIO

from sync_sftp.core.file_info import FileInfo
from sync_sftp.core.file_repository import FileRepository
from sync_sftp.state.file_state_store import FileStateStore

logger = logging.getLogger(__name__)


class FileSyncService:
    """Service to synchronize files between two repositories.

    Handles the core sync logic: identifying new files, transferring them
    with streaming support for large files, and tracking sync state.

    Supports optional transformation hooks for extensibility (compression,
    encryption, data transformation, etc.).
    """

    def __init__(
        self,
        source_repo: FileRepository,
        target_repo: FileRepository,
        state_store: FileStateStore,
        transform_func: Callable[[BinaryIO], BinaryIO] | None = None,
        chunk_size: int = 64 * 1024 * 1024,  # 64MB chunks
    ):
        """Initialize sync service.

        Args:
            source_repo: Repository to read files from.
            target_repo: Repository to write files to.
            state_store: Store to track sync state.
            transform_func: Optional function to transform file content during transfer.
                           Takes input stream, returns output stream.
                           Default is identity (no transformation).
            chunk_size: Size of chunks for streaming large files (default 64MB).
        """
        self.source_repo = source_repo
        self.target_repo = target_repo
        self.state_store = state_store
        self.transform_func = transform_func or self._identity_transform
        self.chunk_size = chunk_size

    @staticmethod
    def _identity_transform(stream_in: BinaryIO) -> BinaryIO:
        """Identity transformation (no-op)."""
        return stream_in

    def _calculate_checksum(self, file_path: str, repo: FileRepository) -> str:
        """Calculate MD5 checksum of a file.

        Args:
            file_path: Path to the file.
            repo: Repository containing the file.

        Returns:
            MD5 checksum as hex string.
        """
        md5_hash = hashlib.md5()
        with repo.open_read(file_path) as f:
            while True:
                chunk = f.read(self.chunk_size)
                if not chunk:
                    break
                md5_hash.update(chunk)
        return md5_hash.hexdigest()

    def _transfer_file(
        self,
        file_info: FileInfo,
        preserve_structure: bool = True,
    ) -> FileInfo:
        """Transfer a single file from source to target with streaming.

        Args:
            file_info: Information about the file to transfer.
            preserve_structure: If True, maintain directory structure in target.

        Returns:
            FileInfo of the transferred file.

        Raises:
            IOError: If transfer fails.
        """
        source_path = file_info.path
        target_path = (
            file_info.path
            if preserve_structure
            else file_info.path.split("/")[-1]
        )

        logger.info(
            f"Transferring file: {source_path} -> {target_path} "
            f"(size: {file_info.size} bytes)"
        )

        # Calculate checksum while transferring (more efficient)
        md5_hash = hashlib.md5()
        bytes_transferred = 0

        try:
            # Stream file from source to target
            with self.source_repo.open_read(source_path) as source_stream:
                # Apply transformation if provided
                transformed_stream = self.transform_func(source_stream)

                with self.target_repo.open_write(target_path) as target_stream:
                    while True:
                        try:
                            chunk = transformed_stream.read(self.chunk_size)
                            if not chunk:
                                break
                            # Update checksum while reading
                            md5_hash.update(chunk)
                            target_stream.write(chunk)
                            bytes_transferred += len(chunk)
                        except Exception as e:
                            logger.error(
                                f"Error during transfer chunk: {e}"
                            )
                            raise

            # Calculate final checksum
            checksum = md5_hash.hexdigest()
            file_info.checksum = checksum

            logger.info(
                f"Successfully transferred {source_path} "
                f"({bytes_transferred} bytes, checksum: {checksum})"
            )

        except Exception as e:
            logger.error(
                f"Failed to transfer file {source_path}: {e}",
                exc_info=True,
            )
            raise

        return file_info

    def get_new_files(self, base_path: str = "") -> list[FileInfo]:
        """Get list of files that need to be synced.

        Args:
            base_path: Base path to search for files (optional).

        Returns:
            List of FileInfo for files that haven't been synced yet.
        """
        logger.info(f"Listing files from source repository (base_path: {base_path})")
        all_files = self.source_repo.list_files(base_path)

        logger.info(f"Found {len(all_files)} files in source")
        logger.info(f"Checking sync state for {len(all_files)} files")

        new_files = []
        for file_info in all_files:
            if not self.state_store.is_synced(file_info.path):
                new_files.append(file_info)
            else:
                logger.debug(f"File already synced, skipping: {file_info.path}")

        logger.info(f"Found {len(new_files)} new files to sync")
        return new_files

    def sync_file(self, file_info: FileInfo) -> FileInfo:
        """Sync a single file.

        Args:
            file_info: Information about the file to sync.

        Returns:
            FileInfo of the synced file.

        Raises:
            IOError: If sync fails.
        """
        try:
            transferred_file = self._transfer_file(file_info)
            self.state_store.mark_synced(transferred_file)
            return transferred_file
        except Exception as e:
            logger.error(f"Failed to sync file {file_info.path}: {e}")
            raise

    def sync_files(self, file_infos: list[FileInfo]) -> list[FileInfo]:
        """Sync multiple files.

        Args:
            file_infos: List of files to sync.

        Returns:
            List of successfully synced FileInfo objects.
        """
        synced_files = []
        for file_info in file_infos:
            try:
                synced_file = self.sync_file(file_info)
                synced_files.append(synced_file)
            except Exception as e:
                logger.error(f"Failed to sync {file_info.path}: {e}")
                # Continue with other files

        if synced_files:
            logger.info(
                f"Successfully synced {len(synced_files)}/{len(file_infos)} files"
            )

        return synced_files
