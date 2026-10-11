import sys
from api.schemas import OptimizationRequest
from api.generation import run_route_generation

print("=== SCENARIO 1: Xem phim + di tích lịch sử ===")
req1 = OptimizationRequest(
    region="Hanoi",
    start_point="Hồ Hoàn Kiếm",
    vehicle_type="xe_may",
    start_time="08:00",
    end_time="20:00",
    user_preference="dị xem phim, khám phá di tích lịch sử",
    trip_date="2026-10-11"
)
res1 = run_route_generation(req1, mode="multi")
assert res1.get("status") == "success"
for i, r in enumerate(res1["routes"]):
    print(f"Route {i+1}: {r['name']}")
    print(f"   Mô tả: {r['description']}")
    places = [p["ten"] for p in r["places"]]
    print(f"   Điểm: {places}")
    # Verify has cinema
    has_cinema = any("CGV" in p or "Cinema" in p or "chiếu phim" in p.lower() or "Vincom" in p or "Tràng Tiền" in p for p in places)
    # Verify has historical monument
    has_cult = any("Đền" in p or "Văn Miếu" in p or "Lăng" in p or "Chùa" in p or "Nhà tù" in p for p in places)
    assert has_cinema, f"Route {i+1} lacks cinema: {places}"
    assert has_cult, f"Route {i+1} lacks monument: {places}"
    print("   -> OK: Có cả rạp phim và di tích lịch sử!")

print("\n=== SCENARIO 2: Xem phim + cafe (tuyệt đối không ẩm thực hay di tích ngoài GPS) ===")
req2 = OptimizationRequest(
    region="Hanoi",
    start_lat=21.0285,
    start_lon=105.8542,
    vehicle_type="xe_may",
    start_time="08:00",
    end_time="20:00",
    user_preference="tôi muốn đi xem phim cf",
    trip_date="2026-10-11"
)
res2 = run_route_generation(req2, mode="multi")
assert res2.get("status") == "success"
for i, r in enumerate(res2["routes"]):
    print(f"Route {i+1}: {r['name']}")
    print(f"   Mô tả: {r['description']}")
    visitable = [p for p in r["places"] if p["id"] != "gps_current"]
    places = [p["ten"] for p in visitable]
    types = [p.get("loai_hinh") for p in visitable]
    print(f"   Điểm: {places}")
    print(f"   Loại hình: {types}")
    # Check no food (an_uong) and no tham_quan
    assert "Ăn uống" not in types, f"Route {i+1} contains food: {types}"
    assert "Tham quan" not in types, f"Route {i+1} contains monuments: {types}"
    assert types.count("TTTM") == 1, f"Route {i+1} does not have exactly 1 cinema: {types}"
    assert types.count("Cafe") == 1, f"Route {i+1} does not have exactly 1 cafe: {types}"
    assert len(visitable) == 2, f"Route {i+1} does not have exactly 2 stops: {places}"
    print(f"   -> OK: ĐÚNG 1 rạp phim và ĐÚNG 1 quán cafe ({len(visitable)} điểm)!")

print("\n=== SCENARIO 3: Giả sử AI gợi ý 2 rạp phim (id 2 và id 31) -> Động cơ vẫn ép tối đa 1 rạp / lộ trình ===")
req3 = OptimizationRequest(
    region="Hanoi",
    start_lat=21.0285,
    start_lon=105.8542,
    vehicle_type="xe_may",
    start_time="08:00",
    end_time="20:00",
    user_preference="tôi muốn đi xem phim cf",
    ai_selected_ids=["2", "31"],
    trip_date="2026-10-11"
)
res3 = run_route_generation(req3, mode="multi")
assert res3.get("status") == "success"
for i, r in enumerate(res3["routes"]):
    visitable = [p for p in r["places"] if p["id"] != "gps_current"]
    types = [p.get("loai_hinh") for p in visitable]
    tttm_count = sum(1 for t in types if t == "TTTM")
    print(f"Route {i+1}: {r['name']} -> {tttm_count} rạp phim. Điểm: {[p['ten'] for p in visitable]}")
    assert tttm_count <= 1, f"Route {i+1} vi phạm: có {tttm_count} rạp phim!"
print("   -> OK: Tuyệt đối không lộ trình nào chứa 2 rạp phim!")

print("\nTẤT CẢ SCENARIO KIỂM THỬ THÀNH CÔNG RỰC RỠ!")
