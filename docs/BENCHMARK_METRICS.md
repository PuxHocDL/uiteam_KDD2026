# Benchmark Metrics — Định nghĩa & Lý do sử dụng

> Nguồn đối chiếu: `docs/KDD_Cup_2026_Creative_Track.md` (rubric chấm điểm §7, mục §3
> "Ba hướng đổi mới") và kiến trúc engine mô tả trong `CLAUDE.md`.
> Dữ liệu: Phase-1 Demo Data (`data/public/`, copy từ
> `assets/benchmark data/demo_samples_0417/public/`) — 50 task: 15 easy /
> 23 medium / 11 hard / 1 extreme.

Mục tiêu của file này: với mỗi metric, giải thích **đo cái gì**, **lấy từ đâu
trong code**, và **tại sao nó cần thiết cho đúng tiêu chí nào của rubric** —
để khi viết báo cáo, mỗi con số đều truy được về một dòng cụ thể trong luật
thi, không phải số liệu rời rạc.

---

## 0. Kết quả full-run (50/50 task, cả 2 model) — số liệu sạch, không rate-limit

Chạy đầy đủ `dabench run-benchmark` trên toàn bộ Phase-1 Demo Data, sau đó
`dabench eval` + `scripts/analyze_reliability.py` +
`scripts/compare_cost_quality.py`. Đây là số liệu final.

**Phương pháp lấy bản `gpt-4o-mini` sạch (minh bạch để tái tạo):** lần chạy
đầu (`full_gpt4o_mini`, `max_workers=24`) dính rate-limit nặng (xem lịch sử
hội thoại) khiến 1 task abort hẳn và làm nhiễu chỉ số reliability. Đã thử
giảm dần `max_workers` (24→3→1) nhưng ngay cả `max_workers=3` vẫn còn 13/50
task dính 429 (dù nhẹ hơn nhiều — 13 step so với 278 trước đó). Thay vì chạy
lại toàn bộ 50 task (tốn thời gian không cần thiết), **chỉ 13 task bị ảnh
hưởng được chạy lại tuần tự** (`max_workers=1`, không dính 429 nào) rồi
**merge** với 37 task sạch từ bản `max_workers=3`:
- `artifacts/runs/full_gpt4o_mini_clean/` — 37 task sạch (max_workers=3)
- `artifacts/runs/full_gpt4o_mini_patch/` — 13 task rerun tuần tự, sạch
- `artifacts/runs/full_gpt4o_mini_final/` — merge của 2 thư mục trên, dùng
  làm nguồn số liệu chính thức bên dưới. **Toàn bộ 50/50 prediction trong
  thư mục final đều đến từ 1 lần gọi API/task duy nhất, không có prediction
  nào bị chỉnh sửa/thay thế thủ công** — chỉ khác nhau ở `max_workers` giữa
  2 batch, không ảnh hưởng đến nội dung câu trả lời của model.

### Tổng quan

| | gpt-4o (đắt) | gpt-4o-mini (rẻ, đã loại nhiễu rate-limit) |
|---|---:|---:|
| Task hoàn thành & chấm điểm | 50/50 | 50/50 (0 task abort) |
| Rate-limit (429) trong run cuối | 0 | **0** |
| Avg score | **0.6543** | **0.5953** |
| Perfect (1.0) | 32/50 | 29/50 |
| Zero (fail hoàn toàn) | 16/50 | 19/50 |
| Tổng cost | $17.4267 | $1.28998 |
| Cost trung bình/task | $0.34853 | $0.02580 |
| Tổng token | 6,728,390 | 8,282,311 |
| Tool error rate | 11.56% | **27.08%** (0% do rate-limit — số sạch thật) |
| Self-recovery rate | 69.39% | 61.54% |

