# Quy Chuẩn Làm Việc Nhóm Trên GitHub

> **Dành cho:** Tất cả thành viên tham gia phát triển đề tài Nghiên cứu Khoa học "Hệ Thống Lập Lịch Trình Du Lịch Thông Minh".  
> **Mục tiêu:** Giữ gìn cây Git sạch sẽ, tránh xung đột mã nguồn (merge conflict) và ngăn chặn việc tạo ra các nhánh rác.

---

## I. QUY TẮC BẤT DI BẤT DỊCH (VÀNG)

1. **TUYỆT ĐỐI KHÔNG sửa code trực tiếp trên giao diện Web của GitHub:**
   - Việc nhấn nút cây bút "Edit this file" trên web sẽ tự động sinh ra các nhánh rác dạng `username-patch-1`, `Task3-sudo-patch-5`... làm ô nhiễm kho mã nguồn.
   - Mọi chỉnh sửa bắt buộc phải thực hiện trên máy tính cá nhân (VS Code / Terminal) rồi mới đẩy lên (`git push`).
2. **KHÔNG BAO GIỜ commit trực tiếp lên nhánh `main`:**
   - Nhánh `main` là nhánh bảo vệ tuyệt đối, chỉ dùng để nộp báo cáo, bảo vệ đề tài hoặc demo hội đồng.
   - Nhánh tích hợp chính để cả nhóm làm việc là **`develop`**.
3. **Mỗi tính năng một nhánh (One Feature - One Branch):**
   - Không làm nhiều tính năng khác nhau trên cùng một nhánh.

---

## II. QUY ƯỚC ĐẶT TÊN NHÁNH (BRANCHING CONVENTIONS)

Tất cả các nhánh mới phải được rẽ nhánh từ **`develop`** và tuân thủ định dạng sau (viết thường, dùng dấu gạch ngang `-`, không dấu tiếng Việt):

| Tiền tố | Mục đích | Ví dụ hợp lệ | Ví dụ SAI (Không được đặt) |
| :--- | :--- | :--- | :--- |
| **`feat/`** | Thêm tính năng mới | `feat/export-pdf`<br>`feat/chat-voice` | `featurethao`<br>`feature/hoang_fixbaitoan2` |
| **`fix/`** | Sửa lỗi hệ thống | `fix/map-zoom-error`<br>`fix/route-timeout` | `fix`<br>`feature/hoangcheck]` |
| **`research/`** | Chạy thực nghiệm, vẽ biểu đồ NCKH | `research/pareto-table1`<br>`research/hypervolume` | `chay-thu-nghiem`<br>`Dulichthongminh-SQL` |
| **`docs/`** | Cập nhật tài liệu, README | `docs/api-guide`<br>`docs/setup-readme` | `update-readme` |

---

## III. QUY TRÌNH LÀM VIỆC TỪNG BƯỚC (DAILY WORKFLOW)

### Bước 1: Trước khi bắt đầu làm tính năng mới
Luôn luôn lấy code mới nhất từ nhánh `develop` về máy:
```bash
git checkout develop
git pull origin develop
```

### Bước 2: Tạo nhánh mới cho tính năng của bạn
```bash
git checkout -b feat/ten-tinh-nang
```

### Bước 3: Viết code và Commit
Đặt thông điệp commit rõ ràng theo chuẩn:
- `feat: them chuc nang ...`
- `fix: sua loi ...`
- `docs: cap nhat tai lieu ...`
- `refactor: toi uu lai code ...`

```bash
git add .
git commit -m "feat: tich hop bo loc theo gio mo cua dia diem"
```

### Bước 4: Đẩy nhánh lên GitHub
```bash
git push -u origin feat/ten-tinh-nang
```

### Bước 5: Tạo Pull Request (PR) trên GitHub
1. Truy cập vào kho mã nguồn trên GitHub.
2. Bấm nút **Compare & pull request**.
3. **Chọn nhánh đích (base):** Bắt buộc chọn **`develop`** (KHÔNG chọn `main`).
4. Viết mô tả ngắn gọn những gì bạn đã làm.
5. Gửi link PR cho trưởng nhóm hoặc thành viên khác duyệt (Review).

### Bước 6: Xóa nhánh sau khi đã Merge
Sau khi Pull Request được chấp nhận và gộp vào `develop`:
- Bấm nút **Delete branch** trên GitHub.
- Dưới máy tính cá nhân, chuyển về `develop` và xóa nhánh local:
  ```bash
  git checkout develop
  git pull origin develop
  git branch -d feat/ten-tinh-nang
  ```

---

## IV. CÁCH XỬ LÝ KHI BỊ XUNG ĐỘT (MERGE CONFLICT)

Nếu khi tạo PR mà GitHub báo bị Conflict với `develop`:
1. Dưới máy tính của bạn, chuyển về nhánh của bạn:
   ```bash
   git checkout feat/ten-tinh-nang
   ```
2. Kéo code mới nhất từ `develop` vào nhánh của bạn:
   ```bash
   git merge develop
   ```
3. Mở VS Code, tìm các đoạn code bị xung đột (được đánh dấu `<<<<<<< HEAD` và `>>>>>>> develop`), thảo luận với người viết code đoạn đó để chọn đoạn đúng nhất.
4. Lưu file lại, sau đó commit và push:
   ```bash
   git add .
   git commit -m "fix: giai quyet xung dot voi develop"
   git push origin feat/ten-tinh-nang
   ```
   *GitHub sẽ tự động cập nhật lại PR và cho phép Merge.*
