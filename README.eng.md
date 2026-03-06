# VPBank Cake Home Test - SFTP Synchronization DAG

## Overview

This project implements an Apache Airflow DAG to synchronize files one-way from a source SFTP server (`source`) to a target SFTP server (`target`), ensuring the original directory structure is preserved. The DAG is designed with a high abstraction layer to easily migrate to Object Storage (S3, GCS) in the future.

### Key Features

- **One-way synchronization**: Only syncs from source → target, never affects the source
- **Preserves directory structure**: File `sftp://source/a/b/c/file.txt` → `sftp://target/a/b/c/file.txt`
- **Does not delete files on target**: When a file is deleted on source, the file on target remains intact
- **Detects changed files**: Automatically re-syncs changed files based on checksum, size, or modified_at
- **Streaming for large files**: Handles files from KB to GB without loading the entire file into memory
- **Parallel processing**: Uses Dynamic Task Mapping to transfer multiple files simultaneously
- **State tracking**: Stores sync state in Airflow Variables to avoid re-transferring already synced files

## System Requirements

- Docker and Docker Compose
- Minimum 4GB RAM
- Minimum 2 CPUs
- Minimum 10GB disk space

## Setup and Run Guide

### 1. Clone repository

```bash
git clone https://github.com/dnguyenngoc/vpbank-cake-home-test.git
cd vpbank-cake-home-test
```

### 2. Create `.env` file

Copy the `.env.sample` file and create a `.env` file:

```bash
cp .env.sample .env
```

Edit the `.env` file according to your needs. See `.env.sample` for available environment variables.

### 3. Start services

```bash
docker-compose up -d
```

This command will start:
- **PostgreSQL**: Database backend for Airflow
- **Redis**: Message broker for Celery
- **Airflow Web Server**: UI at http://localhost:8080
- **Airflow Scheduler**: Schedules and triggers DAGs
- **Airflow Worker**: Processes tasks (CeleryExecutor)
- **Airflow DAG Processor**: Processes DAG files
- **SFTP Source Server**: Test SFTP server on port 2222
- **SFTP Target Server**: Test SFTP server on port 2223

### 4. Access Airflow UI

Open your browser and navigate to: http://localhost:8080

**Default credentials:**
- Username: `airflow`
- Password: `airflow`

### 5. Unpause and run DAG

1. Go to **DAGs** in the Airflow UI
2. Find the `sync_sftp_one_way` DAG
3. Toggle the switch to **unpause** the DAG
4. The DAG will automatically run on schedule (every 5 minutes) or can be manually triggered

### 6. Check logs

- **DAG logs**: Go to DAG → Click on task → View logs
- **Container logs**: `docker-compose logs -f airflow-scheduler` or `docker-compose logs -f airflow-worker`

## DAG Flow

```
init_config 
    ↓
list_source_files 
    ↓
filter_new_files 
    ↓
[transfer_single_file x N] (Dynamic Task Mapping)
    ↓
summarize_sync
```

### Task Descriptions

1. **init_config**: Load and validate configuration from Airflow Variables
2. **list_source_files**: List all files from the source SFTP server
3. **filter_new_files**: Filter out new or changed files (not yet synced or checksum/size/modified_at differs)
4. **transfer_single_file**: Transfer each file (dynamically mapped for each file)
5. **summarize_sync**: Summarize sync results

## Assumptions, Decisions, and Trade-offs

### 1. Abstraction Layer - FileRepository Interface

**Decision**: Separate logic from implementation using the `FileRepository` interface

**Reasons**:
- Easy to migrate to Object Storage (S3, GCS) without changing DAG code
- DAG code only works with the interface, doesn't know if backend is SFTP or S3
- Easy to test by mocking the repository

**Trade-off**: 
- Adds an abstraction layer (slightly more complex)
- In return, provides high flexibility and easier maintenance

### 2. State Management - Airflow Variables

**Decision**: Store sync state in Airflow Variables (JSON format)

**Reasons**:
- No need for additional database tables
- Easy to inspect via Airflow UI
- Sufficient for this use case (no complex queries needed)

**Trade-off**:
- Doesn't scale well with millions of files (JSON too large)
- No transaction support
- **Future solution**: Can migrate to database (PostgreSQL) if needed

### 3. Checksum Calculation

**Decision**: Calculate checksum (MD5) during transfer, don't read file twice

**Reasons**:
- More efficient (only read file once)
- Verify integrity after transfer
- Detect changed files (compare checksum)

**Trade-off**:
- Slight CPU cost to calculate checksum
- In return, provides integrity verification and change detection

### 4. Change Detection Strategy

**Decision**: Sync both new files and changed files

**Implementation**:
- Prioritize checksum comparison (most reliable)
- Fallback: compare size and modified_at if checksum not available
- If different → file needs to be re-synced

**Reasons**:
- Ensures target always has the latest version
- Automatically detects files changed on source

### 5. Streaming Architecture

**Decision**: Use streaming with chunk size 64MB (configurable)

**Reasons**:
- Fixed memory footprint, independent of file size
- Can handle GB-sized files without crashing
- Can tune chunk_size to optimize performance