**Kết luận cost-quality (số liệu sạch cuối cùng — khác cả 2 lần trước):**
`gpt-4o` đắt hơn **13.5 lần** nhưng chỉ nhỉnh hơn **+0.059 điểm** (0.6543 vs
0.5953) — khoảng cách nhỏ hơn RẤT NHIỀU so với con số nhiễu trước đó (+0.21).
Điều này cho thấy chính rate-limit (không phải bản chất model) là nguyên
nhân chính khiến gpt-4o-mini trông kém hơn nhiều trong lần đo đầu — sau khi
loại nhiễu, gpt-4o-mini là lựa chọn cost-effective rõ rệt: **rẻ hơn 13.5 lần
cho chưa tới 6% điểm số thấp hơn**. Đây là minh chứng định lượng mạnh cho
đúng cảnh báo §7.5 "model đắt không tự động thắng", và cũng là bài học
phương pháp luận đáng đưa vào báo cáo: hạ tầng (quota/concurrency) có thể
làm sai lệch hoàn toàn kết luận về chất lượng model nếu không kiểm soát.

### Score theo difficulty

| Difficulty | gpt-4o | gpt-4o-mini (sạch) |
|---|---:|---:|
| Easy (15/15 task) | 0.6844 | 0.6844 |
| Medium (23/23 task) | 0.7587 | 0.6957 |
| Hard (11/11 task) | 0.4545 | 0.2273 |
| Extreme (1/1 task) | 0.0000 | 1.0000 |

Đáng chú ý: ở mức **Easy, 2 model cho điểm trung bình bằng hệt nhau
(0.6844)** — gpt-4o-mini hoàn toàn đủ dùng cho task dễ. Khoảng cách thật sự
chỉ mở ra rõ rệt ở mức **Hard** (0.4545 vs 0.2273) — đây là nơi nên cân nhắc
dùng model đắt hơn nếu cần, thay vì áp dụng 1 model cho mọi độ khó. Mức
extreme vẫn chỉ n=1, coi là case study (xem §5).

### Ghi chú: dữ liệu reliability giờ đã sạch hoàn toàn

Không còn error step nào liên quan 429/rate-limit trong bản `full_gpt4o_mini_final`
(xác nhận bằng `scripts/analyze_reliability.py`, cột "of which rate-limit" =
0/143). Tool error rate 27.08% của gpt-4o-mini là số **thật 100%**, không
còn là ước tính "đã tách nhiễu" như bản trước — và **cao hơn** ước tính tách
nhiễu trước đó (18.75%), cho thấy ngay cả kỹ thuật tách nhiễu theo từ khóa
cũng không hoàn hảo; **chạy lại sạch từ đầu vẫn đáng tin hơn** là cố gắng
làm sạch số liệu nhiễu sau khi thu thập.

---

## 1. Nhóm Độ chính xác (Accuracy)

### 1.1 Score / Recall (value-overlap có phạt cột thừa)

- **Định nghĩa:** `score_task()` (`src/data_agent_baseline/benchmark/scoring.py`)
  so khớp từng cột giữa `prediction.csv` và `gold.csv` theo tập giá trị đã
  chuẩn hoá (số, ngày giờ, chuỗi). `recall = matched_cols / gold_cols`,
  `score = recall − λ × (extra_cols / pred_cols)` với `λ=0.1` mặc định.
- **Rationale kiến trúc:** đây là cơ chế chấm điểm gốc của DataAgent-Bench,
  không phải phát minh riêng — dùng để đảm bảo số liệu so sánh được với chuẩn
  chung của cuộc thi.
- **Rationale rubric:** trực tiếp phục vụ **§7.4 "Hiệu quả & chất lượng bằng
  chứng"** ("Có kết quả định lượng không? Có baseline hợp lý không?") và
  **§7.3 "Năng lực hệ thống Data Agent"** — vì để đạt `score=1.0` agent phải
  tự lập kế hoạch, chọn tool, và tổng hợp đúng cấu trúc bảng mà câu hỏi yêu
  cầu, không chỉ "trả lời đúng nội dung".

