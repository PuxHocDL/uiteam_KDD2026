# KDD Cup 2026 — Data Agents: Creative Track
## Giải thích luật, yêu cầu và ràng buộc

> Nguồn: Official Rules — https://dataagent.top/rules/phase-2/creative
> Đây là Phase-2 của cuộc thi, tổ chức bởi Tsinghua University & HKUST (Guangzhou), thuộc ACM SIGKDD 2026.

---

## 1. Tổng quan tài liệu

Đây là đặc tả đánh giá chính thức cho **Creative Track** của cuộc thi KDD Cup 2026 Data Agents. Tài liệu quy định:
- Yêu cầu nộp bài
- Tiêu chí đánh giá
- Thang điểm chấm
- Chuẩn mực liêm chính học thuật

**Lưu ý quan trọng:** Creative Track **không** ưu tiên một điểm số benchmark duy nhất ở tập test Phase-2. Thay vào đó, track này khuyến khích các giải pháp Data Agent có:
- Giá trị thực tiễn cao hơn
- Tính hoàn thiện hệ thống
- Cảm hứng nghiên cứu

Ban tổ chức có quyền cập nhật tài liệu; phiên bản mới nhất luôn được ưu tiên áp dụng.

---

## 2. Sứ mệnh của Creative Track

Một bài dự thi hợp lệ cần thể hiện các đặc điểm cốt lõi sau:

1. Giải quyết một tác vụ phân tích dữ liệu, quản lý dữ liệu, hoặc hỗ trợ ra quyết định có độ phức tạp đáng kể;
2. Chấp nhận đầu vào là câu hỏi ngôn ngữ tự nhiên, mục tiêu kinh doanh, hoặc yêu cầu phân tích;
3. Tự động thực hiện một phần việc lập kế hoạch, hiểu dữ liệu, gọi công cụ, thực thi phân tích, hoặc xác minh kết quả;
4. Xử lý một hoặc nhiều nguồn dữ liệu (bảng, database, tài liệu, video, log, API, knowledge base...);
5. Tạo ra kết quả phân tích có thể diễn giải, xác minh, hoặc sử dụng được trong thực tế.

---

## 3. Ba hướng đổi mới (Innovation Directions)

Đội thi có thể chọn **một hoặc kết hợp nhiều hướng**:

### 3.1 Đổi mới về phương pháp (Agent Harness Design)
Tập trung vào trí tuệ cốt lõi của Data Agent qua thuật toán, kiến trúc, hoặc phương pháp suy luận mới. Ví dụ:
- Cơ chế lập kế hoạch mới cho suy luận đa bước (hierarchical planning, replanning khi thất bại, plan verification)
- Chiến lược sử dụng công cụ mới (tool composition, tạo công cụ động, tối ưu chọn công cụ)
- Quản lý bộ nhớ/ngữ cảnh nâng cao cho tác vụ dài hạn
- Vòng lặp xác minh và tự sửa lỗi
- Framework hợp tác đa agent

**Lưu ý:** Phương pháp cần có khả năng tổng quát hóa, không chỉ hard-code cho một dataset cụ thể — pipeline cứng nhắc sẽ bị chấm điểm thấp hơn.

### 3.2 Đổi mới về kỹ thuật hệ thống (Systems Engineering)
Tập trung xây dựng hệ thống vững chắc, có khả năng mở rộng, thân thiện người dùng để triển khai thực tế. Ví dụ:
- Thiết kế môi trường dữ liệu (ingest, clean, index, federate dữ liệu không đồng nhất)
- Kiến trúc kỹ thuật (execution engine hiệu quả, caching, xử lý phân tán)
- Giao diện tương tác (conversational UI, dashboard, API-first)
- Khả năng quan sát/debug (trace suy luận, data lineage, confidence score)
- Cơ chế an toàn (privacy, access control, sanitization, human oversight)

**Lưu ý:** Hệ thống chạy hiệu quả trên phần cứng khiêm tốn có thể được đánh giá cao hơn prototype tốn tài nguyên nhưng cải thiện không đáng kể.

