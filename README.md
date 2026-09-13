# NTU Knowledge Crawler

Pipeline tái lập được để thu thập và chuẩn hóa thông tin học vụ từ các nguồn
chính thức của Trường Đại học Nha Trang. SQLite là nguồn dữ liệu thật; JSON,
JSONL, Markdown và OWL đều được sinh lại từ cơ sở dữ liệu.

## Phạm vi đã triển khai

- CTĐT đại học K63–K68 và quan hệ học phần–chương trình.
- Chương trình, điều kiện ngôn ngữ và thông báo tuyển sinh sau đại học.
- Biểu mẫu thủ tục và workflow sinh viên đã xác minh, phân biệt rõ loại bằng chứng.
- Danh mục tuyển sinh đại học gắn với năm được nêu rõ trên nguồn.
- Chính sách tài chính có vòng đời current/historical/unresolved và ghi chú kiểm chứng.
- Dịch vụ sinh viên có bảng riêng, provenance theo từng dịch vụ.
- Bảng hành vi và hình thức xử lý trong QĐ 1351/QĐ-ĐHNT.
- Lưu raw source, SHA-256, version history, conflict và provenance của fact.

Crawler chỉ chấp nhận HTTPS từ `config/sources.yaml`, không thu thập danh sách
sinh viên và không suy đoán dữ liệu còn thiếu. Các quyết định bị loại trừ được
ghi trong `AGENTS.md` và được kiểm tra trong báo cáo chất lượng.

## Chạy pipeline

Yêu cầu Python 3.11 trở lên. Pipeline chính chỉ dùng thư viện chuẩn.

```bash
python3 main.py --skip-pdfs
```

Chạy đầy đủ, bao gồm tải lại PDF CTĐT trong hàng đợi:

```bash
python3 main.py
```

Các tùy chọn hữu ích:

```text
--limit N              Giới hạn số chương trình/tài liệu trong lần chạy mẫu
--skip-pdfs            Không xử lý hàng đợi PDF CTĐT
--skip-postgraduate    Bỏ qua module sau đại học
--skip-procedures      Bỏ qua module thủ tục
--skip-milestone5      Bỏ qua tài chính, dịch vụ, kỷ luật và tuyển sinh
```

Một bước lỗi có thể phục hồi không chặn các bước độc lập khác. URL lỗi được thử
tối đa ba lần qua các lần chạy; lỗi vĩnh viễn vẫn nằm trong audit nhưng không bị
gọi lại vô hạn. Lần chạy trả mã khác 0 nếu phát sinh bước lỗi mới hoặc kiểm tra
chất lượng quan trọng thất bại.

## OCR tài liệu scan

QĐ 1351 là PDF scan không có text layer. Trên macOS, extractor dùng Vision OCR,
giữ số trang, tọa độ và confidence trong `data/extracted/ocr/*.jsonl`. Cache OCR
được định danh bằng SHA-256 của tài liệu và phiên bản extractor. Các ô không đọc
được phải giữ `null`; output luôn có citation đến dòng và trang PDF. Lỗi chính tả
do OCR chỉ được sửa qua bảng correction theo mã dòng sau khi đối soát ảnh quét;
trạng thái `verified_manual` và citation ghi rõ việc đối soát.

## Đầu ra

Các bảng nghiệp vụ được xuất riêng trong `output/`:

- `programs.json`, `program_gaps.json`, `courses.json`
- `graduate_programs.json`, `graduate_admissions.json`
- `procedure_candidates.json`, `procedures.json`
- `admissions.json`, `financial_policies.json`, `units.json`, `student_services.json`
- `source_candidates.json`, `discipline_rules.json`

Các artifact tổng hợp gồm:

- `MASTER.md`: bản đọc được của toàn bộ fact.
- `NTU_HOC_VU_MASTER.md`: bản tổng hợp học vụ 16 phần dành cho người đọc, sinh tự động
  từ các bảng chuẩn hóa và liên kết tới artifact chi tiết.
- `NTU_STUDENT_FACTS.md`: bảng học vụ nghiêm ngặt theo schema 7 cột; mỗi dòng là
  một phát biểu có trích dẫn và URL riêng, tổ chức thành 16 phần và chỉ dùng loại
  fact cùng miền nguồn được phép.
- `NTU_STUDENT_FACTS_AUDIT.jsonl`: metadata theo từng fact của bản trên, gồm
  SHA-256 nguồn, raw path, thời điểm truy xuất, trạng thái snapshot và cấp bằng chứng.
- `facts.jsonl`: fact nguyên tử có provenance và version status.
- `ontology.owl`: RDF/XML của các fact current.
- `CRAWL_AUDIT.md`: số lượng, lỗi crawl và khoảng trống CTĐT.
- `CTDT_GAP_AUDIT.md`: ma trận 165 tổ hợp chưa được API hiện tại công bố và PDF 404.
- `DATA_QUALITY_REPORT.md`: integrity, URL whitelist, provenance, duplicate,
  conflict, dữ liệu cá nhân tiềm ẩn và trạng thái OCR.
- `unresolved.md`: kế hoạch ưu tiên, cách xử lý và tiêu chí đóng các khoảng
  trống dữ liệu còn lại.

Finance records chỉ được nâng thành `current` khi nguồn nêu đủ phạm vi và dữ kiện
hành động; thông báo hết hạn thành `historical`, còn trang danh mục thiếu chi tiết
giữ `unresolved` kèm lý do. Thay đổi fact cùng nguồn tạo bản `historical`; hai nguồn chính
thức mâu thuẫn được giữ đồng thời và ghi vào `conflicts`.

## Kiểm thử và tự động hóa

```bash
python3 -m unittest discover -s tests -v
```

Workflow `.github/workflows/update-ntu-kb.yml` chạy kiểm thử và cập nhật artifact
hằng tuần. Trên runner không có macOS Vision, pipeline dùng cache OCR đã có; nếu
PDF scan mới chưa có cache, quy tắc kỷ luật không được suy đoán.

## Giới hạn nguồn đã biết

Một liên kết PDF CTĐT chính thức hiện trả HTTP 404; hệ thống giữ lỗi trong audit,
dừng gọi lại sau ba lần và dùng trang HTML chính thức làm fallback (không suy ra
tổng tín chỉ từ giao diện chưa trích xuất):
`https://ctdt.ntu.edu.vn/ctdt/7580205_CTGT_K64.pdf`.