### 1.2 % Task điểm tuyệt đối (1.0) và % điểm 0 (fail hoàn toàn)

- **Định nghĩa:** đếm số task có `score >= 1.0` và số task có `score == 0.0`
  trên tổng số task đánh giá (`dabench eval`).
- **Rationale rubric:** §7.4 yêu cầu tường minh *"Có trình bày case thất bại
  không?"* — đây là bằng chứng bắt buộc, không phải tuỳ chọn. Chỉ báo cáo
  average score mà giấu đi tỷ lệ fail hoàn toàn sẽ bị đánh giá là thiếu minh
  bạch (liên quan đến §8 "Hành vi bị cấm — trình bày sai sự thật").

### 1.3 Score theo từng difficulty (easy/medium/hard/extreme)

- **Định nghĩa:** nhóm kết quả theo `task.difficulty` (đọc từ `task.json`,
  bảng "Score by Difficulty" trong `dabench eval`).
- **Rationale kiến trúc:** 4 mức difficulty của Phase-1 map **1-1** vào 4
  nhánh routing của Hybrid-B (`run/runner.py::_select_agent_routing`): easy/
  medium → ReAct, extreme → DRAGIN, hard → heuristic (multi-source/long-doc
  signals). Đo score theo difficulty tức là đo trực tiếp hiệu quả của cơ chế
  routing này — không đo được nếu chỉ nhìn average score gộp.
- **Rationale rubric:** §7.1 *"Có xử lý ràng buộc phức tạp, nhiễu, thông tin
  thiếu, đa nguồn dữ liệu không?"* và §7.3 *"Có khả năng suy luận đa bước? Có
  thích ứng với tác vụ/dữ liệu chưa từng thấy không?"* — cả hai đều đòi hỏi
  breakdown theo độ khó, không phải một con số trung bình.

### 1.4 Score theo modality context (csv-only / db / doc-heavy)

- **Định nghĩa:** nhóm task theo loại file trong `context/` (csv, db, json,
  doc, knowledge.md) — tự tổng hợp từ `data/public/input/task_*/context/`.
- **Kết quả đo được (full 50 task, số liệu sạch):**

  | Modality group | n | gpt-4o | gpt-4o-mini |
  |---|---:|---:|---:|
  | csv/json-only (không db, không doc) | 15 | 0.6844 | 0.6844 |
  | db + structured (đa nguồn, cần cross-file JOIN) | 23 | **0.7587** | 0.6957 |
  | doc-heavy (tài liệu phi cấu trúc) | 12 | 0.4167 | 0.2917 |

  Phát hiện đáng chú ý: **modality gần như trùng khớp 1-1 với difficulty**
  trong bộ Phase-1 này (doc-heavy = 12 task = đúng bằng hard+extreme = 11+1;
  db+structured = 23 = đúng bằng medium; csv/json-only = 15 = đúng bằng easy)
  — nghĩa là bảng này và bảng "Score theo difficulty" ở §0 về bản chất đang
  đo cùng 1 trục, không phải 2 chiều độc lập trong bộ dữ liệu Phase-1 demo.
  Đây là hạn chế cần nêu rõ (xem §5): muốn tách bạch "khó vì modality" khỏi
  "khó vì độ phức tạp câu hỏi" cần bộ dữ liệu có modality và difficulty biến
  thiên độc lập — Phase-1 demo không cung cấp việc đó.
- **Rationale kiến trúc:** mỗi modality kích hoạt một tool khác nhau trong
  `tools/registry.py` (`sqlite`/`duckdb_exec` cho db, `python_exec`/
  `filesystem` cho csv/json, xử lý tài liệu dài cho `doc/`) — đo theo modality
  chứng minh diện bao phủ thật của tool registry, không chỉ số liệu tổng.
  Điểm doc-heavy thấp nhất ở cả 2 model (0.42 / 0.29) khớp với kỳ vọng: đây
  là nhánh mà Hybrid-B route sang DRAGIN (retrieval động) thay vì ReAct
  thuần — vẫn là điểm yếu nhất của engine trên bộ dữ liệu này, đáng nêu ở
  mục "hạn chế" của báo cáo (4.1.7).