### 3.3 Đổi mới theo kịch bản thực tế (Scenario-Based)
Giống một track khởi nghiệp/nghiên cứu ứng dụng: xác định nhu cầu thực tế chưa được đáp ứng, xác thực với người dùng/chuyên gia thực, và xây dựng giải pháp có giá trị thực tiễn. Ví dụ:
- Lĩnh vực mới: y tế, chuỗi cung ứng, tổng hợp tài liệu khoa học, rủi ro tài chính, quy hoạch đô thị, giáo dục...
- Quy trình làm việc mới: tích hợp vào công cụ chuyên nghiệp hiện có (spreadsheet, BI, CRM) thay vì script độc lập
- Bằng chứng giá trị từ người dùng thật: phỏng vấn, khảo sát, triển khai thử nghiệm, số liệu sử dụng
- Giải quyết nỗi đau thực sự: tác vụ hiện đang làm thủ công bởi analyst/data engineer/chuyên gia

**Lưu ý:** Cần trả lời rõ: ai là người dùng, họ gặp khó khăn gì, tại sao công cụ hiện tại không đáp ứng được, và agent cải thiện quy trình của họ như thế nào — có bằng chứng thực tế.

---

## 4. Tài liệu cần nộp

Mỗi đội **phải** nộp đầy đủ các tài liệu sau. **Nộp thiếu có thể bị loại.**

### 4.1 Báo cáo dự án (Project Report)
- Giới hạn khuyến nghị: tối đa **6 trang** nội dung chính (không bắt buộc template), phụ lục không giới hạn hoặc giới hạn nhẹ
- Phải bao gồm:
  1. Vấn đề và động lực (Problem and Motivation)
  2. Hạn chế của các phương pháp hiện có
  3. Tổng quan hệ thống (kiến trúc, module lõi, luồng dữ liệu, workflow của agent)
  4. Điểm đổi mới chính (so với prompt engineering thông thường, tool calling đơn giản, hệ thống hiện có)
  5. Đánh giá và bằng chứng (kịch bản, dataset, metric, baseline, kết quả)
  6. Cân nhắc thực tế (chi phí, độ trễ, quyền riêng tư, bảo mật, khả năng mở rộng, mức độ can thiệp của con người)
  7. Hạn chế và trường hợp thất bại
  8. Tuyên bố khả năng tái tạo (reproducibility)

### 4.2 Video Demo
- Thời lượng khuyến nghị: **5–8 phút**
- Phải bao gồm:
  - Vấn đề mà hệ thống giải quyết
  - Demo thực tế đầu-cuối (end-to-end)
  - Quá trình ra quyết định / trajectory trung gian của agent
  - Ít nhất một case không tầm thường (non-trivial)
  - Kết quả định lượng hoặc bằng chứng so sánh
  - Hạn chế đã biết

### 4.3 Artifact hệ thống có thể xác minh
Cung cấp **ít nhất một trong hai**:
- Container có thể chạy được (bao gồm code)
- Repository code công khai

> Khuyến nghị mạnh: các đội vào vòng trình bày cuối nên cung cấp môi trường chạy được để ban tổ chức kiểm tra, ngay cả khi code chưa public.

### 4.4 Biểu mẫu công khai thông tin (Disclosure Form)
Bảng 1 trang công khai:
| Mục | Mô tả |
|---|---|
| Thư viện/Framework bên thứ ba | Liệt kê toàn bộ |
| Mô hình pre-trained | LLM, embedding, các mô hình khác |
| Nguồn dữ liệu | Dataset, API, knowledge base |
| Thành phần có con người tham gia | Mô tả can thiệp/thao tác thủ công |
| Đóng góp mã nguồn mở | Code tái sử dụng và đóng góp thực tế của đội |

---

## 5. Tài liệu bổ sung (không bắt buộc, có thể tăng điểm)
- Bài blog kỹ thuật
- Demo online tương tác
- Nghiên cứu người dùng / phản hồi thực tế
- Hồ sơ triển khai sản xuất
- Báo cáo stress test
- Nghiên cứu ablation
- Kiểm thử đối kháng (adversarial testing)
- Phân tích chi phí và độ trễ

