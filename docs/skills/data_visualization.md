# Data Visualization Skill

## Khi nào dùng
- Người dùng yêu cầu vẽ biểu đồ, dashboard, trực quan hóa, EDA, so sánh phân phối, xem tương quan hoặc nhận xét chart hiện có.

## Quy trình
1. Xác định nguồn dữ liệu: file upload, Mongo Knowledge Base hay dữ liệu sẵn trong code.
2. Xác định mục tiêu: mô tả, so sánh, xu hướng, phân phối, tương quan, composition hay ranking.
3. Chọn loại biểu đồ tối giản nhưng đúng bài toán.
4. Chọn bảng màu phù hợp với kiểu dữ liệu.
5. Trả lời kèm nhận xét về giới hạn của biểu đồ nếu có.

## Mapping bài toán sang biểu đồ
- So sánh danh mục: bar chart, dot plot.
- Xu hướng theo thời gian: line chart, area chart nếu cần cảm giác volume.
- Phân phối một biến: histogram, KDE, boxplot.
- Tương quan hai biến số: scatter plot, hexbin nếu quá dày.
- Thành phần trong tổng: stacked bar, treemap; hạn chế pie chart nếu có nhiều nhóm.
- Dữ liệu địa lý: map nếu có tọa độ; nếu không thì aggregate theo tỉnh/quận rồi dùng bar chart.

## Quy tắc màu
- Dữ liệu thứ tự tăng dần: dùng sequential palette.
  - Gợi ý: `#0B3C5D -> #328CC1 -> #D9B310`.
- Dữ liệu có điểm giữa quan trọng: dùng diverging palette.
  - Gợi ý: `#B2182B -> #F7F7F7 -> #2166AC`.
- Dữ liệu phân loại: dùng categorical palette với độ tương phản rõ.
  - Gợi ý: `#1B4965`, `#62B6CB`, `#5FA8D3`, `#CAE9FF`, `#FFA62B`, `#D81159`.
- Tránh cặp đỏ-xanh lá nếu không có ký hiệu bổ trợ vì khó tiếp cận với người mù màu.
- Nền sáng thì dùng nét/nhãn tối; nền tối thì phải tăng tương phản chữ và grid.

## Nhận xét biểu đồ
- Mức tối thiểu phải nêu:
  - biểu đồ đang trả lời câu hỏi gì,
  - insight chính,
  - điểm có thể gây hiểu lầm,
  - đề xuất cải thiện nếu cần.
- Dấu hiệu cần cảnh báo:
  - trục bị cắt không hợp lý,
  - không có đơn vị,
  - quá nhiều category gây rối,
  - palette dùng không nhất quán,
  - chồng lấn quá mạnh làm mất dữ liệu.

## Thực thi trong app
- Nếu sinh code, ưu tiên `plt`, `sns`, `px`.
- Với file upload:
  - gọi `list_thread_files()` để biết file nào có sẵn,
  - gọi `load_thread_file(file_name_or_id)` để đọc nhanh CSV/XLSX/text,
  - gọi `get_thread_file_path(file_name_or_id)` khi thư viện cần đường dẫn thật.
- Với Mongo collections:
  - dùng `list_kb_collections()` để kiểm tra tên collection,
  - dùng `load_kb_collection(collection_name)` để load full data vào DataFrame.
