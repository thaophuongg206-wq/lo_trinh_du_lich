
CREATE DATABASE DuLichThongMinh;
USE DuLichThongMinh;

CREATE TABLE NGUOI_DUNG (
    id INT IDENTITY(1,1) PRIMARY KEY,
    ho_ten NVARCHAR(50) NOT NULL,
    email NVARCHAR(100),
    sdt VARCHAR(20),     
    cccd VARCHAR(20)
);
CREATE TABLE DIA_DIEM (
    id INT IDENTITY(1,1) PRIMARY KEY,
    ten NVARCHAR(255) NOT NULL,
    vi_do FLOAT,
    kinh_do FLOAT,
    loai_hinh NVARCHAR(100),
    diem_gia_tri FLOAT,
    thoi_gian_tham_quan_phut INT,
    mo_ta NVARCHAR(MAX),
    thong_tin_chi_tiet NVARCHAR(MAX),
    review NVARCHAR(MAX),
    phu_hop NVARCHAR(255),
    cap_do_tiep_can INT DEFAULT 3
);
CREATE TABLE CUA_SO_THOI_GIAN (
    id INT IDENTITY(1,1) PRIMARY KEY,
    dia_diem_id INT NOT NULL,
    gio_mo_cua TIME,
    gio_dong_cua TIME,
    ngay_ap_dung NVARCHAR(50),
    CONSTRAINT FK_CuaSo_DiaDiem FOREIGN KEY (dia_diem_id) REFERENCES DIA_DIEM(id)
);
CREATE TABLE MA_TRAN_KHOANG_CACH (
    id INT IDENTITY(1,1) PRIMARY KEY,
    diem_di_id INT NOT NULL,
    diem_den_id INT NOT NULL,
    khoang_cach_km FLOAT,
    thoi_gian_di_chuyen_giay INT,
    phuong_tien NVARCHAR(50),
    thoi_diem_cap_nhat_osrm DATETIME,
    CONSTRAINT FK_MaTran_DiemDi FOREIGN KEY (diem_di_id) REFERENCES DIA_DIEM(id),
    CONSTRAINT FK_MaTran_DiemDen FOREIGN KEY (diem_den_id) REFERENCES DIA_DIEM(id)
);
CREATE TABLE YEU_CAU_CHUYEN_DI (
    id INT IDENTITY(1,1) PRIMARY KEY,
    nguoi_dung_id INT NOT NULL,
    diem_xuat_phat_id INT NOT NULL,
    diem_ket_thuc_id INT NOT NULL,
    thoi_diem_bat_dau DATETIME,
    ngan_sach_thoi_gian_phut FLOAT,
    ngay_tao DATETIME DEFAULT GETDATE(),
    CONSTRAINT FK_YeuCau_NguoiDung FOREIGN KEY (nguoi_dung_id) REFERENCES NGUOI_DUNG(id),
    CONSTRAINT FK_YeuCau_XuatPhat FOREIGN KEY (diem_xuat_phat_id) REFERENCES DIA_DIEM(id),
    CONSTRAINT FK_YeuCau_KetThuc FOREIGN KEY (diem_ket_thuc_id) REFERENCES DIA_DIEM(id)
);
CREATE TABLE LO_TRINH (
    id INT IDENTITY(1,1) PRIMARY KEY,
    yeu_cau_id INT NOT NULL,
    tong_diem_dat_duoc FLOAT,
    tong_thoi_gian_su_dung_giay INT,
    trang_thai NVARCHAR(50),
    ngay_tao DATETIME DEFAULT GETDATE(),
    CONSTRAINT FK_LoTrinh_YeuCau FOREIGN KEY (yeu_cau_id) REFERENCES YEU_CAU_CHUYEN_DI(id)
);
CREATE TABLE DIEM_DUNG_LO_TRINH (
    id INT IDENTITY(1,1) PRIMARY KEY,
    lo_trinh_id INT NOT NULL,
    dia_diem_id INT NOT NULL,
    thu_tu_ghe_tham INT,
    gio_den_du_kien DATETIME,
    gio_bat_dau_tham_quan DATETIME,
    gio_roi_du_kien DATETIME,
    CONSTRAINT FK_DiemDung_LoTrinh FOREIGN KEY (lo_trinh_id) REFERENCES LO_TRINH(id),
    CONSTRAINT FK_DiemDung_DiaDiem FOREIGN KEY (dia_diem_id) REFERENCES DIA_DIEM(id)
);