---

## 6. Quy trình đánh giá

**Bước 1 — Đánh giá độc lập:** Mỗi bài nộp được ít nhất **3 giám khảo** chấm độc lập. Giám khảo sẽ:
- Đọc báo cáo dự án
- Xem video demo
- Kiểm tra các mục công khai bắt buộc
- Chấm điểm từng tiêu chí
- Quyết định có vào vòng trình bày online hay không

**Bước 2 — Vòng trình bày online (tùy chọn):** Nếu tài liệu không đủ để phân biệt các đội, ban tổ chức có thể tổ chức demo trực tiếp online. Đội sẽ được thông báo riêng nếu được chọn.

---

## 7. Thang điểm chấm (Scoring Rubric)

Thang điểm neo (anchored scale) 1–5 cho mỗi tiêu chí, nhân với trọng số tương ứng.

| Tiêu chí | Trọng số | Điểm tối đa |
|---|---|---|
| A. Giá trị & tính thực tế của vấn đề | 15% | 15 |
| B. Đổi mới & đóng góp kỹ thuật | 20% | 20 |
| C. Năng lực hệ thống Data Agent | 20% | 20 |
| D. Hiệu quả & chất lượng bằng chứng | 15% | 15 |
| E. Độ tin cậy & cân nhắc thực tế | 15% | 15 |
| F. Tính hoàn thiện & khả năng sử dụng | 10% | 10 |
| G. Khả năng tái tạo & chất lượng trình bày | 5% | 5 |
| **Tổng** | | **100** |

### 7.1 Giá trị & tính thực tế của vấn đề (15đ)
- Vấn đề có xuất phát từ ứng dụng thực tế không?
- Có nhóm người dùng rõ ràng không?
- Tác vụ có gần với thực tế hơn benchmark thông thường không?
- Có xử lý ràng buộc phức tạp, nhiễu, thông tin thiếu, đa nguồn dữ liệu không?
- Thành công của hệ thống có mang lại giá trị thực sự không?

### 7.2 Đổi mới & đóng góp kỹ thuật (20đ)
- Có đề xuất kiến trúc agent, cơ chế lập kế hoạch, hoặc workflow mới không?
- Có thách thức giới hạn hiện tại của Data Agent không?
- Có phương thức tương tác dữ liệu, cơ chế bộ nhớ, cơ chế xác minh, hoặc mô hình hợp tác mới không?
- Đổi mới có khả năng tổng quát hóa, không chỉ hard-code cho một case cụ thể không?

### 7.3 Năng lực hệ thống Data Agent (20đ)
- Có khả năng lập kế hoạch tự động không?
- Có thể hiểu nguồn dữ liệu, chọn công cụ, thực thi phân tích không?
- Có khả năng suy luận đa bước không?
- Có điều chỉnh động dựa trên phản hồi trung gian không?
- Có thể nhận biết lỗi, xác minh output, hoặc chủ động yêu cầu làm rõ không?
- Có thích ứng với tác vụ/dữ liệu chưa từng thấy không?

> **Phân biệt quan trọng:** "Gọi LLM một lần để sinh câu trả lời" **KHÔNG** tương đương với "Data Agent". Hệ thống cần "cảm nhận, quyết định, hành động, và sửa lỗi dựa trên trạng thái môi trường".

### 7.4 Hiệu quả & chất lượng bằng chứng (15đ)
- Có kết quả định lượng không?
- Có baseline hợp lý không?
- Có nghiên cứu ablation không?
- Có nhiều case minh họa không?
- Có trình bày case thất bại không?
- Có phản hồi người dùng thực hoặc dữ liệu triển khai sản xuất không?
- Metric đánh giá có phù hợp với mục tiêu hệ thống không?

*Khuyến khích đa dạng hóa metric.*

### 7.5 Độ tin cậy & cân nhắc thực tế (15đ)
> **Lưu ý rõ ràng:** Chỉ đơn giản dùng model đắt tiền hơn để đạt kết quả tốt hơn sẽ **không** tự động được điểm cao.

