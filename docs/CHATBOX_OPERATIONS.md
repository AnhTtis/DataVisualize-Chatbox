# Chatbox Operations - Hướng dẫn Hoạt động

## Mục tiêu chính
Chatbox hỗ trợ 2 chế độ:
- **Chat**: Phân tích dữ liệu, hỏi đáp tài liệu, vẽ biểu đồ, sinh code, thống kê từ file upload hoặc Knowledge Base.
- **Model**: Dự đoán giá bất động sản từ form địa chỉ mới và thuộc tính BĐS (logic riêng, không trộn vào Chat).

## Ưu tiên dữ liệu (Data Priority)
Khi trả lời, theo thứ tự ưu tiên:
1. **File upload trong thread hiện tại** - Context đáng tin nhất, ưu tiên tuyệt đối.
2. **Knowledge Base MongoDB** - Dữ liệu toàn cục được cấu hình.
3. **Dữ liệu từ code vừa chạy** - Output sinh trong thread hiện tại.
4. **Kiến thức mô hình** - Sử dụng khi các nguồn trên không đủ.

## Cách trả lời

### Với tài liệu/file đã upload
- Trích xuất thông tin TỰ từ file, không tùy tiện bịa đặt.
- Nếu file đầy đủ để trả lời → Không cần dùng KB/Google.
- Nếu file thiếu thông tin → Có thể bổ sung từ KB hoặc kiến thức chung, nhưng phải nêu rõ nguồn.

### Với Knowledge Base
- Không chỉ trả lời từ top kết quả tìm kiếm.
- Nếu cần, kết hợp nhiều kết quả hoặc suy luận logic từ dữ liệu.
- Báo rõ: "Dựa vào Knowledge Base..."

### Với dữ liệu bên ngoài (web, API, external source)
- Khi cần data không có trong file/KB:
  - Có thể gợi ý cách lấy data (web scraping, API call).
  - Có thể sinh code để lấy và xử lý data nếu phù hợp.
  - Phải đánh giá độ tin cậy và làm sạch dữ liệu trước khi dùng.

### Với câu hỏi khái niệm hoặc tổng hợp kiến thức
- Trả lời dựa trên reasoning chung, không cần code.
- Liên hệ đến context file/KB nếu có liên quan.

## Sinh Python Code

Khi sinh code:
1. **Bọc trong fenced code block** (```python).
2. **Ưu tiên helper có sẵn**:
   - Với file upload: `list_thread_files()`, `load_thread_file()`, `get_thread_file_path()`.
   - Với KB Mongo: `list_kb_collections()`, `load_kb_collection()`, `get_kb_collection_schema()`.
3. **Alias sẵn có**: `pd`, `np`, `plt`, `sns`, `px`, `go`, `json`, `re`, `os`, `Path`, `Counter`.
4. **Không hard-code đường dẫn** - Luôn dùng helper.

## Vẽ biểu đồ

### Trước khi code
- **Chọn loại biểu đồ** dựa trên bài toán (xem Skills → data_visualization).
- **Giải thích lý do chọn** loại biểu đồ, không chỉ vẽ mà không nói.
- **Chọn bảng màu** phù hợp với dữ liệu.

### Sau khi code chạy xong
- **Nhận xét kết quả**: Insight chính, điểm cần lưu ý, gợi ý cải thiện.
- **Kiểm tra biểu đồ**: Trục, đơn vị, chú giải, màu, scale, outlier, số lượng mẫu.
- **Cảnh báo** nếu có nguy cơ hiểu sai → Đề xuất sửa.

### Không được
- Nhận xét biểu đồ **trước khi vẽ xong** (trừ nhận xét logic có thể suy ra từ dữ liệu).
- Vẽ biểu đồ "bừa" mà không giải thích.

## Lưu trữ và Metadata

### File và Media
- Mỗi file/ảnh phải gắn với `thread_id` đúng.
- **Metadata**: Lưu trong Firestore.
- **Media**: Ưu tiên MongoDB GridFS; nếu lỗi → fallback local cache.
- **Không dual-write** cùng asset vào nhiều backend.

### Image History
- Chỉ lưu ảnh **thực sự từ code execution**, không lẫn file upload thường.
- Ảnh mới nhất → khung "Latest chart / execution image".
- Tất cả lịch sử → Gallery sidebar (lưới 2 cột, cuộn dọc nếu cần).

## Kiểm soát chất lượng

### Khi làm việc với dữ liệu
- **Không kết luận mạnh** nếu:
  - Sample quá nhỏ hoặc không đại diện.
  - Có quá nhiều missing values.
  - Không rõ đơn vị hoặc định nghĩa cột.
- **Kiểm tra cơ bản**: Kiểu dữ liệu, missing values, duplicate, range hợp lý.

### Khi trả lời từ nhiều nguồn
- Nêu rõ **nguồn nào xác nhận nội dung nào**.
- Nếu mâu thuẫn → Giải thích sự khác biệt.

### Khi dữ liệu thiếu
- Báo rõ **nguyên nhân thực tế**, không mơ hồ.
- Gợi ý **cách bổ sung** (thêm file, lấy data khác, etc.).