--Up Dữ Liệu

--Chế độ chèn ID thành công
SET IDENTITY_INSERT DIA_DIEM ON;


INSERT INTO DIA_DIEM (id, ten, loai_hinh, mo_ta, vi_do, kinh_do, diem_gia_tri, thoi_gian_tham_quan_phut)
VALUES
(1, N'Lotte Center Liễu Giai', N'TTTM', N'Giảng Võ, Hà Nội, Việt Nam', 21.0315, 105.8160, 8.8, 120),
(2, N'Vincom Center Bà Triệu', N'TTTM', N'191 P. Bà Triệu, Hai Bà Trưng, Hà Nội, Việt Nam', 21.0110, 105.8503, 8.2, 60),
(3, N'Tràng Tiền Plaza', N'TTTM', N'24 P. Hai Bà Trưng, Cửa Nam, Hà Nội, Việt Nam', 21.0251, 105.8540, 8.5, 180),
(4, N'Vincom Mega Mall Royal City', N'TTTM', N'72A Nguyễn Trãi, Thanh Xuân, Hà Nội, Việt Nam', 21.0022, 105.8156, 8.6, 120),
(5, N'Aeon Mall Long Biên', N'TTTM', N'27 Đ. Cổ Linh, Long Biên, Hà Nội, Việt Nam', 21.0261, 105.9004, 9.0, 60),
(6, N'Phở Thìn Bờ Hồ (Hàng Vôi)', N'Ăn uống', N'19 P. Hàng Vôi, Hoàn Kiếm, Hà Nội, Việt Nam', 21.0304, 105.8572, 8.4, 180),
(7, N'Bún chả Hương Liên (Bún chả Obama)', N'Ăn uống', N'24 P. Lê Văn Hưu, Cửa Nam, Hà Nội, Việt Nam', 21.0181, 105.8543, 8.9, 120),
(8, N'Bánh tôm Hồ Tây (Thanh Niên)', N'Ăn uống', N'Thanh Niên, Tây Hồ, Hà Nội, Việt Nam', 21.0478, 105.8388, 7.8, 60),
(9, N'Vua chả cá', N'Ăn uống', N'269 P. Giảng Võ, Ô Chợ Dừa, Hà Nội, Việt Nam', 21.0282, 105.8239, 8.7, 180),
(10, N'Bún đậu mắm tôm Ngõ Trạm', N'Ăn uống', N'1B Ng. Trạm, Phố cổ Hà Nội, Hoàn Kiếm, Hà Nội', 21.0309, 105.8465, 8.5, 120),
(11, N'Phở cuốn Hương Mai (Ngũ Xã)', N'Ăn uống', N'25 P. Ngũ Xã, Ba Đình, Hà Nội, Việt Nam', 21.0461, 105.8416, 8.6, 60),
(12, N'Nộm Long Vi Dung (Hàng Bạc)', N'Ăn uống', N'P. Hồ Hoàn Kiếm/23 P. Hàng Bạc, Hoàn Kiếm, Hà Nội', 21.0323, 105.8539, 8.1, 180),
(13, N'Bánh mì Trâm', N'Ăn uống', N'30 P. Đình Ngang, Cửa Nam, Hà Nội, Việt Nam', 21.0286, 105.8424, 8.0, 120),
(14, N'Miến lươn Chân Cầm', N'Ăn uống', N'1 P. Chân Cầm, Hoàn Kiếm, Hà Nội, Việt Nam', 21.0306, 105.8503, 8.3, 60),
(15, N'Xôi Yến (Nguyễn Hữu Huân)', N'Ăn uống', N'35b P. Nguyễn Hữu Huân, Hoàn Kiếm, Hà Nội', 21.0339, 105.8550, 7.9, 180),
(16, N'Loading T Cafe (Chân Cầm - Vintage)', N'Cafe', N'8 P. Chân Cầm, Hoàn Kiếm, Hà Nội, Việt Nam', 21.0306, 105.8493, 9.1, 120),
(17, N'All Day Coffee (Quang Trung)', N'Cafe', N'37 P. Quang Trung, Cửa Nam, Hà Nội, Việt Nam', 21.0207, 105.8488, 9.0, 60),
(18, N'Cafe Giảng (Cà phê trứng nổi tiếng)', N'Cafe', N'39 P. Nguyễn Hữu Huân, Hoàn Kiếm, Hà Nội', 21.0336, 105.8547, 9.3, 180),
(19, N'Blackbird Coffee (Chân Cầm)', N'Cafe', N'5 P. Chân Cầm, Hoàn Kiếm, Hà Nội, Việt Nam', 21.0304, 105.8491, 8.8, 120),
(20, N'Cotero Coffee (Tây Hồ)', N'Cafe', N'80 Từ Hoa, Tây Hồ, Hà Nội, Việt Nam', 21.0612, 105.8230, 8.6, 60),
(21, N'La Mensa (Tông Đản)', N'Cafe', N'8 Tông Đản, Hoàn Kiếm, Hà Nội, Việt Nam', 21.0251, 105.8560, 8.4, 180),
(22, N'Ban Công Cafe (Đinh Liệt - Biệt thự cổ)', N'Cafe', N'2 Đinh Liệt, Hoàn Kiếm, Hà Nội, Việt Nam', 21.0321, 105.8520, 8.9, 120),
(23, N'Yên Cafe (Quán Thánh)', N'Cafe', N'182-184, 182 P. Quán Thánh, Ba Đình, Hà Nội', 21.0431, 105.8400, 8.7, 60),
(24, N'Hasu Cafe (Hàng Chuối)', N'Cafe', N'12 Hàng Chuối, Hai Bà Trưng, Hà Nội, Việt Nam', 21.0181, 105.8560, 8.2, 180),
(25, N'Atelier Coffee (Nguyễn Thượng Hiền)', N'Cafe', N'45 Nguyễn Thượng Hiền, Hai Bà Trưng, Hà Nội', 21.0191, 105.8420, 8.5, 120),
(26, N'Đền Ngọc Sơn (Hồ Hoàn Kiếm)', N'Tham quan', N'Đinh Tiên Hoàng, Hoàn Kiếm, Hà Nội, Việt Nam', 21.0309, 105.8526, 9.2, 60),
(27, N'Lăng Chủ tịch Hồ Chí Minh', N'Tham quan', N'1 Hùng Vương, Điện Biên, Ba Đình, Hà Nội', 21.0369, 105.8352, 9.5, 180),
(28, N'Văn Miếu - Quốc Tử Giám', N'Tham quan', N'58 Quốc Tử Giám, Văn Miếu, Đống Đa, Hà Nội', 21.0282, 105.8359, 9.4, 120),
(29, N'Chùa Trấn Quốc (Hồ Tây)', N'Tham quan', N'Thanh Niên, Tây Hồ, Hà Nội, Việt Nam', 21.0480, 105.8373, 9.1, 60),
(30, N'Nhà thờ Lớn Hà Nội', N'Checkin', N'1 P. Nhà Thờ, Hoàn Kiếm, Hà Nội, Việt Nam', 21.0287, 105.8493, 9.3, 180);

