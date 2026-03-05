# Thiết kế DAG SFTP Sync - Giải pháp cho bài toán đồng bộ file một chiều

## Tổng quan bài toán

Bài toán yêu cầu xây dựng một DAG Apache Airflow để đồng bộ file từ SFTP server nguồn (`source`) sang SFTP server đích (`target`) với các yêu cầu:

1. **Đồng bộ một chiều**: Chỉ sync từ source → target, không bao giờ ảnh hưởng ngược lại
2. **Giữ nguyên cấu trúc thư mục**: File `sftp://source/a/b/c/file.txt` → `sftp://target/a/b/c/file.txt`
3. **Không xóa file trên target**: Khi file bị xóa trên source, file trên target vẫn giữ nguyên
4. **Chỉ sync file mới**: Tránh transfer lại file đã được sync trước đó

## Kiến trúc giải pháp

### 1. Abstraction Layer - Tách biệt logic khỏi implementation

**Vấn đề**: Nếu code DAG phụ thuộc trực tiếp vào SFTP, khi cần chuyển sang Object Storage (S3, GCS) sẽ phải viết lại toàn bộ logic.

**Giải pháp**: Thiết kế **FileRepository Interface** (`core/file_repository.py`) với các phương thức:
- `list_files(base_path) -> List[FileInfo]`: Liệt kê file
- `open_read(path) -> BinaryIO`: Đọc file dạng stream
- `open_write(path) -> BinaryIO`: Ghi file dạng stream
- `exists(path) -> bool`: Kiểm tra file tồn tại
- `get_file_info(path) -> FileInfo`: Lấy metadata file

**Lợi ích**:
- DAG chỉ làm việc với interface, không biết backend là SFTP hay S3
- Để chuyển sang S3, chỉ cần implement `S3FileRepository` implement interface này
- Code DAG không cần thay đổi

### 2. State Management - Tracking files đã sync

**Vấn đề**: Làm sao biết file nào đã được sync để tránh transfer lại?

**Giải pháp**: Sử dụng **FileStateStore** (`state/file_state_store.py`) lưu trạng thái trong Airflow Variables:
- Mỗi file sau khi sync thành công được lưu với metadata (path, size, modified_at, checksum)
- Trước khi sync, check state store để filter ra file mới hoặc file đã thay đổi
- So sánh checksum (nếu có), nếu không có thì so sánh size và modified_at
- State được lưu dạng JSON trong Airflow Variables (có thể migrate sang database sau)

**Lợi ích**:
- Không cần scan lại toàn bộ file trên target
- Có thể verify integrity bằng checksum
- Dễ dàng clear state nếu cần re-sync

### 3. Streaming Architecture - Xử lý file lớn

**Vấn đề**: File có thể từ KB lên GB, không thể load toàn bộ vào memory.

**Giải pháp**: 
- **FileSyncService** (`services/file_sync_service.py`) sử dụng streaming:
  - Đọc file từ source theo chunk (mặc định 64MB)
  - Ghi trực tiếp vào target mà không lưu vào memory
  - Hỗ trợ transform hook để thêm compression/encryption nếu cần

**Implementation**:
```python
md5_hash = hashlib.md5()
with source_repo.open_read(path) as source_stream:
    transformed_stream = transform_func(source_stream)  # Apply transform if needed
    with target_repo.open_write(path) as target_stream:
        while True:
            chunk = transformed_stream.read(chunk_size)
            if not chunk:
                break
            md5_hash.update(chunk)  # Calculate checksum while transferring
            target_stream.write(chunk)
checksum = md5_hash.hexdigest()  # Store checksum for integrity verification
```

**Lưu ý**:
- Checksum (MD5) được tính toán trong quá trình transfer để verify integrity
- Parent directories được tự động tạo nếu chưa tồn tại (trong `SFTPFileRepository.open_write()`)
- Transform function có thể được áp dụng trong quá trình streaming

**Lợi ích**:
- Memory footprint cố định, không phụ thuộc file size
- Có thể xử lý file hàng GB mà không crash
- Có thể tune chunk_size để optimize performance

### 4. Dynamic Task Mapping - Parallel processing

**Vấn đề**: Có thể có hàng trăm file cần sync, nếu làm tuần tự sẽ rất chậm.

**Giải pháp**: Sử dụng **Dynamic Task Mapping** của Airflow 3.x:
- Task `list_source_files` trả về list tất cả file
- Task `filter_new_files` filter ra file mới
- Task `transfer_single_file` được map động cho từng file → tạo parallel tasks

**Flow**:
```
init_config → list_source_files → filter_new_files → [transfer_single_file x N] → summarize_sync
```

**Lợi ích**:
- Mỗi file được transfer trong task riêng, có thể retry độc lập
- Có thể chạy parallel nhiều file cùng lúc (giới hạn bởi `max_active_tasks`)
- Dễ dàng monitor progress từng file

### 5. Error Handling & Retry Strategy

**Vấn đề**: Network có thể bị gián đoạn, SFTP connection có thể timeout.