- **Rationale rubric:** §7.3 *"Có thể hiểu nguồn dữ liệu, chọn công cụ, thực
  thi phân tích không?"*.

---

## 2. Nhóm Hiệu quả vận hành (Efficiency)

### 2.1 Latency (`e2e_elapsed_seconds`) theo difficulty

- **Định nghĩa:** thời gian wall-clock từ lúc nhận task đến khi có
  `prediction.csv`, đo trong `run/runner.py::run_single_task` bằng
  `perf_counter()`, ghi vào `trace.json`.
- **Kết quả đo được (full 50 task, số liệu sạch):**

  | Difficulty | gpt-4o avg time (s) | gpt-4o-mini avg time (s) |
  |---|---:|---:|
  | Easy | 20.5 | 53.5 |
  | Medium | 34.7 | 46.8 |
  | Hard | 39.1 | 94.8 |
  | Extreme (n=1) | 28.3 | 35.7 |

  Đáng chú ý: `gpt-4o-mini` **chậm hơn** `gpt-4o` ở mọi mức difficulty (đặc
  biệt Hard: 94.8s vs 39.1s) dù model rẻ hơn nhiều — vì cần nhiều bước hơn để
  đạt cùng kết luận (xem 2.2). Đây là góc độ "cân nhắc thực tế" khác ngoài
  cost thuần: nếu use case cần phản hồi nhanh (VD tương tác Co-pilot thời gian
  thực), gpt-4o-mini không tự động là lựa chọn tốt hơn dù rẻ hơn 13.5 lần —
  đúng minh hoạ cho §7.5 "cân nhắc độ trễ" tách biệt khỏi "cân nhắc chi phí".
- **Rationale rubric:** **§7.5 "Độ tin cậy & cân nhắc thực tế"** liệt kê rõ
  *"Cân nhắc độ trễ"* là một tiêu chí chấm điểm riêng (15% trọng số) — không
  đo latency thì không có gì để nói ở mục này.

### 2.2 Số bước trung bình (`num_steps`) theo difficulty

- **Định nghĩa:** độ dài `trace.json.steps[]` — số vòng lặp
  thought→action→observation mà `ReActAgent`/`DRAGINAgent` thực hiện.
- **Kết quả đo được (full 50 task, số liệu sạch):**

  | Difficulty | gpt-4o avg steps | gpt-4o-mini avg steps |
  |---|---:|---:|
  | Easy | 6.7 | 10.7 |
  | Medium | 9.0 | 11.3 |
  | Hard | 9.8 | 9.4 |
  | Extreme (n=1) | 9.0 | 6.0 |

  Cả 2 model đều có `num_steps` trung bình 6-11 mỗi task — bằng chứng định
  lượng rõ ràng cho vòng lặp suy luận đa bước, không phải gọi LLM 1 lần. Ở
  mức Easy/Medium, `gpt-4o-mini` cần **~4 bước nhiều hơn** để đạt cùng độ
  khó — hợp lý vì reasoning yếu hơn cần nhiều lần thử/sửa hơn — và đây chính
  là nguyên nhân trực tiếp khiến latency ở 2.1 cao hơn dù chi phí mỗi bước rẻ
  hơn nhiều lần.
- **Rationale kiến trúc:** đây là bằng chứng trực tiếp cho việc engine thực
  sự "suy luận đa bước" (không phải gọi LLM một lần) — đúng phân biệt mà
  rubric nêu ở §7.3.
