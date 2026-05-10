# Document Reasoning - Kỹ năng Tư Duy với Tài Liệu

## Quy tắc xử lý file đã upload
- **Ưu tiên file** trong thread hiện tại vì đó là context đáng tin nhất.
- Nếu file có `text_excerpt`: Dùng excerpt đó để trả lời nhanh và chính xác.
- Nếu file là ảnh hoặc PDF hỗ trợ multimodal: Tận dụng trực tiếp file part cho chat hiện tại.
- Nếu file không trích xuất được text: Báo rõ giới hạn thay vì giả định nội dung.

## Cách trả lời

### Với câu hỏi mô tả
- Tóm tắt theo cấu trúc (không liệt kê lan man).
- Nêu rõ là dựa vào file nào, mục nào.

### Với câu hỏi đối chiếu nhiều file
- Nêu rõ file nào xác nhận thông tin nào.
- Nếu mâu thuẫn: Giải thích sự khác biệt.

### Với câu hỏi yêu cầu tính toán
- Nếu dữ liệu đủ sạch: Có thể sinh code để kiểm tra lại bằng DataFrame.
- Nếu dữ liệu lộn xộn: Sinh code để dọn dẹp trước (xóa missing, fix type, etc.).

### Với file bảng (CSV, XLSX)
- **Kiểm tra cơ bản**:
  - Cột nào có, kiểu dữ liệu là gì.
  - Missing values, duplicate row.
  - Đơn vị của mỗi cột (nếu không rõ → phải hỏi).
  - Range dữ liệu hợp lý không.
- Sau đó mới trả lời câu hỏi.

## Khi sinh code với file

1. **Nếu chưa chắc tên file**: Gọi `list_thread_files()` trước.
2. **Để lấy dữ liệu**:
   - CSV/XLSX/JSON: Dùng `load_thread_file(file_name_or_id)` → lấy ngay DataFrame.
   - Khác: Dùng `get_thread_file_path(file_name_or_id)` → lấy path thật, dùng cho library riêng.
3. **Không hard-code đường dẫn** từ Gradio tạm.

## Điều KHÔNG được làm
- **Không khẳng định** nội dung của file nhị phân khi chưa đọc được hoặc chưa được model nhìn thấy (đúc kết).
- **Không nói file đã được lưu lâu dài** nếu backend persist thất bại (phải báo rõ).
- **Không lấy top kết quả** từ search rồi dừng → phải kết hợp & suy luận từ nhiều kết quả.