- Độ ổn định và bền vững của hệ thống
- Hiệu quả chi phí
- Cân nhắc độ trễ
- Biện pháp bảo mật & quyền riêng tư
- Khả năng mở rộng
- Xử lý lỗi và khả năng phục hồi

### 7.6 Tính hoàn thiện & khả năng sử dụng (10đ)
- Có hệ thống chạy được đầu-cuối không?
- Có mô hình tương tác hợp lý không?
- Trạng thái hiện tại của agent có được hiển thị rõ không?
- Kết quả có thể giải thích được không?
- Có hỗ trợ người dùng sửa/hỏi tiếp không?
- Cách triển khai có rào cản thấp cho sử dụng thực tế không?

> Thiết kế UI đẹp có thể là điểm cộng nhưng **không được lấn át** giá trị kỹ thuật và thực tiễn.

### 7.7 Khả năng tái tạo & chất lượng trình bày (5đ)
- Báo cáo dự án có rõ ràng không?
- Demo có đáng tin cậy không?
- Có sơ đồ kiến trúc hệ thống và hướng dẫn chạy không?
- Dependency có được công khai đầy đủ không?
- Giám khảo có thể xác minh các tuyên bố chính không?
- Code có công khai hoặc ít nhất ban tổ chức có thể kiểm tra được không?

---

## 8. Hành vi bị cấm (Prohibited Behaviors)

Các hành vi sau sẽ dẫn đến **loại khỏi cuộc thi**:

1. **Đạo văn (Plagiarism):** Tái sử dụng code mã nguồn mở mà không công khai đóng góp thực tế trong Disclosure Form. Bài dự thi không thể phân biệt được với dự án mã nguồn mở hiện có (không có khác biệt rõ rệt) là bị cấm.
2. **Không thể tái tạo hoặc trình bày sai sự thật:** Khi ban tổ chức review code phát hiện: hệ thống không thể tái tạo được; hành vi code không khớp với demo (VD: output hard-code, UI giả); hoặc các hình thức trình bày sai khác.

---

## 9. Phương thức nộp bài

### 9.1 Cổng nộp bài & Hạn chót
- Creative Track dùng chung kênh email nộp bài với Leaderboard Track.
- **Hạn chót:** Jul 5, 2026, 19:59 giờ Bắc Kinh (= Jul 4, 2026, 23:59 AoE)
- **Người gửi:** Phải gửi từ email đăng ký của trưởng nhóm; email khác sẽ không qua được xác minh
- **Tiêu đề email:** `[KDDCup2026 Data Agents] Creative Track Submission - <team_id>`
- **Chỉ nộp một lần duy nhất:** Mỗi đội chỉ được nộp bài Creative Track **một lần**. Không được nộp lại/nộp nhiều lần. Cần đảm bảo tài liệu đầy đủ và chính xác trước khi nộp.

### 9.2 Tài liệu bắt buộc đính kèm
Tất cả tài liệu phải được cung cấp dưới dạng **file đính kèm email** hoặc **link Google Drive tải về được**. Link xem online thuần túy (YouTube, Bilibili, trang web repo công khai) **không được chấp nhận** thay cho file tải về.

| Tài liệu | Định dạng | Đặt tên & yêu cầu |
|---|---|---|
| Báo cáo dự án | PDF | `creative_<team_id>_report.pdf` |
| Video demo | MP4 | `creative_<team_id>_video.mp4` — phải là file tải về được, không chấp nhận link streaming |
| Artifact có thể xác minh | Archive hoặc Docker image | Code: `creative_<team_id>_code.zip` hoặc `.tar.gz`; Docker: `creative_<team_id>:v1` (export dạng `.tar.gz`). Phải kèm hướng dẫn tái tạo (Markdown/PDF) gồm setup, dependency, cách chạy, kết quả mong đợi. Chỉ có link repo (không có file) là **không được chấp nhận** |
| Disclosure Form | PDF | `creative_<team_id>_disclosure.pdf` |