- **Rationale rubric:** §7.3 nhấn mạnh: *"'Gọi LLM một lần để sinh câu trả
  lời' KHÔNG tương đương với 'Data Agent'. Hệ thống cần cảm nhận, quyết định,
  hành động, và sửa lỗi dựa trên trạng thái môi trường."* — `num_steps > 1`
  trên phần lớn task là bằng chứng định lượng cho câu này.

### 2.3 Cost / Token per task, theo difficulty và theo model

- **Định nghĩa:** `agents/model.py` ghi lại `usage` (prompt/completion/total
  tokens) sau mỗi lệnh gọi LLM (thread-safe, cả 2 adapter OpenAI và Azure).
  `run/runner.py` tính delta usage cho từng task và quy đổi
  `estimated_cost_usd` theo bảng giá xấp xỉ (`MODEL_PRICING_PER_1M_TOKENS`),
  ghi vào `trace.json.token_usage`.
- **Kết quả đo được (full 50 task, số liệu sạch):**

  | Difficulty | gpt-4o avg cost/task | gpt-4o-mini avg cost/task |
  |---|---:|---:|
  | Easy | $0.22192 | $0.02514 |
  | Medium | $0.35628 | $0.02682 |
  | Hard | $0.49604 | $0.02563 |
  | Extreme (n=1) | $0.44694 | $0.01399 |

  Chênh lệch cost giữa 2 model tương đối ổn định (~13-16 lần) qua mọi mức
  difficulty, không thu hẹp dù `gpt-4o-mini` cần nhiều bước hơn (2.2) — vì
  đơn giá token của gpt-4o-mini rẻ hơn đủ nhiều để bù lại số bước tăng thêm.
- **Rationale rubric:** §7.5 nêu tường minh: *"Chỉ đơn giản dùng model đắt
  tiền hơn để đạt kết quả tốt hơn sẽ KHÔNG tự động được điểm cao"* và mục
  4.1.6 báo cáo yêu cầu "cân nhắc thực tế (chi phí, độ trễ...)". Không có số
  liệu cost thì không thể phản bác/chứng minh luận điểm này bằng gì cả —
  đây là metric có tác động rubric cao nhất nhưng vốn hoàn toàn vắng mặt
  trong engine trước khi bổ sung.

### 2.4 Cost-quality tradeoff (model rẻ vs đắt)

- **Định nghĩa:** chạy cùng bộ task với 2 model khác cấp giá trên cùng một
  Azure resource (`gpt-4o` "đắt" vs `gpt-4o-mini` "rẻ", dò được qua
  `scripts/`), so sánh Δscore và Δcost (`scripts/compare_cost_quality.py`).
- **Kết quả đo được (full 50 task, số liệu sạch — xem §0):** `gpt-4o` đắt
  hơn `gpt-4o-mini` **13.5 lần** ($0.349 vs $0.026/task) nhưng chỉ đạt avg
  score cao hơn **+0.059** (0.6543 vs 0.5953). Số liệu này đã trải qua 2 lần
  điều chỉnh: bản đầu (n=4 smoke-test) nói mini thắng cả điểm lẫn giá; bản
  full-run đầu tiên (bị nhiễu rate-limit nặng) lại nói gpt-4o thắng cách biệt
  lớn (+0.21); bản **sạch cuối cùng** (§0) cho thấy khoảng cách thật chỉ
  +0.059 — nhỏ hơn nhiều so với cả 2 ước tính trước.
- **Rationale rubric:** đây là minh chứng *trực tiếp* và *định lượng* cho
  đúng câu cảnh báo ở §7.5 — không phải diễn giải suông mà có số liệu thật
  từ chính engine đang được chấm điểm. Với chênh lệch thật chỉ +0.059 điểm
  đổi lấy chi phí gấp 13.5 lần, kết luận hợp lý nhất là **gpt-4o-mini là lựa
  chọn mặc định cost-effective**, chỉ nên chuyển sang gpt-4o cho phân khúc
  task khó (xem "Score theo difficulty" ở §0 — khoảng cách chỉ thật sự đáng
  kể ở mức Hard) — đúng tinh thần "cân nhắc thực tế" mà rubric muốn thấy,
  không phải kết luận một chiều "luôn dùng model rẻ" hay "luôn dùng model
  đắt".

