# Kế hoạch xử lý dữ liệu chưa hoàn chỉnh

Cập nhật: 2026-09-13

Tài liệu này là backlog xử lý các khoảng trống đang được công khai trong
`output/NTU_HOC_VU_MASTER.md` và `output/DATA_QUALITY_REPORT.md`. Mục tiêu là
phân loại đúng trạng thái và bổ sung dữ liệu có bằng chứng chính thức, không ép
mọi giá trị `null` thành một giá trị suy đoán.

## Trạng thái triển khai ngày 2026-09-13

| Hạng mục | Trạng thái | Kết quả hiện tại |
|---|---|---|
| Refresh API CTĐT | Đã triển khai bước cốt lõi | 130 URL detail được requeue và tải lại ở mỗi lần discovery; content hash/version cũ vẫn được giữ |
| Phân loại CTĐT rỗng | Đã triển khai | 23 chương trình được xác định `draft_empty`; `complete_programs_without_courses=0` |
| Tổng tín chỉ | Đang thực hiện | 52 chương trình đã có tổng tín chỉ từ dòng/bảng PDF chính thức kèm citation; 55 chương trình `complete` chưa có tổng xác nhận được; 23 draft tiếp tục `not_stated` |
| Gap ngành/khóa | Đã có mô hình, còn review nội dung | 165 gap đều có bản ghi `program_gaps` và trạng thái `api_absent`; chưa được phép chuyển `not_applicable` nếu chưa có bằng chứng |
| Finance index pages | Đã xử lý | 5 trang danh mục từng sinh ra 6 policy giả đã chuyển `index_only`; `unresolved_financial_policies=0` |
| PDF 404 | Đã có fallback chính thức | CTĐT API ID 80 và 84 học phần vẫn hợp lệ; PDF hỏng được thay bằng trang HTML chính thức `/chuongtrinhdt/80` trong output và gắn `missing_asset_with_html_fallback` |
| Regression tests | Đã bổ sung | Có test refresh queue, trạng thái content, finance index và trích xuất tín chỉ PDF |

Các con số ở “Baseline hiện tại” bên dưới là mốc ban đầu dùng để đo tiến độ.

## 1. Baseline hiện tại

| Vấn đề | Số lượng | Bản chất hiện biết |
|---|---:|---|
| Tổ hợp ngành/khóa không xuất hiện trong API | 165 | Được tạo từ tích Descartes giữa mọi ngành hiện tại và K63–K68; một phần có thể là ngành chưa áp dụng hoặc đã đổi mã/tên, không nhất thiết là tài liệu bị thất lạc |
| CTĐT không có học phần | 22 | Raw API đều ghi `Đang biên soạn`; 20 bản có `published=true`, 2 bản có `published=false`; danh sách học phần hiện rỗng |
| CTĐT chưa có tổng tín chỉ | 129 | `nhomKhungCTs.soTinChiBB` và `soTinChiTC` đang bằng 0 nên extractor chủ động giữ `null` |
| Chính sách tài chính `unresolved` | 6 | Đều là trang danh mục/trang giới thiệu, thiếu năm học, điều kiện, thời hạn hoặc căn cứ để trở thành một chính sách có thể hành động |
| PDF trả về 404 | 1 | PDF Kỹ thuật xây dựng công trình giao thông K64 bị hỏng liên kết; dữ liệu API và 84 quan hệ học phần vẫn còn |

## 2. Thứ tự ưu tiên

| Priority | Việc cần làm | Kết quả mong đợi |
|---|---|---|
| P0 | Cho phép URL đã crawl được làm mới định kỳ | Pipeline nhìn thấy thay đổi của API thay vì giữ mãi trạng thái `done` |
| P0 | Lưu trạng thái công bố/biên soạn và sửa cách đánh giá 22 CTĐT rỗng | Không còn gọi nhầm dữ liệu đang biên soạn là lỗi trích xuất |
| P0 | Bổ sung bộ trích xuất tổng tín chỉ có provenance | Có tổng tín chỉ khi nguồn nêu rõ; tiếp tục giữ `null` nếu không có bằng chứng |
| P1 | Thay mô hình 165 `missing` bằng bảng gap có lý do và bằng chứng | Phân biệt `not_applicable`, `api_absent`, `located` và `needs_confirmation` |
| P1 | Crawl trang chi tiết nằm sau 6 trang tài chính | Trang danh mục không còn bị coi là một policy; policy chi tiết được version hóa |
| P1 | Xử lý liên kết PDF 404 như một asset gap | CTĐT API vẫn hợp lệ; PDF có thể được thay thế nếu tìm thấy URL chính thức |
| P2 | Thêm regression test, dashboard và cảnh báo thay đổi | Khoảng trống mới được phát hiện tự động trong các lần chạy sau |

