# Storage Backends - Lưu Trữ Dữ Liệu

## Chính sách hiện tại
- **Metadata thread**: Firestore.
- **Media/file**:
  1. Ưu tiên MongoDB GridFS (bền vững, lâu dài).
  2. Fallback sang local cache nếu GridFS không dùng được (tạm thời, session-scope).
  3. **Không lưu cùng asset vào 2 backend cùng lúc** → Một backend ownership duy nhất.

---

## Firestore (Metadata)
**Dùng cho**: Thread title, chat history, think events, thông tin session.

**Credentials cần**:
- `FIREBASE_PROJECT_ID`
- `FIREBASE_CREDENTIALS_JSON`
- `FIREBASE_DATABASE_ID` (nếu không dùng default Firestore database)
- `FIREBASE_CHAT_NAMESPACE`

**Tính chất**:
- Chỉ lưu metadata **không chứa binary media**.
- Đấu nối lâu dài, dự kiến reliable.

---

## MongoDB GridFS (Primary Media Storage)
**Dùng cho**: Ảnh biểu đồ, file upload, media per thread.

**Credentials cần**:
- `MONGODB_URI_TEMPLATE`
- `MONGODB_PASSWORD` (nếu URI có placeholder `<db_password>`)
- `MONGODB_DB_NAME`
- `MONGODB_COLLECTION_NAME` (cho Knowledge Base)

**Bucket**: Media lưu trong GridFS bucket `chat_media` cùng database.

**Tính chất**:
- Bền vững, lâu dài.
- Mỗi file có `asset_id` duy nhất.

---

## Local Cache Fallback
**Dùng khi**: MongoDB GridFS tạm lỗi hoặc không dùng được.

**Đặc điểm**:
- Lưu vào `artifacts/chatbox_cache/media_fallback/`.
- **Tính chất tạm thời** → Không thay thế backend bền vững.
- Session-scoped: Khi khởi động lại, cache cũ có thể mất.
- **Phải báo rõ cho user** nếu cache được dùng (không yên tâm).

**Khi báo lỗi**:
```
"⚠️ MongoDB không available. Media sẽ lưu local tạm thời, 
có thể mất khi restart. Vui lòng kiểm tra MongoDB."
```

---

## Transparency Rules (Từ CHATBOX_OPERATIONS.md)

### Khi persistence thành công
- Không cần báo.
- File/image lưu bình thường vào backend.

### Khi persistence thất bại
- **PHẢI báo rõ cho user**: "⚠️ Không thể lưu file lâu dài. Sẽ dùng cache tạm."
- Giải thích: MongoDB down, S3 timeout, etc.
- **Không bịa đặt** rằng file đã lưu lâu dài nếu chỉ có cache.

### Khi fallback được kích hoạt
- Log ra: "GridFS unavailable, using local cache."
- Tùy chọn: Gợi ý user kiểm tra MongoDB hoặc configure backend thay thế.

---

## Nếu cả MongoDB và local cache đều không khả dụng
Khuyến nghị dùng object storage (bền vững, dễ scale):

### Supabase Storage
**Credentials cần**:
- `SUPABASE_URL`
- `SUPABASE_SERVICE_ROLE_KEY`
- `SUPABASE_STORAGE_BUCKET`

**Phù hợp khi**: Cần API đơn giản, signed URL, file tập trung.

### S3-compatible (AWS S3, Cloudflare R2, MinIO, B2)
**Credentials cần**:
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_REGION`
- `S3_BUCKET`
- `S3_ENDPOINT_URL` (nếu không AWS S3 chuẩn)

**Phù hợp khi**: File bền vững, scale lớn, tách biệt DB nghiệp vụ.

---

## Quy tắc Chọn Backend Thay Thế

| Yêu cầu | Backend | Ghi chú |
|--------|---------|--------|
| Ít thay đổi, DB nhỏ | MongoDB GridFS | Default hiện tại |
| File bền vững, API tốt | Supabase Storage | Dễ integrate |
| File bền vững, scale lớn | S3-compatible | Production-ready |
| Testing, development | Local cache | Không dùng production |

---

## Implementation Notes

### Single Backend Ownership
- Một file → Một backend duy nhất.
- Không lưu redundant vào 2 backend cùng lúc.
- Fallback → Chuyển từ GridFS sang cache, không copy.

### Error Handling Pattern
```python
try:
    # Lưu vào GridFS
    asset_id = upload_to_gridfs(file_data)
except Exception as e:
    # Fallback sang local cache
    asset_id = save_to_local_cache(file_data)
    # ⚠️ PHẢI báo cho user
    print("⚠️ MongoDB không available. Sử dụng cache tạm.")
    logger.warning(f"GridFS fallback: {e}")
```

### Checking Persistence Success
**Sau khi upload file**:
1. Kiểm tra asset có được lưu vào GridFS không.
2. Nếu lỗi → Kiểm tra local cache đã có không.
3. Nếu cả 2 đều fail → **Báo lỗi cho user**, không giả lập.