SET IDENTITY_INSERT DIA_DIEM OFF;

INSERT INTO CUA_SO_THOI_GIAN (dia_diem_id, gio_mo_cua, gio_dong_cua, ngay_ap_dung)
VALUES
(1, '08:00', '21:00', N'Tất cả các ngày'),
(2, '07:00', '20:00', N'Tất cả các ngày'),
(3, '09:00', '22:00', N'Tất cả các ngày'),
(4, '08:00', '21:00', N'Tất cả các ngày'),
(5, '07:00', '20:00', N'Tất cả các ngày'),
(6, '09:00', '22:00', N'Tất cả các ngày'),
(7, '08:00', '21:00', N'Tất cả các ngày'),
(8, '07:00', '20:00', N'Tất cả các ngày'),
(9, '09:00', '22:00', N'Tất cả các ngày'),
(10, '08:00', '21:00', N'Tất cả các ngày'),
(11, '07:00', '20:00', N'Tất cả các ngày'),
(12, '09:00', '22:00', N'Tất cả các ngày'),
(13, '08:00', '21:00', N'Tất cả các ngày'),
(14, '07:00', '20:00', N'Tất cả các ngày'),
(15, '09:00', '22:00', N'Tất cả các ngày'),
(16, '08:00', '21:00', N'Tất cả các ngày'),
(17, '07:00', '20:00', N'Tất cả các ngày'),
(18, '09:00', '22:00', N'Tất cả các ngày'),
(19, '08:00', '21:00', N'Tất cả các ngày'),
(20, '07:00', '20:00', N'Tất cả các ngày'),
(21, '09:00', '22:00', N'Tất cả các ngày'),
(22, '08:00', '21:00', N'Tất cả các ngày'),
(23, '07:00', '20:00', N'Tất cả các ngày'),
(24, '09:00', '22:00', N'Tất cả các ngày'),
(25, '08:00', '21:00', N'Tất cả các ngày'),
(26, '07:00', '20:00', N'Tất cả các ngày'),
(27, '09:00', '22:00', N'Tất cả các ngày'),
(28, '08:00', '21:00', N'Tất cả các ngày'),
(29, '07:00', '20:00', N'Tất cả các ngày'),
(30, '09:00', '22:00', N'Tất cả các ngày');

