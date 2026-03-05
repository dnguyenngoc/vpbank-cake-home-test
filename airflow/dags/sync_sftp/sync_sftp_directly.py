"""DAG for one-way SFTP file synchronization.

This DAG synchronizes files from a source SFTP server to a target SFTP server,
preserving directory structure. Files deleted on source remain on target.
Uses abstraction layers for easy migration to Object Storage backends.
"""

from datetime import datetime, timedelta

from airflow.decorators import task

from airflow import DAG
from sync_sftp.utils.config import get_sync_config

# Default arguments for the DAG
default_args = {
    "owner": "airflow",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 3,
    "retry_delay": timedelta(minutes=5),
    "execution_timeout": timedelta(hours=2),
}

# DAG definition
dag = DAG(
    "sync_sftp_one_way",
    default_args=default_args,
    description=(
        "One-way SFTP file synchronization with directory structure "
        "preservation."
    ),
    schedule=timedelta(minutes=5),  # Run every 5 minutes
    start_date=datetime(2026, 3, 5),
    catchup=False,
    max_active_runs=1,  # Only one run at a time
    max_active_tasks=4,  # Limit concurrent tasks
    tags=["sftp", "sync", "file-transfer"],
)


@task(dag=dag)
def init_config() -> dict:
    """Initialize and validate configuration.

    Returns:
        Dictionary with configuration values.
    """
    config = get_sync_config()
    return {
        "source_conn_id": config.source_conn_id,
        "target_conn_id": config.target_conn_id,
        "source_base_path": config.source_base_path,
        "target_base_path": config.target_base_path,
        "state_key": config.state_key,
        "chunk_size": config.chunk_size,
    }


@task(dag=dag)
def list_source_files(config: dict) -> list[dict]:
    """List all files from source SFTP server.

    Args:
        config: Configuration dictionary.

    Returns:
        List of file dictionaries (serializable for XCom).
    """
    from sync_sftp.repositories.sftp_repository import SFTPFileRepository

    source_repo = SFTPFileRepository(
        sftp_conn_id=config["source_conn_id"],
        base_path=config["source_base_path"],
    )

    files = source_repo.list_files()
    return [
        {
            "path": f.path,
            "size": f.size,
            "modified_at": f.modified_at.isoformat(),
        }
        for f in files
    ]


@task(dag=dag)
def filter_new_files(config: dict, all_files: list[dict]) -> list[dict]:
    """Filter files that haven't been synced yet or have been changed.

    Args:
        config: Configuration dictionary.
        all_files: List of all files from source.

    Returns:
        List of new or changed files to sync (serializable for XCom).
    """
    from datetime import datetime

    from sync_sftp.core.file_info import FileInfo
    from sync_sftp.state.file_state_store import FileStateStore

    state_store = FileStateStore(state_key=config["state_key"])

    new_files = []
    for file_dict in all_files:
        # Reconstruct FileInfo to check if file has changed
        file_info = FileInfo(
            path=file_dict["path"],
            size=file_dict["size"],
            modified_at=datetime.fromisoformat(file_dict["modified_at"]),
        )
        # Check if file needs sync (not synced or changed)
        if not state_store.is_synced(file_dict["path"], file_info):
            new_files.append(file_dict)

    return new_files


@task(
    dag=dag,
    retries=3,
    retry_delay=timedelta(minutes=2),
    # pool="sftp_transfer",  # Use pool to limit concurrent SFTP connections
)
def transfer_single_file(file_dict: dict) -> dict:
    """Transfer a single file from source to target.

    Loads configuration from Airflow Variables to avoid passing through XCom.

    Args:
        file_dict: File information dictionary.

    Returns:
        Dictionary with transfer result.
    """
    from datetime import datetime

    from sync_sftp.core.file_info import FileInfo
    from sync_sftp.repositories.sftp_repository import SFTPFileRepository
    from sync_sftp.services.file_sync_service import FileSyncService
    from sync_sftp.state.file_state_store import FileStateStore
    from sync_sftp.utils.config import get_sync_config

    # Load config (each task loads it independently)
    config = get_sync_config()

    # Reconstruct FileInfo from dict
    file_info = FileInfo(
        path=file_dict["path"],
        size=file_dict["size"],
        modified_at=datetime.fromisoformat(file_dict["modified_at"]),
    )

    # Create repositories
    source_repo = SFTPFileRepository(
        sftp_conn_id=config.source_conn_id,
        base_path=config.source_base_path,
    )
    target_repo = SFTPFileRepository(
        sftp_conn_id=config.target_conn_id,
        base_path=config.target_base_path,
    )

    # Create state store and sync service
    state_store = FileStateStore(state_key=config.state_key)
    sync_service = FileSyncService(
        source_repo=source_repo,
        target_repo=target_repo,
        state_store=state_store,
        chunk_size=config.chunk_size,
    )

    # Transfer file
    try:
        synced_file = sync_service.sync_file(file_info)
        return {
            "path": synced_file.path,
            "size": synced_file.size,
            "status": "success",
            "checksum": synced_file.checksum,
        }
    except Exception as e:
        return {
            "path": file_info.path,
            "size": file_info.size,
            "status": "failed",
            "error": str(e),
        }


@task(dag=dag)
def summarize_sync(results: list[dict]) -> dict:
    """Summarize sync operation results.

    Args:
        results: List of transfer results.

    Returns:
        Summary dictionary with statistics.
    """
    total = len(results)
    successful = sum(1 for r in results if r.get("status") == "success")
    failed = total - successful
    total_size = sum(r.get("size", 0) for r in results if r.get("status") == "success")

    summary = {
        "total_files": total,
        "successful": successful,
        "failed": failed,
        "total_size_bytes": total_size,
    }

    print(f"Sync Summary: {summary}")
    return summary


# Define task dependencies
config = init_config()
all_files = list_source_files(config)
new_files = filter_new_files(config, all_files)

# Use dynamic task mapping to create one task per file
# Each task loads config independently from Airflow Variables
transfer_results = transfer_single_file.expand(file_dict=new_files)
summary = summarize_sync(transfer_results)

# Set dependencies
config >> all_files >> new_files >> transfer_results >> summary