## 3. P0 — Làm mới nguồn thay vì bỏ qua URL đã `done`

### Vấn đề

`discover()` tiếp tục enqueue URL, nhưng các URL đã có trạng thái `done` không
được đưa lại về `pending`. Vì thế một lần chạy tuần có thể không tải lại API đã
thay đổi. Đây là việc cần sửa trước khi đánh giá lại các CTĐT “Đang biên soạn”.

### Cách xử lý

1. Thêm chính sách freshness cho `crawl_queue`, ví dụ:
   - API/index: crawl lại sau 24 giờ.
   - Trang thông báo: crawl lại sau 7 ngày.
   - PDF: dùng `ETag`/`Last-Modified` nếu có; nếu không thì kiểm tra lại theo chu kỳ dài hơn.
2. Khi URL quá hạn freshness, chuyển `done` về `pending`; không xóa source version cũ.
3. Gửi `If-None-Match` và `If-Modified-Since` khi server hỗ trợ.
4. Nếu nội dung thay đổi, lưu raw mới và SHA-256 mới; giữ phiên bản cũ là
   `historical` theo cơ chế hiện tại.
5. Không reset vô hạn URL 404 trong mỗi run. Dùng `next_retry_at` và backoff dài
   cho asset đã thất bại đủ ba lần.

### File dự kiến thay đổi

- `database/store.py`: thêm `last_success_at`, `next_retry_at`, `etag`,
  `last_modified` nếu cần.
- `pipeline/crawl.py` và `crawlers/ctdt.py`: freshness/requeue và conditional GET.
- `tests/test_ctdt.py`: test URL `done` quá hạn được crawl lại, URL còn mới không
  bị tải lại.

### Tiêu chí hoàn tất

- Chạy pipeline lần hai vẫn kiểm tra được nguồn đã thay đổi.
- Nội dung không đổi không tạo source version mới.
- Nội dung đổi tạo đúng một version mới và bảo toàn lịch sử.

## 4. P0 — 22 CTĐT chưa có chi tiết học phần

### Kết luận từ raw API

Đây chưa phải 22 lỗi parser. Cả 22 payload đều có `hocPhanKhungs=[]`,
`hocPhanKhungTTCLCs=[]`, `soHocPhan=0` và trạng thái `Đang biên soạn`.

| Khóa | Mã ngành | Chương trình | API ID |
|---|---|---|---:|
| K65 | 7220201 | Ngôn ngữ Anh | 146 |
| K65 | 7620303 | Khoa học thủy sản | 156 |
| K65 | 7620304 | Khai thác thủy sản | 157 |
| K67 | 7480201 | Công nghệ thông tin | 145 |
| K67 | 7520103 | Kỹ thuật cơ khí | 144 |
| K67 | 7540105 | Công nghệ chế biến thủy sản | 137 |
| K67 | GDTC | Chương trình GDTC | 147 |
| K67 | GDTQ | Chương trình GDTQ | 148 |
| K68 | 7340101 | Quản trị kinh doanh | 219 |
| K68 | 7340201 | Tài chính - Ngân hàng | 1218 |
| K68 | 7340301 | Kế toán | 1219 |
| K68 | 7420201 | Công nghệ sinh học | 1221 |
| K68 | 7480201 | Công nghệ thông tin | 1220 |
| K68 | 7480201 | Công nghệ thông tin | 1225 |
| K68 | 7520103 | Kỹ thuật cơ khí | 213 |
| K68 | 7520103 | Kỹ thuật cơ khí | 1223 |
| K68 | 7520114 | Kỹ thuật cơ điện tử | 214 |
| K68 | 7520115 | Kỹ thuật nhiệt | 216 |
| K68 | 7520115MP | Kỹ thuật cơ điện lạnh (chương trình MP - NTU) | 1224 |
| K68 | 7540105 | Công nghệ chế biến thủy sản | 1222 |
| K68 | 7540105 | Công nghệ chế biến thủy sản | 1226 |
| K68 | 7810103 | Quản trị dịch vụ du lịch và lữ hành | 217 |

