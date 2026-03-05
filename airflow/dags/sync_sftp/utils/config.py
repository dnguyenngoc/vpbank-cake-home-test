"""Configuration utilities for SFTP sync."""

from dataclasses import dataclass

from airflow.sdk import Variable


@dataclass
class SyncConfig:
    """Configuration for SFTP sync operation.

    Attributes:
        source_conn_id: Airflow connection ID for source SFTP server.
        target_conn_id: Airflow connection ID for target SFTP server.
        source_base_path: Base path on source SFTP server (optional).
        target_base_path: Base path on target SFTP server (optional).
        state_key: Key for storing sync state in Airflow Variables.
        chunk_size: Size of chunks for streaming (bytes).
    """

    source_conn_id: str
    target_conn_id: str
    source_base_path: str = ""
    target_base_path: str = ""
    state_key: str = "sftp_sync_state"
    chunk_size: int = 64 * 1024 * 1024  # 64MB


def get_sync_config() -> SyncConfig:
    """Load sync configuration from Airflow Variables.

    Expected variables:
        - sftp_sync_source_conn_id: Source SFTP connection ID
        - sftp_sync_target_conn_id: Target SFTP connection ID
        - sftp_sync_source_base_path: Source base path (optional)
        - sftp_sync_target_base_path: Target base path (optional)
        - sftp_sync_state_key: State storage key (optional, default: sftp_sync_state)
        - sftp_sync_chunk_size: Chunk size in bytes (optional, default: 67108864)

    Returns:
        SyncConfig object with loaded configuration.

    Raises:
        ValueError: If required variables are missing.
    """
    source_conn_id = Variable.get("sftp_sync_source_conn_id", default=None)
    target_conn_id = Variable.get("sftp_sync_target_conn_id", default=None)

    if not source_conn_id:
        raise ValueError("sftp_sync_source_conn_id Airflow Variable is required")
    if not target_conn_id:
        raise ValueError("sftp_sync_target_conn_id Airflow Variable is required")

    source_base_path = Variable.get("sftp_sync_source_base_path", default="")
    target_base_path = Variable.get("sftp_sync_target_base_path", default="")
    state_key = Variable.get("sftp_sync_state_key", default="sftp_sync_state")

    try:
        chunk_size = int(Variable.get("sftp_sync_chunk_size", default="67108864"))
    except ValueError:
        chunk_size = 64 * 1024 * 1024  # Default 64MB

    return SyncConfig(
        source_conn_id=source_conn_id,
        target_conn_id=target_conn_id,
        source_base_path=source_base_path,
        target_base_path=target_base_path,
        state_key=state_key,
        chunk_size=chunk_size,
    )
