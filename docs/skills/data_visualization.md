# Data Visualization - Kỹ năng Vẽ Biểu Đồ

## Quy trình chọn biểu đồ
1. **Xác định mục tiêu**: Mô tả, so sánh, xu hướng, phân phối, tương quan, thành phần hay ranking.
2. **Chọn loại biểu đồ** tối giản nhưng phù hợp bài toán (xem mapping dưới).
3. **Chọn bảng màu** thích hợp với kiểu dữ liệu.
4. **Giải thích lý do chọn** trước khi vẽ.
5. **Sau khi code chạy**: Nhận xét kết quả (insight, điểm lưu ý, gợi ý cải thiện).

## Mapping bài toán → Loại biểu đồ

| Bài toán | Loại biểu đồ | Ghi chú |
|----------|--------------|--------|
| So sánh danh mục | Bar chart, dot plot | Bar horizontal nếu label dài |
| Xu hướng theo thời gian | Line chart, area chart | Area nếu cần cảm giác volume |
| Phân phối một biến | Histogram, KDE, boxplot | Boxplot nếu so sánh nhiều nhóm |
| Tương quan 2 biến số | Scatter plot, hexbin | Hexbin nếu point quá dày |
| Thành phần trong tổng | Stacked bar, treemap | Tránh pie chart với nhiều nhóm |
| Dữ liệu địa lý | Map (nếu có tọa độ) | Nếu không có tọa độ: aggregate theo vùng, dùng bar chart |

## Quy tắc chọn bảng màu

### Dữ liệu thứ tự (Sequential)
Dùng khi giá trị tăng dần từ thấp → cao.
- **Gợi ý**: `#0B3C5D → #328CC1 → #D9B310`
- **Thư viện**: `sns.color_palette("Blues")`, `plt.cm.viridis`

### Dữ liệu phân kỳ (Diverging)
Dùng khi có điểm giữa quan trọng (ví dụ: lợi nhuận lỗ nhuận, chênh lệch).
- **Gợi ý**: `#B2182B → #F7F7F7 → #2166AC` (đỏ-trắng-xanh)
- **Thư viện**: `sns.color_palette("RdBu")`, `plt.cm.RdBu`

### Dữ liệu phân loại (Categorical)
Dùng khi không có thứ tự, mục đích phân biệt các nhóm.
- **Gợi ý**: `#1B4965`, `#62B6CB`, `#5FA8D3`, `#CAE9FF`, `#FFA62B`, `#D81159`
- **Thư viện**: `sns.color_palette("Set2")`, `sns.color_palette("husl")`

### Quy tắc tránh
- **Không dùng cặp đỏ-xanh lá** nếu không có bổ trợ (khó tiếp cận người mù màu).
- **Nền sáng**: Dùng nét/nhãn tối, grid nhẹ.
- **Nền tối**: Tăng tương phản chữ và grid, dùng màu sáng cho dữ liệu.

## Nhận xét biểu đồ (Chart Critique)

### Mức tối thiểu phải nêu
1. **Biểu đồ trả lời câu hỏi gì?** - Liên hệ với mục tiêu phân tích.
2. **Insight chính** - Pattern, trend, outlier nổi bật.
3. **Điểm cần lưu ý** - Gì có thể gây hiểu sai hoặc cần thận trọng.
4. **Đề xuất cải thiện** nếu có (nếu biểu đồ có vấn đề).

### Dấu hiệu cảnh báo
- Trục bị cắt ngang (cutoff) không hợp lý.
- Thiếu đơn vị hoặc chú giải rõ ràng.
- Quá nhiều category → gây rối, khó đọc.
- Palette không nhất quán → gây nhầm lẫn.
- Chồng lấn quá mạnh → mất thông tin.
- Tỷ lệ/scale không hợp lý → hiểu sai magnitude.

## Khi sinh code
- **Ưu tiên**: `plt` (Matplotlib), `sns` (Seaborn), `px` (Plotly Express).
- **Với file upload**: Gọi `list_thread_files()` trước, sau đó `load_thread_file()`.
- **Với KB Mongo**: Gọi `load_kb_collection(collection_name)` để lấy DataFrame.
- **Không hard-code đường dẫn** - Luôn dùng helper function.

## Lưu ý quan trọng
- **Giải thích lý do chọn biểu đồ trước khi vẽ** - Không vẽ rồi mới nói.
- **Chỉ nhận xét kết quả sau khi code chạy xong** (trừ nhận xét logic có thể suy ra từ dữ liệu trước vẽ).
- **Kiểm tra dữ liệu trước vẽ**: Missing values, outlier, range hợp lý.