--8/12/2026
-- Update điểm giá giá trị
UPDATE DIA_DIEM SET diem_gia_tri = 4.3 WHERE id = 1;  -- Lotte Center Liễu Giai
UPDATE DIA_DIEM SET diem_gia_tri = 4.4 WHERE id = 2;  -- Vincom Center Bà Triệu
UPDATE DIA_DIEM SET diem_gia_tri = 4.4 WHERE id = 3;  -- Tràng Tiền Plaza
UPDATE DIA_DIEM SET diem_gia_tri = 4.5 WHERE id = 4;  -- Vincom Mega Mall Royal City
UPDATE DIA_DIEM SET diem_gia_tri = 4.6 WHERE id = 5;  -- Aeon Mall Long Biên
UPDATE DIA_DIEM SET diem_gia_tri = 4.2 WHERE id = 6;  -- Phở Thìn Bờ Hồ
UPDATE DIA_DIEM SET diem_gia_tri = 4.1 WHERE id = 7;  -- Bún chả Hương Liên
UPDATE DIA_DIEM SET diem_gia_tri = 2.3 WHERE id = 8;  -- Bánh tôm Hồ Tây
UPDATE DIA_DIEM SET diem_gia_tri = 4.5 WHERE id = 9;  -- Vua chả cá
UPDATE DIA_DIEM SET diem_gia_tri = 3.3 WHERE id = 10; -- Bún đậu mắm tôm Ngõ Trạm
UPDATE DIA_DIEM SET diem_gia_tri = 4.0 WHERE id = 11; -- Phở cuốn Hương Mai
UPDATE DIA_DIEM SET diem_gia_tri = 4.0 WHERE id = 12; -- Nộm Long Vi Dũng
UPDATE DIA_DIEM SET diem_gia_tri = 4.3 WHERE id = 13; -- Bánh mì Trâm
UPDATE DIA_DIEM SET diem_gia_tri = 4.0 WHERE id = 14; -- Miến lươn Chân Cầm
UPDATE DIA_DIEM SET diem_gia_tri = 4.0 WHERE id = 15; -- Xôi Yến
UPDATE DIA_DIEM SET diem_gia_tri = 4.7 WHERE id = 16; -- Loading T Cafe
UPDATE DIA_DIEM SET diem_gia_tri = 4.7 WHERE id = 17; -- All Day Coffee
UPDATE DIA_DIEM SET diem_gia_tri = 4.4 WHERE id = 18; -- Cafe Giảng
UPDATE DIA_DIEM SET diem_gia_tri = 4.6 WHERE id = 19; -- Blackbird Coffee
UPDATE DIA_DIEM SET diem_gia_tri = 4.6 WHERE id = 20; -- Cotero Coffee
UPDATE DIA_DIEM SET diem_gia_tri = 4.1 WHERE id = 21; -- La Mensa
UPDATE DIA_DIEM SET diem_gia_tri = 4.8 WHERE id = 22; -- Ban Công Cafe
UPDATE DIA_DIEM SET diem_gia_tri = 4.8 WHERE id = 23; -- Yên Cafe
UPDATE DIA_DIEM SET diem_gia_tri = 4.4 WHERE id = 24; -- Hasu Cafe
UPDATE DIA_DIEM SET diem_gia_tri = 5.0 WHERE id = 25; -- Atelier Coffee
UPDATE DIA_DIEM SET diem_gia_tri = 4.4 WHERE id = 26; -- Đền Ngọc Sơn
UPDATE DIA_DIEM SET diem_gia_tri = 4.5 WHERE id = 27; -- Lăng Chủ tịch Hồ Chí Minh
UPDATE DIA_DIEM SET diem_gia_tri = 4.6 WHERE id = 28; -- Văn Miếu - Quốc Tử Giám
UPDATE DIA_DIEM SET diem_gia_tri = 4.4 WHERE id = 29; -- Chùa Trấn Quốc
UPDATE DIA_DIEM SET diem_gia_tri = 4.5 WHERE id = 30; -- Nhà thờ Lớn Hà Nội