**Giải pháp**:
- Mỗi `transfer_single_file` task có retry 3 lần với exponential backoff
- Sử dụng **pool** (`sftp_transfer`) để limit concurrent connections (tránh quá tải SFTP server)
- Task failed không block các task khác (continue on failure)

**Configuration**:
```python
@task(
    retries=3,
    retry_delay=timedelta(minutes=2),
    # pool="sftp_transfer",  # Có thể uncomment để limit concurrent SFTP connections
)
```

**Lưu ý**: Pool `sftp_transfer` hiện đang bị comment trong code. Nếu cần giới hạn số lượng kết nối SFTP đồng thời, có thể uncomment dòng này và tạo pool tương ứng trong Airflow.

### 6. Extensibility - Transform Hook

**Vấn đề**: Sau này có thể cần thêm transformation (compress, encrypt, validate) trước khi ghi vào target.

**Giải pháp**: `FileSyncService` có parameter `transform_func`:
- Mặc định là identity function (không làm gì)
- Có thể truyền custom function để transform stream
- Transform được apply trong quá trình streaming (không cần buffer toàn bộ file)

**Ví dụ thêm compression**:
```python
def compress_stream(stream_in):
    return gzip.GzipFile(fileobj=stream_in)

sync_service = FileSyncService(
    ...,
    transform_func=compress_stream
)
```

## Cấu trúc code

```
sync_sftp/
├── sync_sftp_directly.py      # DAG chính
├── core/                       # Abstractions
│   ├── file_info.py           # FileInfo dataclass
│   └── file_repository.py     # FileRepository interface
├── repositories/              # Implementations
│   └── sftp_repository.py     # SFTPFileRepository
├── services/                  # Business logic
│   └── file_sync_service.py  # FileSyncService
├── state/                     # State management
│   └── file_state_store.py   # FileStateStore
└── utils/                     # Utilities
    └── config.py             # Configuration loader
```

## Design Decisions & Trade-offs

### 1. Tại sao dùng Airflow Variables thay vì Database?

**Decision**: Lưu state trong Airflow Variables (JSON)

**Pros**:
- Không cần thêm database table
- Dễ dàng inspect qua Airflow UI
- Đủ cho use case này (không cần query phức tạp)

**Cons**:
- Không scale tốt với hàng triệu file (JSON quá lớn)
- Không có transaction support

**Trade-off**: Chấp nhận limitation này vì bài toán chỉ cần track file đã sync, không cần query phức tạp. Có thể migrate sang database sau nếu cần.

### 2. Tại sao không dùng Airflow XCom để pass config?

**Decision**: Mỗi task load config từ Airflow Variables độc lập

**Pros**:
- Tránh XCom size limit khi pass config qua nhiều tasks
- Tasks độc lập, dễ test
- Config có thể thay đổi mà không cần re-run DAG

**Cons**:
- Mỗi task phải đọc Variables (overhead nhỏ)

**Trade-off**: Overhead nhỏ nhưng đổi lại flexibility cao hơn.

### 3. Tại sao dùng CeleryExecutor thay vì SequentialExecutor?

**Decision**: CeleryExecutor với separate worker containers

**Pros**:
- Scale out: có thể thêm workers để tăng throughput
- Isolation: worker crash không ảnh hưởng scheduler
- Production-ready: phù hợp với môi trường thực tế

**Cons**:
- Phức tạp hơn (cần Redis/RabbitMQ)
- Tốn tài nguyên hơn

**Trade-off**: Chấp nhận complexity để có scalability và production-readiness.

### 4. Sync file mới và file đã thay đổi

**Decision**: Sync cả file mới và file đã thay đổi (dựa trên checksum, size, hoặc modified_at)

**Implementation**:
- `FileStateStore.is_synced()` kiểm tra:
  1. Nếu có checksum: so sánh checksum (đáng tin cậy nhất)
  2. Nếu không có checksum: so sánh size và modified_at
  3. Nếu khác → file cần được sync lại

**Lợi ích**:
- Tự động phát hiện file đã thay đổi trên source
- Đảm bảo target luôn có version mới nhất
- Checksum giúp verify integrity và phát hiện corruption

## Xử lý Scale & Anomalies

### 1. File size tăng từ KB lên GB

**Vấn đề**: File lớn có thể làm crash nếu load vào memory.

**Giải pháp đã implement**:
- Streaming với chunk size 64MB (configurable qua Airflow Variable `sftp_sync_chunk_size`)
- Memory footprint cố định, không phụ thuộc file size
- Checksum được tính toán trong quá trình transfer (không cần đọc file 2 lần)
- Parent directories được tự động tạo khi ghi file vào target

**Monitoring**:
- Log file size trong mỗi transfer task
- Có thể set alert nếu file > threshold

### 2. Số lượng file tăng đột biến

**Vấn đề**: Hàng nghìn file → tạo hàng nghìn tasks → quá tải scheduler.

**Giải pháp**:
- `max_active_tasks=4`: Giới hạn concurrent tasks
- `pool="sftp_transfer"`: Có thể uncomment trong code để giới hạn concurrent SFTP connections (hiện đang bị comment)
- `max_active_runs=1`: Chỉ 1 DAG run tại một thời điểm
- SFTP connections được quản lý tốt: mỗi operation mở/đóng connection riêng, tránh connection leak

