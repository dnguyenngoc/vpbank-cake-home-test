# VPBank Cake Home Test - SFTP Synchronization DAG

## Tổng quan

Project này implement một Apache Airflow DAG để đồng bộ file một chiều từ SFTP server nguồn (`source`) sang SFTP server đích (`target`), đảm bảo giữ nguyên cấu trúc thư mục. DAG được thiết kế với abstraction layer cao để dễ dàng migrate sang Object Storage (S3, GCS) trong tương lai.

### Tính năng chính

- **Đồng bộ một chiều**: Chỉ sync từ source → target, không bao giờ ảnh hưởng ngược lại
- **Giữ nguyên cấu trúc thư mục**: File `sftp://source/a/b/c/file.txt` → `sftp://target/a/b/c/file.txt`
- **Không xóa file trên target**: Khi file bị xóa trên source, file trên target vẫn giữ nguyên
- **Phát hiện file đã thay đổi**: Tự động sync lại file đã thay đổi dựa trên checksum, size, hoặc modified_at
- **Streaming cho file lớn**: Xử lý file từ KB đến GB mà không load toàn bộ vào memory
- **Parallel processing**: Sử dụng Dynamic Task Mapping để transfer nhiều file đồng thời
- **State tracking**: Lưu trạng thái sync trong Airflow Variables để tránh transfer lại file đã sync

## Yêu cầu hệ thống

- Docker và Docker Compose
- Tối thiểu 4GB RAM
- Tối thiểu 2 CPUs
- Tối thiểu 10GB disk space

## Hướng dẫn Setup và Chạy

### 1. Clone repository

```bash
git clone https://github.com/dnguyenngoc/vpbank-cake-home-test.git
cd vpbank-cake-home-test
```

### 2. Tạo file `.env`

Copy file `.env.sample` và tạo file `.env`:

```bash
cp .env.sample .env
```

Chỉnh sửa file `.env` theo nhu cầu của bạn. Xem `.env.sample` để biết các biến môi trường có sẵn.

### 3. Khởi động services

```bash
docker-compose up -d
```

Lệnh này sẽ khởi động:
- **PostgreSQL**: Database backend cho Airflow
- **Redis**: Message broker cho Celery
- **Airflow Web Server**: UI tại http://localhost:8080
- **Airflow Scheduler**: Lên lịch và trigger DAGs
- **Airflow Worker**: Xử lý tasks (CeleryExecutor)
- **Airflow DAG Processor**: Xử lý DAG files
- **SFTP Source Server**: Test SFTP server tại port 2222
- **SFTP Target Server**: Test SFTP server tại port 2223

### 4. Truy cập Airflow UI

Mở trình duyệt và truy cập: http://localhost:8080

**Default credentials:**
- Username: `airflow`
- Password: `airflow`

### 5. Unpause và chạy DAG

1. Vào **DAGs** trong Airflow UI
2. Tìm DAG `sync_sftp_one_way`
3. Toggle switch để **unpause** DAG
4. DAG sẽ tự động chạy theo schedule (mỗi 5 phút) hoặc có thể trigger manual

### 6. Kiểm tra logs

- **DAG logs**: Vào DAG → Click vào task → View logs
- **Container logs**: `docker-compose logs -f airflow-scheduler` hoặc `docker-compose logs -f airflow-worker`

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

1. **init_config**: Load và validate configuration từ Airflow Variables
2. **list_source_files**: Liệt kê tất cả files từ source SFTP server
3. **filter_new_files**: Filter ra files mới hoặc đã thay đổi (chưa sync hoặc checksum/size/modified_at khác)
4. **transfer_single_file**: Transfer từng file (được map động cho mỗi file)
5. **summarize_sync**: Tổng hợp kết quả sync

## Assumptions, Decisions, và Trade-offs

### 1. Abstraction Layer - FileRepository Interface

**Decision**: Tách biệt logic khỏi implementation bằng interface `FileRepository`

