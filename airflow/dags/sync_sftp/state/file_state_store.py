"""State store to track which files have been synced."""

import json
from datetime import datetime
from typing import Any

from airflow.sdk import Variable

from sync_sftp.core.file_info import FileInfo


class FileStateStore:
    """Stores sync state using Airflow Variables.

    Tracks which files have been successfully synced by storing their paths
    and metadata. This allows the sync process to skip files that have already
    been transferred.

    State is stored as JSON in Airflow Variables, keyed by a state key.
    """

    def __init__(self, state_key: str = "sftp_sync_state"):
        """Initialize state store.

        Args:
            state_key: Airflow Variable key to store state under.
        """
        self.state_key = state_key

    def get_synced_files(self) -> dict[str, dict[str, Any]]:
        """Get all synced files from state.

        Returns:
            Dictionary mapping file paths to their sync metadata.
        """
        try:
            state_json = Variable.get(self.state_key, default="{}")
            return json.loads(state_json)
        except Exception:
            return {}

    def is_synced(self, file_path: str, file_info: FileInfo | None = None) -> bool:
        """Check if a file has been synced and hasn't changed.

        Args:
            file_path: Relative path of the file.
            file_info: Optional FileInfo to compare with stored state.

        Returns:
            True if file is marked as synced and hasn't changed, False otherwise.
        """
        synced_files = self.get_synced_files()
        if file_path not in synced_files:
            return False
        
        # If file_info provided, check if file has changed
        if file_info:
            stored_info = synced_files[file_path]
            # Compare checksum if available (most reliable)
            if file_info.checksum and stored_info.get("checksum"):
                return file_info.checksum == stored_info["checksum"]
            # Fallback: compare size and modified_at
            if file_info.size != stored_info.get("size"):
                return False
            # Compare modified_at (with small tolerance for timestamp differences)
            stored_modified = datetime.fromisoformat(stored_info["modified_at"])
            time_diff = abs((file_info.modified_at - stored_modified).total_seconds())
            if time_diff > 1:  # More than 1 second difference
                return False
        
        return True
    
    def needs_sync(self, file_info: FileInfo) -> bool:
        """Check if a file needs to be synced (new or changed).

        Args:
            file_info: FileInfo of the file to check.

        Returns:
            True if file needs sync, False otherwise.
        """
        return not self.is_synced(file_info.path, file_info)

    def mark_synced(self, file_info: FileInfo) -> None:
        """Mark a file as synced.

        Args:
            file_info: FileInfo of the synced file.
        """
        synced_files = self.get_synced_files()
        synced_files[file_info.path] = {
            "size": file_info.size,
            "modified_at": file_info.modified_at.isoformat(),
            "checksum": file_info.checksum,
            "synced_at": datetime.utcnow().isoformat(),
        }
        Variable.set(self.state_key, json.dumps(synced_files))

    def mark_multiple_synced(self, file_infos: list[FileInfo]) -> None:
        """Mark multiple files as synced in one operation.

        Args:
            file_infos: List of FileInfo objects for synced files.
        """
        synced_files = self.get_synced_files()
        now = datetime.utcnow().isoformat()

        for file_info in file_infos:
            synced_files[file_info.path] = {
                "size": file_info.size,
                "modified_at": file_info.modified_at.isoformat(),
                "checksum": file_info.checksum,
                "synced_at": now,
            }

        Variable.set(self.state_key, json.dumps(synced_files))

    def clear_state(self) -> None:
        """Clear all sync state."""
        Variable.set(self.state_key, "{}")

    def get_file_sync_info(self, file_path: str) -> dict[str, Any] | None:
        """Get sync metadata for a specific file.

        Args:
            file_path: Relative path of the file.

        Returns:
            Dictionary with sync metadata or None if not synced.
        """
        synced_files = self.get_synced_files()
        return synced_files.get(file_path)