**Nếu cần scale hơn**:
- Tăng số workers
- Batch files thành groups (mỗi group = 1 task)
- Sử dụng TaskGroup với concurrency limit

### 3. Network timeout / Connection lost

**Vấn đề**: SFTP connection có thể bị timeout khi transfer file lớn.

**Giải pháp**:
- Retry 3 lần với exponential backoff
- Mỗi file transfer độc lập → file khác vẫn tiếp tục
- Log chi tiết để debug

### 4. Disk space trên target

**Vấn đề**: Target có thể hết disk space.

**Giải pháp**:
- Check disk space trước khi transfer (có thể thêm vào `FileSyncService`)
- Fail fast với error message rõ ràng
- Monitor disk usage qua Airflow metrics

## Migration từ SFTP sang Object Storage

### Scenario: Chuyển từ SFTP sang S3

**Bước 1**: Implement `S3FileRepository`:
```python
class S3FileRepository(FileRepository):
    def __init__(self, bucket: str, prefix: str = ""):
        self.s3_client = boto3.client('s3')
        self.bucket = bucket
        self.prefix = prefix.rstrip("/")
    
    def list_files(self, base_path: str = "") -> list[FileInfo]:
        # List objects từ S3, trả về list[FileInfo]
        ...
    
    def open_read(self, path: str) -> BinaryIO:
        # Trả về file-like object stream từ S3 (context manager)
        # Có thể dùng boto3's StreamingBody wrapped trong BytesIO
        ...
    
    def open_write(self, path: str) -> BinaryIO:
        # Trả về file-like object để upload lên S3 (context manager)
        # Có thể buffer trong BytesIO rồi upload khi __exit__
        ...
    
    def exists(self, path: str) -> bool:
        # Check object tồn tại trên S3
        ...
    
    def get_file_info(self, path: str) -> FileInfo | None:
        # Get object metadata từ S3
        ...
```

**Bước 2**: Update DAG để dùng S3Repository:
```python
# Chỉ cần thay đổi này
source_repo = S3FileRepository(bucket="source-bucket")
target_repo = S3FileRepository(bucket="target-bucket")
```

**Bước 3**: Logic sync không cần thay đổi!

**Lợi ích của abstraction**:
- Code DAG không đổi
- `FileSyncService` không đổi
- Chỉ cần implement interface mới

## Thêm Transformation

### Scenario: Cần compress file trước khi sync

**Bước 1**: Tạo transform function:
```python
import gzip
import io

def compress_transform(stream_in: BinaryIO) -> BinaryIO:
    """Compress stream using gzip.
    
    Lưu ý: Function này cần trả về file-like object có thể đọc được.
    Nếu cần streaming compression, có thể dùng gzip.GzipFile với mode='rb'.
    """
    # Option 1: Buffer toàn bộ rồi compress (cho file nhỏ)
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode='wb') as gz:
        gz.write(stream_in.read())
    compressed.seek(0)
    return compressed
    
    # Option 2: Streaming compression (cho file lớn)
    # Cần implement custom wrapper để stream compress
```

**Bước 2**: Pass vào FileSyncService:
```python
sync_service = FileSyncService(
    source_repo=source_repo,
    target_repo=target_repo,
    state_store=state_store,
    transform_func=compress_transform  # Chỉ thêm dòng này
)
```

**Lợi ích**:
- Không cần thay đổi DAG
- Transform được apply trong quá trình streaming
- Có thể chain nhiều transforms nếu cần

## Implementation Details

### Checksum Calculation
- Checksum (MD5) được tính toán trong quá trình transfer để tránh đọc file 2 lần
- Checksum được lưu trong state store để verify integrity
- Khi check file đã sync, ưu tiên so sánh checksum (đáng tin cậy nhất)

### Directory Structure Preservation
- `SFTPFileRepository.open_write()` tự động tạo parent directories nếu chưa tồn tại
- Đảm bảo cấu trúc thư mục được giữ nguyên khi sync

### Connection Management
- Mỗi SFTP operation mở và đóng connection riêng
- Sử dụng context manager để đảm bảo connection được đóng đúng cách
- Tránh connection leak và timeout issues

### Error Handling
- Mỗi file transfer có error handling riêng, không ảnh hưởng file khác
- Failed transfers được log chi tiết để debug
- State chỉ được update khi transfer thành công

## Kết luận

Giải pháp này đạt được:

**Abstraction**: Dễ dàng migrate sang Object Storage  
**Extensibility**: Dễ dàng thêm transformation  
**Scalability**: Xử lý được file lớn và số lượng file nhiều  
**Reliability**: Retry, error handling, state tracking với checksum verification  
**Maintainability**: Code clean, tách biệt concerns, dễ test  
**Change Detection**: Tự động phát hiện và sync file đã thay đổi  

Đây là foundation tốt để mở rộng thành production-ready solution khi cần.
