# Chatbox Tools - Công Cụ & Helper Functions

## Knowledge Base (KB) Tools

### `list_kb_collections()`
**Mục đích**: Xem danh sách collection có sẵn trong KB.

**Khi dùng**:
- Lần đầu tiên trong lượt chat → Chưa chắc tên collection nào.
- Sau mỗi upload mới vào KB → Có thể collection tên đã thay đổi.

**Ví dụ**:
```python
collections = list_kb_collections()
print(collections)  # ['stores', 'products', 'sales']
```

### `load_kb_collection(collection_name, limit=None)`
**Mục đích**: Load một collection từ KB thành DataFrame.

**Khi dùng**:
- **Có KB collection phù hợp** → Ưu tiên dùng thay vì hard-code.
- EDA, thống kê mô tả, tính toán dữ liệu.
- Vẽ biểu đồ từ KB.

**Không dùng khi**:
- File upload có sẵn trong thread hiện tại → Dùng `load_thread_file()` thay.
- Cần dữ liệu từ bên ngoài (API, web scrape) → Xem External Data Handling skill.

**Ví dụ**:
```python
df_stores = load_kb_collection("stores")
print(df_stores.head())
print(f"Shape: {df_stores.shape}")
```

### `get_kb_collection_schema(collection_name)`
**Mục đích**: Xem mô tả schema, kiểu dữ liệu, missing %, sample values.

**Khi dùng**:
- Trước khi sinh code → Cần hiểu cột nào có, kiểu gì.
- Khi muốn kiểm tra dữ liệu có sạch không.

**Ví dụ**:
```python
schema = get_kb_collection_schema("products")
# Trả về: {
#   "fields": [...],
#   "missing_pct": {...},
#   "sample_values": {...}
# }
```

---

## Thread File Tools

### `list_thread_files()`
**Mục đích**: Xem danh sách file đã upload trong chat hiện tại.

**Trả về**: List dict với `asset_id`, `name`, `content_type`, `size_bytes`.

**Khi dùng**:
- Chưa chắc tên file chính xác → Cần liệt kê trước.
- Câu hỏi "có những file nào trong thread?"
- Cần xác nhận file tồn tại trước khi load.

**Ví dụ**:
```python
files = list_thread_files()
for f in files:
    print(f["name"], f["content_type"])
```

### `load_thread_file(file_name_or_id)`
**Mục đích**: Đọc file từ thread thành DataFrame (nếu CSV/JSON/XLSX) hoặc string (text).

**Ưu điểm**:
- **Không cần hard-code path** → Tự tìm file trong thread cache.
- Tự detect format và convert.
- Nhanh nhất cho file vừa upload.

**Loại file được hỗ trợ**:
- `csv` → DataFrame
- `json` → DataFrame (nếu structured array/object)
- `xlsx`, `xlsm` → DataFrame (first sheet)
- Text-like (`txt`, `md`, `log`) → str
- Khác → Trả về path string

**Khi dùng**:
- File vừa được upload vào thread.
- Cần dữ liệu từ file **hiện tại**, không phải KB.

**Ví dụ**:
```python
df = load_thread_file("sales.csv")
# Hoặc
text = load_thread_file("notes.txt")

# Hoặc nếu chưa chắc tên
files = list_thread_files()
file_name = files[0]["name"]
df = load_thread_file(file_name)
```

### `get_thread_file_path(file_name_or_id)`
**Mục đích**: Lấy đường dẫn file thật từ local cache.

**Khi dùng**:
- **Thư viện chỉ chấp nhận path**, không nhận file object.
- Ví dụ: `sqlalchemy`, custom loader, library không dùng pandas.

**Ví dụ**:
```python
path = get_thread_file_path("my_database.db")
conn = sqlite3.connect(path)  # Library yêu cầu path
cursor = conn.cursor()
```

**Không dùng khi**:
- `load_thread_file()` đã trả về DataFrame → Không cần path.
- Cần modify file → Path chỉ dùng read, không bảo đảm write.

---

## Visualization Tools (Injected Aliases)

### `plt` (Matplotlib)
**Injected**: Tự động nếu code có vẻ dùng Matplotlib.

**Alias**: `matplotlib.pyplot` đã import sẵn.

**Ví dụ**:
```python
plt.figure(figsize=(10, 6))
plt.plot(df["x"], df["y"], marker="o")
plt.title("My Chart")
plt.show()
```

### `sns` (Seaborn)
**Injected**: Nếu code gợi ý dùng Seaborn hoặc đã import khả dụng.

**Ví dụ**:
```python
sns.scatterplot(data=df, x="col1", y="col2", hue="category")
```

### `px`, `go` (Plotly)
**Injected**: Nếu import khả dụng.

- `px` = Plotly Express (high-level).
- `go` = Graph Objects (low-level).

**Ví dụ**:
```python
fig = px.bar(df, x="category", y="value", color="status")
fig.show()
```

---

## Standard Aliases (Luôn Khả Dụng)
Những này được inject tự động vào execution environment:
- `pd` = `pandas`
- `np` = `numpy`
- `json` → `import json`
- `math` → `import math`
- `re` → `import re`
- `os` → `import os`
- `Path` = `pathlib.Path`
- `Counter` = `collections.Counter`
- `defaultdict` = `collections.defaultdict`

**Không cần import lại** những alias này trong code cell.

---

## Quy Tắc Ưu Tiên

### 1. Chọn Đúng Tool
| Dữ liệu | Tool | Ghi chú |
|--------|------|--------|
| File upload trong thread | `load_thread_file()` | **Ưu tiên** |
| KB collection có sẵn | `load_kb_collection()` | Nếu thread file không có |
| Bên ngoài web/API | External Data Handling skill | Scrape, fetch, API |

### 2. Không Hard-Code Path
**SAI**:
```python
df = pd.read_csv("C:\\Users\\...\\file.csv")  # ❌ Hard-code
```

**ĐÚNG**:
```python
df = load_thread_file("file.csv")  # ✅ Dùng helper
# Hoặc
path = get_thread_file_path("file.csv")
df = pd.read_csv(path)  # ✅ Dynamic path
```

### 3. Kiểm Tra Tồn Tại Trước
**TỐTKHÔI**:
```python
files = list_thread_files()
if not files:
    print("Chưa upload file")
else:
    df = load_thread_file(files[0]["name"])
```

### 4. Hiểu Data Source Priority
Theo CHATBOX_OPERATIONS.md:
1. **File upload** (thread) → Most trusted
2. **KB collection** → Pre-validated
3. **Output từ chat trước** → Session-scoped
4. **Tổng quát** → Last resort, cần validate

---

## Debugging Tips
- **"Module not found"** → Thư viện chưa cài. Gợi ý user install.
- **"File not found"** → Check `list_thread_files()` trước, verify tên.
- **"DataFrame empty"** → Kiểm tra file upload có content không.
- **"Path invalid"** → Dùng `get_thread_file_path()` thay vì hard-code.
