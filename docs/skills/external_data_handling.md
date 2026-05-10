# External Data Handling - Kỹ năng Xử lý Dữ Liệu Bên Ngoài

## Khi nào cần
- Dữ liệu cần không nằm trong file upload hoặc Knowledge Base.
- Cần lấy data từ web, API, hoặc nguồn bên ngoài khác.

## Các kỹ năng cần thiết

### 1. Web Scraping
**Khi nào dùng**: Cần trích xuất dữ liệu từ website.

**Cơ bản**:
- **Kiểm tra robots.txt** - Đảm bảo website cho phép scrape.
- **Hiểu HTML structure** - Dùng `BeautifulSoup`, `requests` để parse.
- **Xử lý JavaScript** - Nếu trang load động → dùng `Selenium`, `Playwright`.

**Quy tắc đạo đức**:
- Không scrape data riêng tư hoặc bị cấm.
- Không spam server → Thêm delay giữa request, tuân thủ rate limit.
- Kiểm tra Terms of Service trang web trước.

**Ví dụ code**:
```python
import requests
from bs4 import BeautifulSoup

url = "https://example.com/data"
response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
soup = BeautifulSoup(response.content, "html.parser")
# Trích xuất data từ HTML
data = soup.find_all("table")
```

### 2. API Integration
**Khi nào dùng**: Website/dịch vụ cung cấp API chính thức.

**Các bước**:
1. **Hiểu API documentation** - Endpoint, authentication, query param, rate limit.
2. **Xử lý authentication** - API key, OAuth, JWT token.
3. **Quản lý error** - Handle rate limit (retry logic), timeout, invalid response.
4. **Làm sạch response** - Parse JSON/XML, filter field cần, handle missing data.

**Ví dụ**:
```python
import requests

api_key = os.getenv("API_KEY")  # Đừng hard-code
url = "https://api.example.com/data"
params = {"key": api_key, "limit": 100}
response = requests.get(url, params=params, timeout=10)

if response.status_code == 200:
    data = response.json()
    df = pd.DataFrame(data["records"])
else:
    print(f"Error: {response.status_code}")
```

### 3. Data Cleaning & Validation
**Phải làm trước dùng**:
- **Xóa null/missing values** → `df.dropna()` hoặc fill.
- **Fix data type** → `astype()`, parse date, convert number.
- **Xóa duplicate** → `drop_duplicates()`.
- **Handle outlier** → Kiểm tra range hợp lý, xóa hoặc flag.
- **Standardize format** → Cột name, text case, unit.

**Ví dụ**:
```python
df = pd.read_csv("data.csv")
# Xóa null
df = df.dropna(subset=["price", "quantity"])
# Fix type
df["price"] = pd.to_numeric(df["price"], errors="coerce")
df["date"] = pd.to_datetime(df["date"])
# Kiểm tra outlier
df = df[(df["price"] > 0) & (df["price"] < 10000)]
```

### 4. Đánh giá Độ Tin Cậy (Data Trustworthiness)
Trước dùng data từ bên ngoài, phải hỏi:

**Nguồn**: Ai tạo? Có chính thức không? Có update thường xuyên không?
**Accuracy**: Có kiểm tra được với dữ liệu khác không? Có tài liệu về methodology không?
**Completeness**: Có bị thiếu field, time period, hoặc geography không?
**Timeliness**: Data bao cũ rồi? Còn phù hợp cho phân tích hiện tại không?
**Bias**: Có bias trong cách collect data không? (Ví dụ: survey online thiên lệch về tuổi, education)

**Khi đánh giá thấp → Gợi ý cảnh báo** cho người dùng, không khẳng định kết luận mạnh.

## Workflow Tổng Quát
1. **Xác định nguồn** → Web scrape hay API?
2. **Lấy data** → Handle error, timeout, authentication.
3. **Parse & transform** → Từ raw format thành structured.
4. **Validate & clean** → Fix type, missing, outlier, duplicate.
5. **Đánh giá độ tin cậy** → Source, accuracy, completeness, bias.
6. **Tổng hợp với dữ liệu khác** → Kết hợp file upload, KB, data mới lấy.
7. **Phân tích & trả lời** → Luôn nêu rõ nguồn và cảnh báo nếu cần.

## Điều KHÔNG được làm
- **Không scrape dữ liệu riêng tư** hoặc bị cấm (check Terms of Service).
- **Không khẳng định kết luận** từ data bên ngoài mà không validate trước.
- **Không hard-code API key** → Dùng environment variable.
- **Không lấy top kết quả API rồi dừng** → Cần paginate, aggregate, suy luận từ toàn bộ.
- **Không quên attribution** → Luôn nêu nguồn data.

## Công cụ Python Hay Dùng
- **Web scraping**: `requests`, `BeautifulSoup`, `Selenium`, `Playwright`.
- **API**: `requests`, `httpx`, `aiohttp` (async).
- **Data cleaning**: `pandas`, `numpy`, `regex`.
- **Validation**: `pandas.DataFrame.info()`, `df.describe()`, `df.isnull().sum()`.
