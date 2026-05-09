# Chatbox Tools

## Công cụ dữ liệu từ Mongo Knowledge Base
- `list_kb_collections()`
  - Trả về danh sách collection được cấu hình.
  - Dùng khi chưa chắc tên collection.
- `load_kb_collection(collection_name, limit=None)`
  - Load collection thành `pandas.DataFrame`.
  - Dùng cho EDA, tính toán thống kê, vẽ biểu đồ.
- `get_kb_collection_schema(collection_name)`
  - Trả về mô tả schema, field coverage, sample values.
  - Dùng trước khi sinh code nếu cần hiểu dữ liệu.

## Công cụ dữ liệu từ file upload trong thread
- `list_thread_files()`
  - Trả về danh sách file đã upload trong chat hiện tại.
  - Mỗi item có `asset_id`, `name`, `content_type`, `size_bytes`.
- `get_thread_file_path(file_name_or_id)`
  - Materialize file từ backend storage về local cache và trả đường dẫn thật.
  - Dùng khi thư viện chỉ nhận path.
- `load_thread_file(file_name_or_id)`
  - Tự đọc nhanh một số loại file:
    - `csv` -> `DataFrame`
    - `json` -> `DataFrame`
    - `xlsx/xlsm` -> `DataFrame`
    - text-like -> `str`
  - Với loại khác, trả về path string.

## Công cụ trực quan hóa
- `plt`
  - Matplotlib đã được inject sẵn khi code có dấu hiệu vẽ biểu đồ.
- `sns`
  - Seaborn nếu import khả dụng.
- `px`, `go`
  - Plotly Express / Graph Objects nếu cài đặt khả dụng.

## Alias cơ bản trong runtime
- `pd`, `np`, `json`, `math`, `re`, `os`, `Path`, `Counter`, `defaultdict`.

## Quy tắc dùng tool
- Không gọi helper Mongo nếu dữ liệu nằm hoàn toàn trong file upload.
- Không hard-code đường dẫn file tạm từ Gradio.
- Với câu hỏi cần biểu đồ từ file upload, nên bắt đầu bằng `list_thread_files()` hoặc dùng chính tên file đã thấy trong thread.
