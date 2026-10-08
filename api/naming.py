"""Human-facing route names, themes and descriptions."""
import random

# ============================================================
# ĐẶT TÊN LỘ TRÌNH THEO NỘI DUNG THỰC TẾ (BUG 3 FIX)
# Backend quyết định tên dựa trên loai_hinh chiếm ưu thế trong route,
# KHÔNG dùng index xoay vòng qua danh sách tên cố định.
# ============================================================
# ============================================================
# ĐẶT TÊN LỘ TRÌNH THEO TRẢI NGHIỆM THẬT & CẢM XÚC
# Tên route phản ánh chủ đề, loại hình, nhịp độ và không khí trải nghiệm,
# TUYỆT ĐỐI KHÔNG dùng logic máy móc kiểu "Đa dạng - 9h36", "Theo sở thích - 12h".
# ============================================================
THEME_EXPERIENCE_NAMES = {
    "relax": [
        "Hà Nội chậm rãi & Những khoảng lặng",
        "Cafe, phố cũ và những khoảng nghỉ",
        "Thong thả ngắm phố & Tìm chút bình yên",
        "Nhịp sống êm đềm bên góc phố quen",
    ],
    "food": [
        "Một ngày khám phá Hà Nội qua ẩm thực",
        "Hành trình vị giác & Hương vị phố xưa",
        "Hà Nội đậm đà qua từng góc quán",
        "Hương vị truyền thống & Cà phê phố cổ",
    ],
    "culture": [
        "Dấu ấn văn hóa & Chiều sâu nghìn năm",
        "Hà Nội hoài niệm qua các di sản",
        "Lắng đọng ký ức lịch sử & Di tích xưa",
        "Hành trình di sản & Chiều sâu văn hiến",
    ],
    "time_optimized": [
        "Cung đường tinh gọn & Trải nghiệm liền mạch",
        "Khám phá trọng tâm, tối ưu nhịp điệu",
        "Hành trình kết nối nhanh các điểm đến",
        "Gọn gàng từng bước chân & Tiện lợi tối đa",
    ],
    "exploration": [
        "Góc nhìn mới & Những khám phá bất ngờ",
        "Hà Nội mở rộng qua những góc phố mới",
        "Hành trình vi vu & Khám phá nét độc đáo",
        "Chạm vào những điểm đến ít người biết",
    ],
    "highlight": [
        "Tinh hoa Hà Nội qua những điểm nổi bật",
        "Trọn vẹn các điểm đến được yêu thích nhất",
        "Những tọa độ biểu tượng không thể bỏ lỡ",
        "Hành trình điểm hẹn kinh kỳ",
    ],
    "preference": [
        "Hành trình thiết kế riêng theo gu của bạn",
        "Theo dòng cảm xúc & Không gian yêu thích",
        "Giai điệu bình yên theo đúng sở thích",
        "Hành trình dành riêng cho tâm hồn bạn",
    ],
    "diverse": [
        "Trọn vẹn sắc màu phố thị",
        "Hòa nhịp muôn màu trải nghiệm Hà Nội",
        "Góc nhìn đa chiều & Trải nghiệm phong phú",
        "Một ngày sống trọn chất Hà Thành",
    ],
}

CATEGORY_EXPERIENCE_NAMES = {
    "Cafe": "Cafe, phố cũ và những khoảng nghỉ",
    "Tham quan": "Dấu ấn văn hóa & Chiều sâu di sản",
    "Checkin": "Hà Nội qua những góc check-in",
    "TTTM": "Nhịp sống hiện đại & Mua sắm giải trí",
    "Ăn uống": "Một ngày khám phá ẩm thực phố thị",
}

THEME_EXPERIENCE_DESCRIPTIONS = {
    "relax": "Hành trình nhẹ nhàng, thong thả với ít điểm dừng để bạn dành nhiều thời gian cảm nhận từng nơi và tận hưởng bầu không khí mà không phải vội vã.",
    "food": "Chuyến du hành ẩm thực kết nối những món ngon nức tiếng và quán cafe có gu, thỏa mãn trọn vẹn vị giác của người sành ăn.",
    "culture": "Hành trình giàu chiều sâu cảm xúc qua các không gian di sản, kiến trúc hoài niệm, đưa bạn ngược dòng lịch sử lắng đọng cùng thủ đô.",
    "time_optimized": "Cung đường được tính toán tối ưu quãng đường di chuyển, tiết kiệm tối đa thời gian trên đường để bạn tận hưởng nhiều thời gian tham quan nhất.",
    "exploration": "Dành cho những ai thích đổi gió với cung đường mở rộng, chạm vào những góc phố và điểm hẹn mang nét độc đáo, bất ngờ.",
    "highlight": "Tuyển tập những địa danh tiêu biểu và được yêu thích nhất, hoàn hảo cho một ngày trải nghiệm tinh hoa thành phố.",
    "preference": "Lộ trình được tuyển chọn riêng bám sát mong muốn và tâm trạng của bạn, mang lại trải nghiệm chuẩn gu và đong đầy cảm xúc.",
    "diverse": "Hành trình đa sắc màu kết hợp hài hòa giữa tham quan, thưởng thức ẩm thực và thư giãn, đem lại trải nghiệm phong phú suốt cả ngày.",
}


