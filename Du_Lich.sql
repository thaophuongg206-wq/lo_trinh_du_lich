
CREATE DATABASE DuLichThongMinh;
USE DuLichThongMinh;

-- Tạo Bảng

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
    mo_ta NVARCHAR(MAX)
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
SET IDENTITY_INSERT DIA_DIEM  ON;

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


-- Xóa dữ liệu

-- 1
EXEC sp_Xoa 'ALTER TABLE ? NOCHECK CONSTRAINT ALL';
-- 2
EXEC sp_Xoa 'TRUNCATE TABLE ?';
-- 3
EXEC sp_Xoa 'ALTER TABLE ? CHECK CONSTRAINT ALL';