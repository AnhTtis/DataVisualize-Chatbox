# Storage Backends

## Chính sách hiện tại
- Metadata thread: Firestore.
- Media/file:
  1. Ưu tiên MongoDB GridFS.
  2. Nếu MongoDB GridFS không dùng được, fallback sang local cache để vẫn hiển thị được trong UI.
  3. Không ghi cùng một asset vào nhiều backend cùng lúc.

## Firestore metadata
- Firestore chỉ dùng cho metadata thread/chat/event, không lưu binary media.
- Credentials/environment nên có:
  - `FIREBASE_PROJECT_ID`
  - `FIREBASE_CREDENTIALS_JSON`
  - `FIREBASE_DATABASE_ID` nếu không dùng default Firestore database
  - `FIREBASE_CHAT_NAMESPACE`

## MongoDB GridFS (Primary)
- Dùng làm backend chính để lưu ảnh biểu đồ và file upload theo thread.
- Credentials/environment cần có:
  - `MONGODB_URI_TEMPLATE`
  - `MONGODB_PASSWORD` nếu URI dùng placeholder `<db_password>`
  - `MONGODB_DB_NAME`
  - `MONGODB_COLLECTION_NAME` cho Knowledge Base
- GridFS sẽ lưu media trong cùng database MongoDB nhưng bucket riêng `chat_media`.

## Local cache fallback
- Khi MongoDB media tạm lỗi, hệ thống lưu file vào local cache để sidebar image/file history vẫn hiển thị.
- Backend này mang tính session/runtime, không thay thế cho lưu trữ lâu dài.
- Đường dẫn cache mặc định: `artifacts/chatbox_cache/media_fallback/`.

## Nếu cả MongoDB và local cache đều không khả dụng
- Khuyến nghị ưu tiên một object storage thay cho database thuần. Hai lựa chọn thực tế:

### Supabase Storage
- Credentials cần có:
  - `SUPABASE_URL`
  - `SUPABASE_SERVICE_ROLE_KEY`
  - `SUPABASE_STORAGE_BUCKET`
- Phù hợp nếu cần API đơn giản, signed URL và quản lý file tập trung.

### S3-compatible object storage
- Áp dụng cho AWS S3, Cloudflare R2, MinIO, Backblaze B2 S3 API.
- Credentials cần có:
  - `AWS_ACCESS_KEY_ID`
  - `AWS_SECRET_ACCESS_KEY`
  - `AWS_REGION`
  - `S3_BUCKET`
  - `S3_ENDPOINT_URL` nếu không phải AWS S3 chuẩn
- Phù hợp nếu muốn backend lưu file bền vững và dễ scale.

## Quy tắc chọn backend thay thế
- Nếu muốn ít thay đổi nhất với app hiện tại: dùng MongoDB GridFS.
- Nếu hệ thống cần lưu file bền vững, signed URL tốt và tách biệt khỏi DB nghiệp vụ: dùng S3-compatible storage hoặc Supabase Storage.