-- Thêm 3 cột


UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- TTTM cao cấp 6 tầng kết hợp đài quan sát Sky Walk, siêu thị Lotte Mart, rạp phim & ẩm thực cao cấp. Không gian hiện đại, sang trọng.',
    phu_hop = N'Mua sắm hàng hiệu, hẹn hò, vui chơi gia đình cuối tuần.',
    review = N'Không gian sang trọng, dịch vụ chuyên nghiệp, đài quan sát view đẹp. Giá cả thuộc phân khúc cao.'
WHERE id = 1;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Tổ hợp mua sắm 3 tòa tháp, tích hợp rạp CGV, chuỗi thời trang phổ thông (Zara, Mango...) & khu ẩm thực sầm uất nội đô.',
    phu_hop = N'Mua sắm thời trang, xem phim, ăn uống, gặp gỡ bạn bè.',
    review = N'Vị trí trung tâm tiện di chuyển, thương hiệu đa dạng. Hầm gửi xe đông và việc di chuyển giữa các tháp hơi tốn thời gian.'
WHERE id = 2;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- TTTM hạng sang phong cách Pháp cổ điển cạnh Hồ Gươm. Tập trung các gian hàng xa xỉ thế giới (Louis Vuitton, Dior...) & rạp CGV.',
    phu_hop = N'Mua sắm đồ hiệu cao cấp, dạo phố cổ, check-in kiến trúc.',
    review = N'Kiến trúc lộng lẫy, không gian yên tĩnh và đẳng cấp. Chủ yếu dành cho khách mua sắm phân khúc cao hoặc chụp ảnh.'
WHERE id = 3;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- TTTM ngầm quy mô lớn nhất Hà Nội. Tích hợp sân trượt băng trong nhà, khu Heroworld, rạp phim & phố ẩm thực khép kín.',
    phu_hop = N'Vui chơi giải trí nhóm đông/gia đình cả ngày, trượt băng.',
    review = N'Đa dạng dịch vụ giải trí và ăn uống. Diện tích quá rộng nên rất dễ bị lạc đường và mất thời gian tìm xe dưới hầm.'
WHERE id = 4;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- TTTM phong cách Nhật Bản diện tích lớn, gồm siêu thị thực phẩm, làng ẩm thực Nhật-Việt, rạp CGV & bãi đỗ xe miễn phí.',
    phu_hop = N'Mua sắm gia đình cuối tuần, trải nghiệm ẩm thực, dã ngoại mua sắm.',
    review = N'Dịch vụ chuẩn Nhật rất chu đáo, tiện nghi sạch sẽ, bãi xe rộng miễn phí. Ngày lễ/cuối tuần thường cực kỳ đông.'
WHERE id = 5;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Mô hình quán ăn bình dân gia truyền từ 1954. Phục vụ phở bò nước dùng thanh trong. Diện tích chật hẹp đặc trưng phố cổ.',
    phu_hop = N'Ăn sáng/tối nhanh, trải nghiệm ẩm thực Hà Nội xưa.',
    review = N'Nước dùng thanh ngọt chuẩn vị truyền thống, thịt bò tươi ngon. Chỗ ngồi hẹp, giờ cao điểm phải xếp hàng và ngồi ghép.'
WHERE id = 6;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Mô hình quán ăn 3 tầng có điều hòa. Phục vụ bún chả nướng than & nem hải sản theo quy trình dây chuyền nhanh chóng.',
    phu_hop = N'Du lịch trải nghiệm địa điểm nổi tiếng, ăn trưa văn phòng.',
    review = N'Bún chả đậm đà, suất ăn đầy đặn. Đông khách du lịch nên không gian khá ồn ào, chất lượng phục vụ ở mức trung bình.'