**Trade-off**:
- More complex than reading entire file into memory
- In return, can scale with large files

### 6. Dynamic Task Mapping

**Decision**: Use Dynamic Task Mapping from Airflow 3.x

**Reasons**:
- Each file is transferred in a separate task, can retry independently
- Can run multiple files in parallel
- Easy to monitor progress per file

**Trade-off**:
- Can create many tasks if there are thousands of files
- **Solution**: Limit with `max_active_tasks=4` and can use pools

### 7. Connection Management

**Decision**: Each SFTP operation opens and closes its own connection

**Reasons**:
- Avoid connection leaks
- Avoid timeout issues with long-running connections
- Ensure connections are properly closed (using context manager)

**Trade-off**:
- Small overhead when opening/closing connections multiple times
- In return, provides higher reliability

### 8. Directory Structure Preservation

**Decision**: Automatically create parent directories when writing files

**Implementation**: `SFTPFileRepository.open_write()` automatically creates parent directories if they don't exist

**Reasons**:
- Ensures directory structure is preserved
- No need to pre-create directories

### 9. Error Handling

**Decision**: Each file transfer has its own error handling, doesn't affect other files

**Reasons**:
- One file failure doesn't block other files
- Can retry each file independently
- Easy to debug with detailed logs

### 10. Executor - CeleryExecutor

**Decision**: Use CeleryExecutor with separate worker containers

**Reasons**:
- Scale out: can add workers to increase throughput
- Isolation: worker crash doesn't affect scheduler
- Production-ready: suitable for real-world environments

**Trade-off**:
- More complex (needs Redis/RabbitMQ)
- More resource-intensive than SequentialExecutor
- In return, provides scalability and production-readiness

## Scale & Anomaly Handling

### 1. File size increases from KB to GB

**Solution**:
- Streaming with chunk size 64MB (configurable)
- Fixed memory footprint, independent of file size
- Checksum calculated during transfer (no need to read file twice)

**Monitoring**: Log file size in each transfer task, can set alert if file > threshold

### 2. Sudden increase in number of files

**Solution**:
- `max_active_tasks=4`: Limit concurrent tasks
- `max_active_runs=1`: Only 1 DAG run at a time
- SFTP connections well managed: each operation opens/closes its own connection

**If more scaling needed**:
- Increase number of workers
- Batch files into groups (each group = 1 task)
- Use TaskGroup with concurrency limit

### 3. Network timeout / Connection lost

**Solution**:
- Retry 3 times with exponential backoff (2 minutes)
- Each file transfer is independent → other files continue
- Detailed logs for debugging

### 4. Disk space on target

**Solution**:
- Check disk space before transfer (can be added to `FileSyncService`)
- Fail fast with clear error message
- Monitor disk usage via Airflow metrics

## Extensibility

### Migration from SFTP to Object Storage

To migrate from SFTP to S3, simply:

1. Implement `S3FileRepository` implementing the `FileRepository` interface
2. Update DAG to use `S3FileRepository` instead of `SFTPFileRepository`
3. Sync logic doesn't need to change!

See details in `airflow/dags/sync_sftp/README.md` section "Migration from SFTP to Object Storage".

### Adding Transformation

To add transformation (compress, encrypt, validate) before writing to target:

1. Create a transform function that takes `BinaryIO` and returns `BinaryIO`
2. Pass to `FileSyncService` via `transform_func` parameter
3. Transform is applied during streaming

See details in `airflow/dags/sync_sftp/README.md` section "Adding Transformation".

## Testing

The project has 2 SFTP test servers configured in `docker-compose.yaml`:
- **Source**: `localhost:2222` (user: `testuser`, pass: `testpass`)
- **Target**: `localhost:2223` (user: `testuser`, pass: `testpass`)

### Mount Volumes for Testing

SFTP servers mount local directories for easy testing:

- **Source SFTP**: Directory `./sftp/source` is mounted to `/home/testuser` in the `sftp-source` container
- **Target SFTP**: Directory `./sftp/target` is mounted to `/home/testuser` in the `sftp-target` container

You can add files to `sftp/source/` on the host machine to test, and check sync results in `sftp/target/` without needing an SFTP client.

## Troubleshooting

### DAG not appearing in UI

- Check logs: `docker-compose logs airflow-scheduler`
- Check if DAG file has syntax errors
- Ensure DAG file is in `airflow/dags/`

### Connection failed

- Check if SFTP servers are running: `docker-compose ps`
- Check connection config in Airflow UI
- Test connection using SFTP client: `sftp -P 2222 testuser@localhost`

### Tasks failed

- View detailed logs in Airflow UI
- Check if Airflow Variables are set correctly
- Check disk space on target server

### State not saved

- Check if Airflow Variables are created
- Check Airflow user permissions
- View logs of `transfer_single_file` task

## Additional Resources

- Design details: See `airflow/dags/sync_sftp/README.md`
- Airflow documentation: https://airflow.apache.org/docs/
- SFTP provider: https://airflow.apache.org/docs/apache-airflow-providers-sftp/

## License

This project is created for VPBank Cake home test interview.
