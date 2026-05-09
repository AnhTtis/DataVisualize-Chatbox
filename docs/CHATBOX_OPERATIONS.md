# Chatbox Operations

## Mục tiêu
- Chatbox phục vụ 2 nhóm chức năng chính: hỏi đáp dữ liệu/tài liệu trong `Chat` và dự đoán giá bất động sản trong `Model`.
- Ưu tiên trả lời dựa trên dữ liệu đã upload trong thread hiện tại, sau đó mới tới Knowledge Base MongoDB, rồi mới tới suy luận chung.
- Nếu sinh Python để phân tích hoặc vẽ biểu đồ, mã phải dùng các helper đã có sẵn thay vì giả định đường dẫn ngẫu nhiên.

## Chọn chế độ
- Dùng `Chat` khi người dùng cần phân tích dữ liệu, hỏi về file đã upload, hỏi theo Knowledge Base, yêu cầu viết code, thống kê hoặc vẽ biểu đồ.
- Dùng `Model` khi người dùng cần dự đoán giá bất động sản từ form địa chỉ mới và các thuộc tính của bất động sản.
- Không trộn logic form của `Model` vào luồng chat thông thường trừ khi người dùng chỉ cần giải thích hoặc so sánh kết quả dự đoán.

## Nguồn dữ liệu ưu tiên
1. File đã upload trong thread hiện tại.
2. Knowledge Base MongoDB theo các collection đã cấu hình.
3. Dữ liệu đầu ra do code vừa chạy sinh ra trong thread.
4. Kiến thức chung của mô hình khi các nguồn trên không đủ.

## Luồng upload và lưu trữ
- Mỗi file upload phải gắn với đúng `thread_id`.
- Metadata thread được lưu trong Firestore.
- File nhị phân và ảnh ưu tiên lưu ở MongoDB GridFS.
- Nếu MongoDB GridFS lỗi, fallback sang local cache để ảnh/file vẫn hiển thị trong giao diện hiện tại.
- Không dual-write lên cả Firebase và MongoDB cho cùng một asset.
- Nếu cả hai backend media đều không dùng được, vẫn có thể trả lời cho lượt chat hiện tại nhưng phải báo rõ là asset chưa được persist lâu dài.

## Luồng ảnh biểu đồ
- Mỗi lần code sinh ra biểu đồ và người dùng bấm `Approve and run`, ảnh mới phải được thêm vào `image_history` của thread.
- `image_history` chỉ lưu ảnh đầu ra thực sự của code execution, không lẫn file upload thông thường.
- Ảnh gần nhất phải được hiển thị ở khung `Latest chart / execution image`, đồng thời toàn bộ lịch sử ảnh hiển thị ở gallery bên trái.
- Gallery lịch sử ảnh ở sidebar dùng lưới 2 cột (nhìn tương đương 2x2 ảnh trong khung nhỏ); nếu ảnh vượt quá chiều cao khung thì cuộn dọc.

## Luồng điều hướng giao diện
- Nút chuyển `Chat` / `Model` chỉ điều khiển hiển thị 2 group giao diện, không được reset state chat.
- Logic model page tách riêng trong `app_mode.py`; `app.py` chỉ import `build_property_model_page()` để giảm rối và dễ bảo trì.

## Yêu cầu trả lời
- Nếu có file hoặc KB liên quan, phải nói ngắn gọn là đang dựa vào nguồn nào.
- Nếu dữ liệu thiếu hoặc mapping địa chỉ thất bại, phải nêu rõ nguyên nhân thực tế thay vì trả lời mơ hồ.
- Khi sinh code:
  - phải bọc trong fenced code block,
  - ưu tiên `pd`, `np`, `plt`,
  - với file upload thì dùng `list_thread_files()`, `get_thread_file_path()`, `load_thread_file()`,
  - với KB Mongo thì dùng `load_kb_collection()`, `get_kb_collection_schema()`, `list_kb_collections()`.

## Kiểm soát chất lượng
- Không kết luận mạnh nếu dữ liệu chỉ là sample nhỏ, có missing values nhiều hoặc không rõ đơn vị.
- Khi nhận xét biểu đồ, luôn kiểm tra: tiêu đề, trục, đơn vị, chú giải, màu, scale, outlier, số lượng mẫu.
- Nếu biểu đồ có nguy cơ gây hiểu sai, phải đề xuất cách sửa cụ thể.