def generate_route_name(route_points: list, route_index: int = 1, theme: str = "", user_preference: str = "") -> str:
    """
    Sinh tên lộ trình dựa trên trải nghiệm thật, theme, loại hình địa điểm và sở thích.
    Tuyệt đối không dùng tên máy móc.
    """
    idx_offset = max(0, route_index - 1)

    # 1. Nếu có theme và có danh sách tên trải nghiệm cho theme
    if theme in THEME_EXPERIENCE_NAMES:
        options = THEME_EXPERIENCE_NAMES[theme]
        # Nếu có loại hình chiếm ưu thế áp đảo (>60%), ưu tiên tên trải nghiệm của loại hình đó
        if route_points:
            counts = {}
            for p in route_points:
                lh = p.get("loai_hinh") or "Khác"
                counts[lh] = counts.get(lh, 0) + 1
            total = len(route_points)
            dominant, dcount = max(counts.items(), key=lambda kv: kv[1])
            if dcount / total >= 0.6 and dominant in CATEGORY_EXPERIENCE_NAMES and theme in ("diverse", "preference", "exploration"):
                return CATEGORY_EXPERIENCE_NAMES[dominant]

        return options[idx_offset % len(options)]

    # 2. Nếu không có theme, dựa trên cơ cấu loại hình
    if route_points:
        counts = {}
        for p in route_points:
            lh = p.get("loai_hinh") or "Khác"
            counts[lh] = counts.get(lh, 0) + 1
        total = len(route_points)
        dominant, dcount = max(counts.items(), key=lambda kv: kv[1])
        if dcount / total >= 0.6 and dominant in CATEGORY_EXPERIENCE_NAMES:
            return CATEGORY_EXPERIENCE_NAMES[dominant]
        if len(counts) >= 3:
            return "Trọn vẹn sắc màu phố thị"

    return f"Hành trình trải nghiệm {route_index}"


# ============================================================
# BỘ CHỦ ĐỀ LỘ TRÌNH (mục 4)
# ------------------------------------------------------------
# Mỗi route candidate được gắn một `theme`. Theme KHÔNG phải nhãn trang trí:
# nó quyết định scorer nào được dùng, pool candidate nào được cấp, và ngân
# sách thời gian nào được áp — tức là các route thực sự khác nhau về tập
# điểm / thứ tự / tổng thời gian / mức độ di chuyển.
# ============================================================
ROUTE_THEMES = {
    "time_optimized": {"label": "🏃 Tiết kiệm thời gian", "desc": "Đi gần, ít di chuyển, tận dụng tối đa thời gian tham quan."},
    "preference":     {"label": "🧭 Theo sở thích của bạn", "desc": "Bám sát mô tả mong muốn bạn đã nhập."},
    "relax":          {"label": "🌿 Thong thả", "desc": "Ít điểm hơn, mỗi điểm ở lâu hơn, không vội."},
    "food":           {"label": "🍜 Ẩm thực", "desc": "Xoay quanh quán ăn và cà phê."},
    "culture":        {"label": "🏛️ Văn hoá & tham quan", "desc": "Ưu tiên di tích, bảo tàng, điểm check-in."},
    "exploration":    {"label": "🧳 Khám phá", "desc": "Đi xa hơn một chút để gặp những điểm ít người chọn."},
    "diverse":        {"label": "🌈 Đa dạng", "desc": "Mỗi điểm một kiểu trải nghiệm khác nhau."},
    "highlight":      {"label": "⭐ Điểm nổi bật", "desc": "Gom các địa điểm được đánh giá cao nhất."},
}

# Nhóm loại hình dùng cho theme food / culture.
_FOOD_CATEGORIES = {"Ăn uống", "Cafe"}
_CULTURE_CATEGORIES = {"Tham quan", "Checkin"}


def _fmt_minutes(mins) -> str:
    """90 -> '1h30', 45 -> '45 phút'."""
    mins = int(round(mins or 0))
    if mins < 60:
        return f"{mins} phút"
    h, m = divmod(mins, 60)
    return f"{h}h{m:02d}" if m else f"{h}h"


def generate_route_description(route_points: list, theme: str, total_minutes, travel_minutes, user_preference: str = "") -> str:
    """
    Mô tả ngắn, gợi mở trải nghiệm và giúp người dùng hiểu rõ lộ trình này
    khác lộ trình kia ở đâu (nhịp độ, phong cách, thời gian di chuyển).
    """
    visitable = [p for p in route_points if p.get("loai_hinh") != "diem_xuat_phat"]
    counts = {}
    for p in visitable:
        lh = p.get("loai_hinh") or "Khác"
        counts[lh] = counts.get(lh, 0) + 1
    breakdown = ", ".join(f"{n} {lh.lower()}" for lh, n in sorted(counts.items(), key=lambda kv: -kv[1]))

    narrative = THEME_EXPERIENCE_DESCRIPTIONS.get(
        theme,
        "Hành trình kết hợp hài hòa các điểm đến để bạn có trải nghiệm trọn vẹn và thoải mái nhất."
    )

    parts = [f"{len(visitable)} điểm"]
    if breakdown:
        parts.append(breakdown)
    parts.append(f"di chuyển {_fmt_minutes(travel_minutes)}")
    parts.append(f"tổng {_fmt_minutes(total_minutes)}")

    stats = " · ".join(parts)
    return f"{narrative} ({stats})."