### Cách xử lý

1. Lưu thêm các trường gốc `tenTrangThaiCTDT`, `published`, `soHocPhan` và
   `filePDF` vào bản ghi chương trình.
2. Tách `crawl_status` khỏi `content_status`:
   - `crawl_status=success`: HTTP và parse thành công.
   - `content_status=draft_empty`: nguồn ghi đang biên soạn và chưa có học phần.
   - `content_status=complete`: có danh sách học phần hoặc tài liệu hoàn chỉnh.
   - `content_status=inconsistent`: API nói hoàn chỉnh nhưng không có nội dung.
3. Với 22 bản ghi trên, giữ chương trình và provenance nhưng không phát fact
   `has_course` hay suy đoán môn học.
4. Sau khi cơ chế refresh ở Phần 3 hoạt động, kiểm tra lại hằng ngày/tuần. Khi
   danh sách học phần xuất hiện, `replace_courses()` sẽ cập nhật snapshot.
5. Nếu PDF chính thức đã có học phần trong khi API rỗng, trích xuất từ PDF thành
   nguồn riêng và ghi rõ nguồn/citation; không âm thầm trộn hai nguồn.

### Tiêu chí hoàn tất

- Quality check đổi từ `successful_programs_without_courses` thành
  `complete_programs_without_courses`.
- Giá trị kỳ vọng của check mới là 0.
- 22 bản ghi được báo cáo là `draft_empty`, không còn bị mô tả như lỗi crawler.

## 5. P0 — 129 CTĐT chưa có tổng tín chỉ

### Nguyên tắc

Không lấy tổng của mọi dòng học phần làm tổng tín chỉ chương trình. Một học phần
có thể nằm trong nhiều nhóm lựa chọn, nhiều học kỳ hoặc nhiều hướng chuyên sâu;
cộng thẳng có thể cho kết quả sai. Chỉ ghi tổng tín chỉ khi nguồn chính thức nêu
rõ hoặc phép tính được chứng minh là tương đương với cấu trúc lựa chọn.

### Thứ tự nguồn trích xuất

1. Trường tổng tín chỉ trực tiếp trong API, nếu NTU bổ sung sau này.
2. Tổng `soTinChiBB + soTinChiTC` theo `nhomKhungCTs` khi các giá trị khác 0 và
   cấu trúc nhóm không chồng lặp.
3. Dòng “tổng số tín chỉ/toàn khóa” trong PDF CTĐT chính thức, kèm số trang và
   citation.
4. Tổng được tính từ học phần chỉ khi extractor mô hình hóa đầy đủ nhóm bắt
   buộc, số tín chỉ tự chọn tối thiểu, hướng/chuyên ngành và học phần thay thế.
5. Nếu không đạt một trong bốn điều kiện trên, giữ `total_credits=null` và gắn
   `credit_status=not_stated` hoặc `source_draft`.

### Cách triển khai

1. Mở rộng `pdf_extractor.py` để nhận dạng các nhãn tổng tín chỉ và bảng phân bổ
   khối kiến thức; lưu page/cell citation.
2. Thêm bảng/field `program_credit_evidence` gồm `value`, `method`, `source_url`,
   `citation`, `retrieved_at`, `confidence`.
3. So sánh tổng từ API và PDF. Nếu khác nhau, tạo conflict thay vì chọn một giá
   trị tùy ý.
4. Chạy backfill trên 128 PDF khả dụng; chương trình có PDF 404 được xử lý theo
   Phần 8.
5. Thêm test cho nhóm tự chọn và chương trình nhiều hướng để ngăn cộng trùng.

### Tiêu chí hoàn tất

- Mọi `total_credits` khác `null` đều có citation và phương pháp tính.
- Không có conflict bị ghi đè.
- Quality check chỉ cảnh báo chương trình `complete` thiếu tổng tín chỉ; chương
  trình `draft_empty` được theo dõi riêng.

## 6. P1 — 165 tổ hợp ngành/khóa không xuất hiện trong API

### Phân bố hiện tại