WHERE id = 7;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Nhà hàng ẩm thực diện tích rộng ven Hồ Trúc Bạch & Hồ Tây. Phục vụ bánh tôm chiên giòn & đặc sản Hà Nội trong không gian mở.',
    phu_hop = N'Tụ tập gia đình, đón tiếp khách du lịch, ăn uống ngắm cảnh hồ.',
    review = N'Vị trí đẹp, không gian thoáng mát nhìn ra hồ. Chất lượng món ăn và thái độ phục vụ chưa thực sự đồng đều.'
WHERE id = 8;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Chuỗi nhà hàng hiện đại trang bị hệ thống hút mùi tại bàn. Phục vụ chả cá lăng chiên chảo nóng ăn kèm bún, thì là, mắm tôm.',
    phu_hop = N'Tiếp đối tác, họp mặt gia đình, ăn uống nhóm đông lịch sự.',
    review = N'Chả cá tươi ngon, mắm tôm vừa vị, không gian không bị ám mùi mỡ. Mức giá thuộc phân khúc tầm trung - khá.'
WHERE id = 9;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Quán ăn bình dân nằm trong ngõ nhỏ phố cổ. Phục vụ mẹt bún đậu chiên giòn, chả cốm, thịt chân giò luộc & giả cầy.',
    phu_hop = N'Ăn trưa nhanh, trải nghiệm ẩm thực ngõ ngách Hà Nội.',
    review = N'Đậu rán giòn nóng hổi, mắm tôm thơm đậm đà. Quán nhỏ nằm trong ngõ nên chỗ ngồi khá chật chội vào giờ trưa.'
WHERE id = 10;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Quán ăn bình dân 2–3 tầng sạch sẽ tại làng ẩm thực Ngũ Xã. Phục vụ phở cuốn bò xào, phở chiên phồng & phở chiên trứng.',
    phu_hop = N'Tụ tập bạn bè, ăn trưa/tối, đổi vị món ăn nhẹ nhàng.',
    review = N'Phở cuốn tươi ngon, phở chiên phồng sốt bò đậm đà. Phục vụ nhanh nhẹn dù quán thường xuyên đông khách.'
WHERE id = 11;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Quán ăn vặt vỉa hè phố cổ (ngồi ghế nhựa). Phục vụ nộm bò khô, bánh bột lọc, nem chua bọc sương & chim quay.',
    phu_hop = N'Ăn vặt chiều/tối, dạo phố Bờ Hồ, trải nghiệm văn hóa vỉa hè.',
    review = N'Nộm đậm đà, topping bò khô phong phú, nước trộn vừa vị. Trải nghiệm ngồi vỉa hè ngắm phố xá rất thú vị.'
WHERE id = 12;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Tiệm bánh mì bình dân không gian chật hẹp/vỉa hè. Phục vụ bánh mì sốt vang bò gân mềm, nước sốt sánh đặc đậm vị.',
    phu_hop = N'Ăn sáng/trưa/tối nhanh, ăn ấm bụng ngày lạnh.',
    review = N'Sốt vang sánh đặc thơm mùi ngũ vị, thịt bò mềm dẻo. Chỗ ngồi chật và giá nhỉnh hơn mặt bằng chung bánh mì.'
WHERE id = 13;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Quán ăn gia đình nhỏ thuộc danh sách Michelin Selected. Phục vụ miến lươn nước/trộn (lươn chiên giòn hoặc lươn mềm).',
    phu_hop = N'Ăn sáng/trưa/tối thanh nhẹ, trải nghiệm ẩm thực chuẩn Michelin.',
    review = N'Lươn giòn không bị hôi, nước hầm ngọt thanh từ xương lươn. Không gian diện tích nhỏ nhưng sạch sẽ.'
WHERE id = 14;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Quán xôi quy mô 3 tầng, phục vụ theo mô hình công nghiệp nhanh chóng. Menu gồm xôi xéo/ngô ăn kèm đa dạng topping (thịt kho, pate, trứng...).',
    phu_hop = N'Ăn nhanh no lâu, ăn sáng, ăn đêm muộn.',
    review = N'Xôi dẻo thơm, topping đậm đà phong phú, phục vụ cực nhanh. Mức giá khá cao so với mặt bằng xôi thông thường.'