---

## 3. Nhóm Độ tin cậy & Phục hồi lỗi (Reliability)

### 3.1 Tool error rate

- **Định nghĩa:** tỷ lệ step có `observation.ok == False` hoặc
  `observation.content.error` hoặc `stagnation_warning` trên tổng số step
  (`scripts/analyze_reliability.py error-recovery`, đọc trực tiếp
  `trace.json.steps[]` — dữ liệu này engine đã ghi sẵn qua cơ chế recovery-
  hint trong `agents/react.py`, chỉ thiếu tầng tổng hợp).
- **Kết quả đo được (full 50 task, số liệu sạch, 0 rate-limit — xem §0):**
  `gpt-4o` **11.56%** (49/424), `gpt-4o-mini` **27.08%** (143/528) — đây là
  số thật 100%, không còn lẫn 429. Trước khi có bản chạy sạch, số ước tính
  "đã tách nhiễu" từ bản nhiễu là 18.75% — thấp hơn con số thật 27.08% khá
  nhiều, cho thấy kỹ thuật tách nhiễu theo từ khóa (đếm "429" trong text lỗi)
  chỉ là xấp xỉ, không thay thế được việc chạy lại sạch từ đầu.
- **Rationale rubric:** §7.5 *"Xử lý lỗi và khả năng phục hồi"*.

### 3.2 Self-recovery rate

- **Định nghĩa:** trong số các step lỗi, tỷ lệ mà step **kế tiếp** không còn
  lỗi (proxy cho việc agent tự sửa được dựa trên recovery hint mà
  `react.py::_check_stagnation` / action-specific recovery hints tạo ra).
- **Kết quả đo được (full 50 task, số liệu sạch):** `gpt-4o` **69.39%**
  (34/49 error step tự phục hồi, 16/21 task phục hồi hoàn toàn), `gpt-4o-mini`
  **61.54%** (17/41 task phục hồi hoàn toàn) — không còn confound rate-limit,
  đây là khoảng cách reliability thật giữa 2 model, không phải artefact hạ
  tầng.
- **Rationale kiến trúc:** đây là bằng chứng cho tính năng "recovery hints"
  đã tồn tại trong engine (`agents/react.py` dòng ~233-501) nhưng chưa từng
  được đo lường — metric này biến một cơ chế nội bộ thành con số báo cáo
  được.
- **Rationale rubric:** §7.3 *"Có thể nhận biết lỗi, xác minh output, hoặc
  chủ động yêu cầu làm rõ không?"* và §7.5 *"khả năng phục hồi"*.

### 3.3 Độ ổn định (variance/stdev khi chạy lặp lại cùng task)

- **Định nghĩa:** chạy cùng 1 task N lần độc lập (`temperature=0.0` nhưng
  vẫn có biến động do tool-call timing, retry, LLM sampling dư), tính
  `stdev(score)` qua các lần chạy (`scripts/analyze_reliability.py
  stability`).
- **Kết quả đo được:** `task_11` (easy) dao động [0.267, 0.267, 1.0] qua 3
  lần chạy — `stdev=0.42`. Đây là phát hiện thật, không phải giả định: một
  task "dễ" vẫn có thể không ổn định, đáng đưa vào phần "hạn chế" của báo
  cáo (mục 4.1.7).
- **Rationale rubric:** §7.5 *"Độ ổn định và bền vững của hệ thống"* — đây là
  tiêu chí duy nhất trong rubric không thể suy ra từ một lần chạy đơn lẻ.

---

## 4. Nhóm đã có sẵn trong engine, chỉ cần chạy (tham chiếu nhanh)