| Khóa | Số tổ hợp |
|---|---:|
| K63 | 23 |
| K64 | 38 |
| K65 | 30 |
| K66 | 41 |
| K67 | 31 |
| K68 | 2 |

Danh sách chi tiết hiện có tại `output/CTDT_GAP_AUDIT.md`.

### Vì sao không nên cố đưa con số về 0

Danh sách 165 được sinh bằng cách kỳ vọng mọi mã ngành hiện có phải có CTĐT ở
mọi khóa K63–K68. Giả định này không đúng cho ngành mở mới, ngừng tuyển, đổi mã,
đổi tên hoặc chương trình đặc biệt. Mục tiêu đúng là đưa số gap **chưa giải
thích** về 0, không phải tạo đủ 165 CTĐT.

### Mô hình dữ liệu đề xuất

Tạo bảng `program_gaps` với:

- `major_code`, `cohort`, `expected_reason`;
- `gap_status`: `api_absent`, `not_applicable`, `located`,
  `needs_official_confirmation`;
- `evidence_url`, `citation`, `retrieved_at`;
- `replacement_major_code` khi có bằng chứng đổi mã;
- `review_note` và `reviewed_at`.

### Luồng giải quyết

1. Xây dựng khoảng áp dụng của từng ngành từ chính các summary API và quyết
   định/mục tuyển sinh chính thức; không suy ra khóa từ ngày văn bản.
2. Tìm trên các miền NTU đã được phê duyệt theo mã ngành + khóa, sitemap, trang
   CTĐT cũ và liên kết PDF có thật.
3. Nếu tìm thấy PDF/trang chính thức: crawl, hash, trích xuất và chuyển gap thành
   `located`.
4. Nếu có bằng chứng ngành chưa mở hoặc đã đổi mã ở khóa đó: chuyển
   `not_applicable`, giữ citation.
5. Nếu chỉ không thấy trên API và không có bằng chứng khác: giữ
   `needs_official_confirmation`; không tự đánh dấu `not_applicable`.
6. Báo cáo hai KPI riêng: `api_absent_total` và `unexplained_gap_total`.

### Tiêu chí hoàn tất

- Cả 165 tổ hợp đều có `gap_status` và provenance cho mọi kết luận khác
  `api_absent`.
- Không có CTĐT hoặc khóa học được dựng từ tên file hay năm quyết định.
- `CTDT_GAP_AUDIT.md` hiển thị lý do, bằng chứng và trạng thái review.

## 7. P1 — 6 chính sách tài chính `unresolved`