### 9.3 Yêu cầu chia sẻ Google Drive (nếu dùng, khuyến nghị cho file lớn)
- Upload tất cả file vào **một folder** Google Drive duy nhất
- Đặt quyền chia sẻ: **"Anyone with the link can view"**
- Đính kèm link folder trong email nộp bài
- Đảm bảo link còn hiệu lực trong suốt thời gian đánh giá — **link hết hạn sẽ làm bài nộp vô hiệu**

Ví dụ nội dung email:
```
Subject: [KDDCup2026 Data Agents] Creative Track Submission - team0000

Team ID: team0000
Google Drive Folder Link: https://drive.google.com/drive/folders/...

File Checklist:
- creative_team0000_report.pdf
- creative_team0000_video.mp4
- creative_team0000_code.zip         (hoặc .tar.gz / Docker image)
- creative_team0000_guide.md         (hoặc .pdf — hướng dẫn tái tạo)
- creative_team0000_disclosure.pdf
```

### 9.4 Checklist trước khi nộp
- [ ] Email gửi từ địa chỉ đăng ký của trưởng nhóm
- [ ] Tiêu đề email đúng định dạng quy định
- [ ] Đây là bài nộp Creative Track **duy nhất** của đội (không nộp nhiều lần)
- [ ] Đầy đủ tài liệu: báo cáo, video, artifact kèm hướng dẫn tái tạo, disclosure form
- [ ] Quyền Google Drive đặt là "Anyone with the link can view" (nếu dùng)
- [ ] File video tải về và phát được bình thường
- [ ] Code archive / Docker image tải về và giải nén được bình thường
- [ ] Báo cáo dự án không vượt quá 6 trang nội dung chính (phụ lục không tính)

### 9.5 Sau khi nộp bài
- Trong thời gian đánh giá, ban tổ chức có thể liên hệ qua email để lên lịch trình bày online và demo trực tiếp nếu cần làm rõ thêm
- Kết quả cuối cùng của Creative Track sẽ được công bố cùng Leaderboard Track vào **Jul 15, 2026 (AoE)**

---

## 10. Mốc thời gian tổng quan (Phase-2)

| Thời gian | Sự kiện |
|---|---|
| Jul 4–5, 2026 | Cửa sổ nộp bài cuối B-board |
| Jul 6–10, 2026 | Đánh giá B-board & kết quả Phase-2 |
| Jul 11–14, 2026 | Rà soát điều kiện dự thi (Qualification review) |
| Jul 15, 2026 | Thông báo giải thưởng |

**Ghi chú riêng cho Leaderboard Track (để so sánh):**
- Hạn nộp A-board: Jul 4, 2026, 19:59 Bắc Kinh (Jul 3, 23:59 AoE) — **sớm hơn 1 ngày** so với Creative Track
- Có dữ liệu khó hơn với modality "data video" mới
- Nộp bằng Docker image, đánh giá tự động
- Số phiên bản nộp bài Phase-2 bắt đầu lại từ v1, độc lập với Phase-1

---

## 11. Tóm tắt các điểm cần lưu ý nhất

1. **Không chỉ chấm điểm benchmark** — track này coi trọng tính thực tế, sáng tạo, và hoàn thiện hệ thống hơn.
2. **Chỉ được nộp 1 lần** — không có cơ hội sửa sau khi nộp.
3. **Hạn chót: Jul 5, 2026, 19:59 Bắc Kinh / Jul 4, 23:59 AoE.**
4. **Bắt buộc 4 loại tài liệu:** report (≤6 trang), video (5–8 phút), artifact chạy được + hướng dẫn tái tạo, disclosure form.
5. **Không chấp nhận link thuần** (YouTube, GitHub web page...) — phải là file tải về được.
6. **Model đắt tiền hơn không tự động cho điểm cao** — chú trọng hiệu quả chi phí thực tế.
7. **"Gọi LLM 1 lần" không phải là Data Agent** — cần thể hiện chu trình cảm nhận–quyết định–hành động–sửa lỗi.
8. **Đạo văn hoặc code không khớp demo → loại trực tiếp.**
9. **Email phải gửi từ địa chỉ trưởng nhóm đã đăng ký**, đúng tiêu đề quy định.