**Lý do**:
- Dễ dàng migrate sang Object Storage (S3, GCS) mà không cần thay đổi code DAG
- Code DAG chỉ làm việc với interface, không biết backend là SFTP hay S3
- Dễ test bằng cách mock repository

**Trade-off**: 
- Thêm một layer abstraction (phức tạp hơn một chút)
- Đổi lại có flexibility cao và dễ maintain

### 2. State Management - Airflow Variables

**Decision**: Lưu sync state trong Airflow Variables (JSON format)

**Lý do**:
- Không cần thêm database table
- Dễ dàng inspect qua Airflow UI
- Đủ cho use case này (không cần query phức tạp)

**Trade-off**:
- Không scale tốt với hàng triệu files (JSON quá lớn)
- Không có transaction support
- **Giải pháp tương lai**: Có thể migrate sang database (PostgreSQL) nếu cần

### 3. Checksum Calculation

**Decision**: Tính checksum (MD5) trong quá trình transfer, không đọc file 2 lần

**Lý do**:
- Hiệu quả hơn (chỉ đọc file 1 lần)
- Verify integrity sau khi transfer
- Phát hiện file đã thay đổi (so sánh checksum)

**Trade-off**:
- Tốn CPU một chút để tính checksum
- Đổi lại có integrity verification và change detection

### 4. Change Detection Strategy

**Decision**: Sync cả file mới và file đã thay đổi

**Implementation**:
- Ưu tiên so sánh checksum (đáng tin cậy nhất)
- Fallback: so sánh size và modified_at nếu không có checksum
- Nếu khác → file cần được sync lại

**Lý do**:
- Đảm bảo target luôn có version mới nhất
- Tự động phát hiện file đã thay đổi trên source

### 5. Streaming Architecture

**Decision**: Sử dụng streaming với chunk size 64MB (configurable)

**Lý do**:
- Memory footprint cố định, không phụ thuộc file size
- Có thể xử lý file hàng GB mà không crash
- Có thể tune chunk_size để optimize performance

**Trade-off**:
- Phức tạp hơn so với đọc toàn bộ file vào memory
- Đổi lại có thể scale với file lớn

### 6. Dynamic Task Mapping

**Decision**: Sử dụng Dynamic Task Mapping của Airflow 3.x

**Lý do**:
- Mỗi file được transfer trong task riêng, có thể retry độc lập
- Có thể chạy parallel nhiều file cùng lúc
- Dễ dàng monitor progress từng file

**Trade-off**:
- Có thể tạo nhiều tasks nếu có hàng nghìn files
- **Giải pháp**: Giới hạn bằng `max_active_tasks=4` và có thể dùng pool

### 7. Connection Management

**Decision**: Mỗi SFTP operation mở và đóng connection riêng

**Lý do**:
- Tránh connection leak
- Tránh timeout issues với long-running connections
- Đảm bảo connection được đóng đúng cách (dùng context manager)

**Trade-off**:
- Overhead nhỏ khi mở/đóng connection nhiều lần
- Đổi lại có reliability cao hơn

### 8. Directory Structure Preservation

**Decision**: Tự động tạo parent directories khi ghi file

**Implementation**: `SFTPFileRepository.open_write()` tự động tạo parent directories nếu chưa tồn tại

**Lý do**:
- Đảm bảo cấu trúc thư mục được giữ nguyên
- Không cần pre-create directories

### 9. Error Handling

**Decision**: Mỗi file transfer có error handling riêng, không ảnh hưởng file khác

**Lý do**:
- Một file fail không block các file khác
- Có thể retry từng file độc lập
- Dễ debug với logs chi tiết

### 10. Executor - CeleryExecutor

**Decision**: Sử dụng CeleryExecutor với separate worker containers

**Lý do**:
- Scale out: có thể thêm workers để tăng throughput
- Isolation: worker crash không ảnh hưởng scheduler
- Production-ready: phù hợp với môi trường thực tế

**Trade-off**:
- Phức tạp hơn (cần Redis/RabbitMQ)
- Tốn tài nguyên hơn SequentialExecutor
- Đổi lại có scalability và production-readiness

## Xử lý Scale & Anomalies