| Loại | Trang nguồn | Thiếu dữ kiện |
|---|---|---|
| exemption | [Chế độ và chính sách](https://phongctsv.ntu.edu.vn/Che-%C4%91o-Chinh-sach/Che-%C4%91o) | Năm học, hành động/điều kiện áp dụng |
| scholarship | [Chế độ và chính sách](https://phongctsv.ntu.edu.vn/Che-%C4%91o-Chinh-sach/Che-%C4%91o) | Năm học, hành động/điều kiện áp dụng |
| scholarship | [Học bổng — Phòng KHTC](https://phongkhtc.ntu.edu.vn/sinh-vien-hoc-vien/hoc-bong) | Năm học, điều kiện, thời hạn/căn cứ |
| scholarship | [Học bổng — tin tức CTSV](https://phongctsv.ntu.edu.vn/tin-tuc/d/hoc-bong) | Năm học, điều kiện, thời hạn/căn cứ |
| scholarship | [Danh mục học bổng CTSV](https://phongctsv.ntu.edu.vn/danh-muc/hoc-bong) | Năm học, điều kiện, thời hạn/căn cứ |
| tuition_rule | [Học phí — Phòng KHTC](https://phongkhtc.ntu.edu.vn/sinh-vien-hoc-vien/hoc-phi) | Năm học, mức/phạm vi, thời hạn/căn cứ |

### Cách xử lý

1. Phân loại các URL trên là `index_page`/`topic_page`, không phải policy nguyên
   tử.
2. Crawl các liên kết bài chi tiết từ từng trang, giới hạn trong allowed domains.
3. Chỉ tạo `financial_policies` khi bài chi tiết có tối thiểu:
   - loại và phạm vi chính sách;
   - năm học hoặc thời gian hiệu lực;
   - đối tượng/điều kiện hay hành động;
   - deadline, mức tiền/tỷ lệ hoặc căn cứ khi loại policy yêu cầu.
4. Một bài đã hết hạn được lưu `historical`; bài đang áp dụng là `current`; trang
   danh mục ở lại `source_candidates` với trạng thái `index_only`.
5. Deduplicate bài xuất hiện qua nhiều trang danh mục bằng canonical URL và
   content hash.

### Tiêu chí hoàn tất

- Sáu trang danh mục không còn nằm trong `financial_policies` như policy giả.
- `unresolved_financial_policies=0`, trừ bài chi tiết thực sự chưa đủ chứng cứ;
  các bài đó phải có `verification_note` cụ thể.
- Không thu thập danh sách sinh viên nhận học bổng hay dữ liệu cá nhân.

## 8. P1 — PDF CTĐT trả về 404

### Bản ghi liên quan

- Ngành: Kỹ thuật xây dựng công trình giao thông.
- Mã ngành: `7580205`.
- Khóa: `K64`.
- API ID: `80`.
- API còn hoạt động và kho hiện có 84 quan hệ học phần.
- URL PDF do API cung cấp:
  `https://ctdt.ntu.edu.vn/ctdt/7580205_CTGT_K64.pdf`.
- Kết quả: HTTP 404 sau 3 lần thử.

### Cách xử lý

1. Refresh summary API để kiểm tra `filePDF` có được NTU sửa hay không.
2. Tìm liên kết từ trang CTĐT/index/sitemap chính thức theo API ID, mã ngành và
   khóa; chỉ chấp nhận URL trên allowed domains.
3. Nếu tìm thấy URL chính thức mới, lưu nó như asset hiện hành và giữ URL 404 ở
   lịch sử với quan hệ `replaced_by`.
4. Nếu không tìm thấy, đặt `pdf_status=missing_asset` và giữ CTĐT API là hợp lệ.
   Một PDF phụ trợ không được làm cả pipeline thất bại.
5. Retry theo chu kỳ dài, ví dụ 30 ngày, thay vì thử ba lần trong mọi run.

### Tiêu chí hoàn tất

- Không còn URL này trong hàng đợi lỗi chặn release.
- Trạng thái phải là `replaced` nếu có PDF mới, hoặc `missing_asset` nếu NTU vẫn
  trả 404.
- Dữ liệu 84 học phần từ API vẫn giữ nguyên provenance và không được gắn citation
  giả sang PDF.

## 9. P2 — Kiểm thử và báo cáo bắt buộc

Thêm các kiểm tra sau trước khi coi backlog đã đóng:

1. `stale_sources_not_refetched`: nguồn quá freshness phải được kiểm tra lại.
2. `complete_programs_without_courses`: phải bằng 0.
3. `complete_programs_missing_total_credits`: phải bằng 0 hoặc có waiver kèm
   bằng chứng nguồn không nêu.
4. `program_gaps_without_classification`: phải bằng 0.
5. `program_gap_conclusions_without_evidence`: phải bằng 0 cho
   `not_applicable`/`located`.
6. `financial_index_pages_materialized_as_policies`: phải bằng 0.
7. `current_financial_policies_missing_actionable_fields`: phải bằng 0.
8. `missing_assets_without_retry_schedule`: phải bằng 0.
9. Chạy `python3 -m unittest discover -s tests -v`.
10. Sinh lại `MASTER.md`, `NTU_HOC_VU_MASTER.md`, `CRAWL_AUDIT.md`,
    `CTDT_GAP_AUDIT.md` và `DATA_QUALITY_REPORT.md` từ cùng một snapshot SQLite.

## 10. Định nghĩa hoàn tất toàn bộ

Backlog này được xem là hoàn tất khi:

- không còn nhầm lẫn giữa lỗi crawl, nguồn đang biên soạn và tổ hợp không áp
  dụng;
- mọi tổng tín chỉ đã điền đều có provenance và không cộng trùng nhóm tự chọn;
- mọi gap CTĐT có trạng thái giải thích được, nhưng gap không có bằng chứng vẫn
  được giữ công khai;
- trang danh mục tài chính không bị trình bày như chính sách;
- PDF 404 không làm mất dữ liệu API hoặc làm pipeline thất bại;
- toàn bộ quality check bắt buộc và test suite đều đạt.