Các metric sau **không cần sửa code**, chỉ cần lệnh `dabench` có sẵn — liệt
kê lại ở đây để bảng chỉ mục đầy đủ:

| Metric | Lệnh | Rubric |
|---|---|---|
| Self-consistency / consensus improvement | `dabench run-consensus` + `eval-consensus` | §7.4, §7.5 |
| So sánh agent_mode (react/dragin/hybrid_b/multi) | đổi `agent.agent_mode` trong config | §7.2 |
| Ablation tham số DRAGIN (rind_threshold, qfs_top_n) | sweep config trên tập hard/extreme | §7.2 |

---

## 5. Giới hạn cần nêu trong báo cáo

- **Extreme difficulty chỉ có 1 task** trong toàn bộ Phase-1 demo data — bất
  kỳ kết luận nào về mức "extreme" (kể cả kết quả đảo chiều ở §0: gpt-4o=0,
  gpt-4o-mini=1.0) đều là case study đơn lẻ, không có ý nghĩa thống kê.
- **Rate-limit ở các lần chạy đầu đã được xử lý bằng cách chạy lại** (xem §0
  "Phương pháp lấy bản gpt-4o-mini sạch") — số liệu §0–§3 hiện dùng
  `artifacts/runs/full_gpt4o_mini_final` (0 step nào dính 429), không còn
  cần cảnh báo confound. Vẫn nên nêu trong báo cáo *tại sao* cần bước xử lý
  này — đây là bài học thực tế về vận hành hệ thống (§7.5 "cân nhắc thực
  tế"), không phải hạn chế cần giấu đi.
- **Bảng giá cost là ước tính công khai (public list price)**, không phải số
  hoá đơn Azure thực tế — có thể lệch theo hợp đồng/khu vực, cần ghi rõ đây
  là *estimate* trong báo cáo (đã có trong docstring `MODEL_PRICING_PER_1M_TOKENS`).
- **Độ ổn định (§3.3) mới đo trên 1 task (task_11, 3 lần chạy)** — đủ để
  chứng minh hiện tượng "task dễ vẫn có thể không ổn định" tồn tại, nhưng
  chưa đủ để ước tính variance trung bình toàn hệ thống; muốn số liệu
  variance đại diện cần lặp lại trên nhiều task hơn (xem đề xuất cũ: 2
  task/difficulty × 2-3 lần chạy).
- Các metric ở §0–§3 đo trên **Phase-1 data** — theo phân tích trước đó,
  **không thay thế được** cho 2 nhóm metric mà rubric §7.2 (generalization,
  20% trọng số) và §7.1 (giá trị thực tế, 15%) đòi hỏi, vốn cần dữ liệu/hoạt
  động ngoài Phase-1 (xem hội thoại trước, không lặp lại ở đây).

---

## 6. Chi phí thực tế đã dùng (Azure OpenAI, ước tính theo public list price)

Số liệu thật, không còn ngoại suy — tổng hợp từ toàn bộ `trace.json.token_usage`
trong `artifacts/runs/` (bao gồm cả smoke-test/stability-test trước đó).

| Hạng mục | Chi phí |
|---|---:|
| `full_gpt4o` (50/50 task, số liệu chính thức) | $17.4267 |
| `full_gpt4o_mini_final` (50/50 task merge, số liệu chính thức) | $1.2900 |
| **Tổng số liệu dùng trong báo cáo (§0)** | **$18.7167** |
| Các lần chạy trung gian bị rate-limit / chạy thử (`full_gpt4o_mini` gốc, `full_gpt4o_mini_clean` phần bị thay thế, smoke-test, stability-test) | $6.3228 |
| **Tổng đã chi toàn bộ session (mọi lần thử, kể cả lần bị bỏ)** | **$25.04** |

Ghi chú:
- Chỉ `full_gpt4o` và `full_gpt4o_mini_final` là số liệu chính thức dùng
  trong báo cáo — các lần chạy trung gian bị rate-limit không đại diện, dù
  vẫn tốn phí thật (retry 429 không tính phí, nhưng phần completion đã sinh
  ra trước khi bị thay thế thì có tính phí).
- Không bao gồm `run-consensus` (self-consistency) — chưa chạy trong session
  này. Nếu muốn thêm bằng chứng cho §7.4/§7.5, ước tính chi phí cho 2 vòng
  bổ sung trên `gpt-4o` full 50 task là khoảng **+$35** (2× chi phí 1 lần
  full-run); đề xuất giới hạn ở tập con hard/extreme (12 task) để giảm còn
  khoảng ~$8.
- Chi phí là *estimate* theo bảng giá public list trong
  `MODEL_PRICING_PER_1M_TOKENS` (`agents/model.py`), không phải hoá đơn Azure
  thực tế.

---

## 7. Vị trí artifact (prediction + trace + eval)

Toàn bộ output của full-run nằm trong `artifacts/runs/` (thư mục này bị
`.gitignore`, không commit — chỉ tồn tại local, cần đính kèm riêng nếu dùng
làm bằng chứng nộp bài):

| Run | Thư mục | Nội dung |
|---|---|---|
| gpt-4o, 50/50 task (chính thức) | `artifacts/runs/full_gpt4o/` | `task_*/prediction.csv`, `task_*/trace.json` (gồm `token_usage`, `steps[]`), `summary.json` |
| gpt-4o-mini, 50/50 task, **sạch, chính thức** | `artifacts/runs/full_gpt4o_mini_final/` | merge của `full_gpt4o_mini_clean/` (37 task, `max_workers=3`) + `full_gpt4o_mini_patch/` (13 task rerun tuần tự `max_workers=1`) — xem §0 |
| gpt-4o-mini, bản gốc bị nhiễu rate-limit (KHÔNG dùng để báo cáo) | `artifacts/runs/full_gpt4o_mini/` | giữ lại để đối chiếu/minh hoạ vấn đề rate-limit nếu cần trong phần "hạn chế" của báo cáo |
| Stability test (task_11 × 3 lần) | `artifacts/runs/smoke_test_gpt4o/`, `stability_run_2/`, `stability_run_3/` | dùng cho §3.3 |

Lệnh tái tạo bảng eval/reliability/cost-quality ở §0 từ artifact có sẵn
(không cần gọi lại API):

```bash
uv run dabench eval --run-dir artifacts/runs/full_gpt4o
uv run dabench eval --run-dir artifacts/runs/full_gpt4o_mini_final
uv run python scripts/analyze_reliability.py error-recovery artifacts/runs/full_gpt4o
uv run python scripts/analyze_reliability.py error-recovery artifacts/runs/full_gpt4o_mini_final
uv run python scripts/compare_cost_quality.py artifacts/runs/full_gpt4o artifacts/runs/full_gpt4o_mini_final \
    --gold-dir data/public/output --label-a gpt-4o --label-b gpt-4o-mini
```

Để tái tạo lại đúng quy trình tạo bản `full_gpt4o_mini_final` từ đầu (chạy
API thật, tốn phí):

```bash
uv run python scripts/run_full_benchmark.py --model gpt-4o-mini \
    --run-id full_gpt4o_mini_clean --max-workers 3
# Kiểm tra output cuối: nếu có dòng "⚠ N step(s) hit a 429", xác định các
# task_id bị ảnh hưởng (script này in cảnh báo tự động), rồi rerun tuần tự:
uv run python scripts/smoke_test_run.py --config configs/hybrid_b_baseline.example.yaml \
    --run-id full_gpt4o_mini_patch --model gpt-4o-mini <danh sách task_id bị ảnh hưởng>
# Merge 2 thư mục trên (loại các task_id đã patch khỏi bản _clean) thành
# full_gpt4o_mini_final trước khi eval.
```