### 1. File size tăng từ KB lên GB

**Giải pháp**:
- Streaming với chunk size 64MB (configurable)
- Memory footprint cố định, không phụ thuộc file size
- Checksum được tính trong quá trình transfer (không cần đọc file 2 lần)

**Monitoring**: Log file size trong mỗi transfer task, có thể set alert nếu file > threshold

### 2. Số lượng file tăng đột biến

**Giải pháp**:
- `max_active_tasks=4`: Giới hạn concurrent tasks
- `max_active_runs=1`: Chỉ 1 DAG run tại một thời điểm
- SFTP connections được quản lý tốt: mỗi operation mở/đóng connection riêng

**Nếu cần scale hơn**:
- Tăng số workers
- Batch files thành groups (mỗi group = 1 task)
- Sử dụng TaskGroup với concurrency limit

### 3. Network timeout / Connection lost

**Giải pháp**:
- Retry 3 lần với exponential backoff (2 phút)
- Mỗi file transfer độc lập → file khác vẫn tiếp tục
- Log chi tiết để debug

### 4. Disk space trên target

**Giải pháp**:
- Check disk space trước khi transfer (có thể thêm vào `FileSyncService`)
- Fail fast với error message rõ ràng
- Monitor disk usage qua Airflow metrics

## Extensibility

### Migration từ SFTP sang Object Storage

Để chuyển từ SFTP sang S3, chỉ cần:

1. Implement `S3FileRepository` implement interface `FileRepository`
2. Update DAG để dùng `S3FileRepository` thay vì `SFTPFileRepository`
3. Logic sync không cần thay đổi!

Xem chi tiết trong `airflow/dags/sync_sftp/README.md` section "Migration từ SFTP sang Object Storage".

### Thêm Transformation

Để thêm transformation (compress, encrypt, validate) trước khi ghi vào target:

1. Tạo transform function nhận `BinaryIO` và trả về `BinaryIO`
2. Pass vào `FileSyncService` qua parameter `transform_func`
3. Transform được apply trong quá trình streaming

Xem chi tiết trong `airflow/dags/sync_sftp/README.md` section "Thêm Transformation".

## Testing

Project đã có 2 SFTP test servers được cấu hình trong `docker-compose.yaml`:
- **Source**: `localhost:2222` (user: `testuser`, pass: `testpass`)
- **Target**: `localhost:2223` (user: `testuser`, pass: `testpass`)

### Mount Volumes cho Testing

Các SFTP servers mount local directories để dễ dàng test:

- **Source SFTP**: Thư mục `./sftp/source` được mount vào `/home/testuser` trong container `sftp-source`
- **Target SFTP**: Thư mục `./sftp/target` được mount vào `/home/testuser` trong container `sftp-target`

Bạn có thể thêm file vào `sftp/source/` trên host machine để test, và kiểm tra kết quả sync trong `sftp/target/` mà không cần SFTP client.

## Troubleshooting

### DAG không xuất hiện trong UI

- Kiểm tra logs: `docker-compose logs airflow-scheduler`
- Kiểm tra DAG file có syntax error không
- Đảm bảo DAG file nằm trong `airflow/dags/`

### Connection failed

- Kiểm tra SFTP servers đang chạy: `docker-compose ps`
- Kiểm tra connection config trong Airflow UI
- Test connection bằng SFTP client: `sftp -P 2222 testuser@localhost`

### Tasks failed

- Xem logs chi tiết trong Airflow UI
- Kiểm tra Airflow Variables đã được set đúng chưa
- Kiểm tra disk space trên target server

### State không được lưu

- Kiểm tra Airflow Variables có được tạo chưa
- Kiểm tra permissions của Airflow user
- Xem logs của task `transfer_single_file`

## Additional Resources

- Chi tiết thiết kế: Xem `airflow/dags/sync_sftp/README.md`
- Airflow documentation: https://airflow.apache.org/docs/
- SFTP provider: https://airflow.apache.org/docs/apache-airflow-providers-sftp/

## License

This project is created for VPBank Cake home test interview.