WHERE id = 15;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Không gian yên tĩnh, ít người, mang nét kiến trúc Pháp cổ điển, quán rất chill.',
    phu_hop = N'Làm việc hoặc trò chuyện tâm tình.',
    review = N'Không gian biệt thự Pháp cổ vintage, yên tĩnh. Cà phê quế và cà phê trứng ngon (hơi ngọt). Giá nhỉnh hơn mặt bằng chung, thích hợp làm việc hoặc tâm sự.'
WHERE id = 16;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Không gian ấm cúng, yên tĩnh và ít người.',
    phu_hop = N'Tập trung làm việc hoặc có những buổi hẹn hò nhẹ nhàng.',
    review = N'Không gian ấm cúng, lý tưởng để làm việc hay hẹn hò. Nhân viên cực kỳ chu đáo. Menu ngon, nổi bật là món Cold Brew Sấu thanh mát.'
WHERE id = 17;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Quán cà phê truyền thống nổi tiếng, không gian sôi động và đông đúc.',
    phu_hop = N'Trải nghiệm văn hóa phố cổ.',
    review = N'Quán lâu đời, nổi tiếng nhất với cà phê trứng béo ngậy không tanh. Không gian đậm chất Hà Nội xưa nhưng hơi chật chội và đông đúc. Phục vụ nhanh.'
WHERE id = 18;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Không gian ấm cúng, có khu vực vỉa hè ngồi rất chill dù diện tích hơi nhỏ.',
    phu_hop = N'Ngắm view phố cổ nhộn nhịp.',
    review = N'Quán ấm cúng, ngồi vỉa hè ngắm phố cực chill, nhạc hay, bạc sỉu ngon. Nhược điểm là bên trong nhỏ, ồn ào lúc đông khách và giá hơi cao.'
WHERE id = 19;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Không gian yên tĩnh, ít người, có độ riêng tư cao.',
    phu_hop = N'Tập trung làm việc, học tập với cà phê pha máy chuyên sâu.',
    review = N'Cà phê pha máy cực ngon. Không gian yên tĩnh, riêng tư, rất lý tưởng để tập trung làm việc hay học bài. Nhân viên thân thiện, vỉa hè thoáng mát.'
WHERE id = 20;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Không gian sôi động, nhộn nhịp và đông đúc. Có view đẹp ngắm phố.',
    phu_hop = N'Check-in, vui chơi và dạo phố.',
    review = N'Trà Ô Long đậm vị, không gian phố cổ chill. Tuy nhiên, thời gian lên đồ siêu lâu, nhân viên đôi lúc thiếu nhiệt tình và khâu dọn vệ sinh bàn chưa tốt.'
WHERE id = 21;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Không gian mang nét cổ điển, hoài niệm với view ban công ngắm phố cực đẹp, khá yên tĩnh.',
    phu_hop = N'Tâm sự nhẹ nhàng.',
    review = N'Vibe biệt thự Pháp cổ xinh xắn, có ban công ngắm phố rất tuyệt. Nhân viên nhiệt tình. Điểm trừ là chất lượng món ăn chưa đồng đều, có tính VAT.'
WHERE id = 22;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Nằm trong ngõ nhỏ yên tĩnh, ít người. Không gian ngoài trời thoáng đãng, mang lại cảm giác vô cùng chill.',
    phu_hop = N'Thư giãn, tìm không gian yên bình.',
    review = N'Nằm trong ngõ nhỏ, thiết kế mở thoáng đãng, yên tĩnh. Đồ uống chuẩn vị, nhân viên khéo léo chu đáo. Quán hay đông khách và giá nước hơi cao.'
WHERE id = 23;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Không gian yên tĩnh, thanh lịch.',
    phu_hop = N'Cắm cúi làm việc hoặc có những cuộc trò chuyện riêng tư.',
    review = N'Không gian đẹp, yên tĩnh, dễ trò chuyện hay làm việc. Menu đồ uống ít nhưng cà phê rất đậm vị. Đồ ăn nhẹ và dịch vụ đều được đánh giá cao.'
WHERE id = 24;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Không gian rộng rãi, yên tĩnh với thiết kế tối giản, hiện đại.',
    phu_hop = N'Làm không gian làm việc trong ngày, bàn công việc.',
    review = N'Quán rộng rãi, yên tĩnh, gần văn phòng nên thích hợp bàn công việc. Nhân viên và chủ quán dễ mến. Cà phê pha chế ở mức ổn, tròn vai.'
