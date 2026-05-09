# Document Reasoning Skill

## Khi nào dùng
- Người dùng hỏi dựa trên file đã upload: PDF, CSV, TXT, DOCX, XLSX, ảnh hoặc nhiều file kết hợp.

## Quy tắc xử lý
- Ưu tiên file trong thread hiện tại trước vì đó là context đáng tin nhất.
- Nếu file có `text_excerpt`, dùng excerpt đó để tạo câu trả lời nhanh.
- Nếu file là ảnh hoặc PDF và có multimodal input, tận dụng trực tiếp file part cho lượt chat hiện tại.
- Nếu file không trích xuất được text, phải nói rõ giới hạn thay vì giả định nội dung.

## Cách trả lời
- Với câu hỏi mô tả: tóm tắt theo cấu trúc, không liệt kê lan man.
- Với câu hỏi đối chiếu nhiều file: nêu rõ file nào xác nhận thông tin nào.
- Với câu hỏi yêu cầu tính toán: nếu dữ liệu đủ sạch, có thể sinh code để kiểm tra lại bằng DataFrame.
- Với file bảng: ưu tiên kiểm tra cột, kiểu dữ liệu, missing values, đơn vị, duplicate.

## Khi sinh code
- Nếu chưa chắc tên file, gọi `list_thread_files()` trước.
- Nếu cần DataFrame:
  - `load_thread_file(file_name_or_id)` cho CSV/XLSX/JSON.
- Nếu thư viện yêu cầu path:
  - `get_thread_file_path(file_name_or_id)`.

## Điều không được làm
- Không khẳng định nội dung của file nhị phân khi chưa đọc được hoặc chưa được model nhìn thấy.
- Không nói file đã được lưu lâu dài nếu backend persist thất bại.