WHERE id = 25;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Di tích lịch sử và tâm linh nổi tiếng. Không gian ngoài trời thoáng đãng, nhiều cây xanh.',
    phu_hop = N'Tham quan, ngắm cảnh Hồ Gươm.',
    review = N'Di tích biểu tượng giữa Hồ Gươm, không gian xanh mát. Cầu Thê Húc, Tháp Bút là điểm check-in không thể bỏ lỡ. Vé vào cửa 50k, rất đáng trải nghiệm.'
WHERE id = 26;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Địa điểm lịch sử trang nghiêm, thiêng liêng. Không gian ngoài trời vô cùng rộng lớn, quy củ và nhiều cây xanh mát.',
    phu_hop = N'Tham quan lịch sử, viếng thăm.',
    review = N'Biểu tượng thiêng liêng tại Quảng trường Ba Đình. Khuôn viên quy củ, rộng lớn. Cần đi sớm, mặc lịch sự và tuân thủ xếp hàng để vào viếng bên trong.'
WHERE id = 27;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Di tích lịch sử, không gian văn hóa giáo dục truyền thống. Khuôn viên ngoài trời cổ kính, rộng rãi, nhiều cây xanh.',
    phu_hop = N'Tìm hiểu văn hóa, giáo dục và thư giãn.',
    review = N'Trường đại học đầu tiên với kiến trúc cổ kính, thư thái. Thường rất đông khách du lịch, khâu mua vé có lúc chậm. Có nhiều sự kiện văn hoá hay.'
WHERE id = 28;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Địa điểm tâm linh cổ kính, linh thiêng bậc nhất. Sở hữu view nhìn ra mặt nước Hồ Tây yên bình và tĩnh lặng.',
    phu_hop = N'Đi lễ, vãn cảnh và tìm sự thanh tịnh.',
    review = N'Ngôi chùa cổ linh thiêng bên Hồ Tây với cảnh quan bình yên, kiến trúc bảo tháp độc đáo. Khách đến viếng cần ăn mặc kín đáo, lịch sự.'
WHERE id = 29;

UPDATE DIA_DIEM SET 
    thong_tin_chi_tiet = N'- Biểu tượng kiến trúc mang phong cách Gothic. Không gian ngoài trời sôi động, nhộn nhịp.',
    phu_hop = N'Check-in chụp ảnh, dạo phố, tụ tập bạn bè.',
    review = N'Biểu tượng kiến trúc uy nghiêm giữa lòng thủ đô. Xung quanh nhộn nhịp trà chanh, quán xá. Buổi tối lên đèn cực kỳ lộng lẫy và sôi động.'
WHERE id = 30;

-- Cập nhật Cấp độ tiếp cận phương tiện (cap_do_tiep_can)
-- Cấp 1: Xe lớn (16/29/45 chỗ), Ô tô, Xe máy (có bãi đỗ xe lớn, đường rộng)
-- Cấp 2: Ô tô, Xe máy (đường chính, không đỗ được xe lớn)
-- Cấp 3: Chỉ xe máy, xe đạp, đi bộ (ngõ/hẻm nhỏ, phố cổ chật hẹp)
IF NOT EXISTS (SELECT * FROM sys.columns WHERE object_id = OBJECT_ID('DIA_DIEM') AND name = 'cap_do_tiep_can')
BEGIN
    ALTER TABLE DIA_DIEM ADD cap_do_tiep_can INT DEFAULT 3;
END;

-- Cấp 1 (Xe lớn được phép):
UPDATE DIA_DIEM SET cap_do_tiep_can = 1 WHERE id IN (1, 2, 3, 4, 5, 27, 28, 29);

-- Cấp 2 (Ô tô cá nhân):
UPDATE DIA_DIEM SET cap_do_tiep_can = 2 WHERE id IN (6, 7, 8, 9, 11, 17, 20, 21, 23, 24, 25, 26, 30);

-- Cấp 3 (Chỉ xe máy, xe đạp, đi bộ):
UPDATE DIA_DIEM SET cap_do_tiep_can = 3 WHERE id IN (10, 12, 13, 14, 15, 16, 18, 19, 22);
