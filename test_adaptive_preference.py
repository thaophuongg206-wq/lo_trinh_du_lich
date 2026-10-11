import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
from datetime import datetime
from api.schemas import OptimizationRequest
from api.generation import run_route_generation

def run_tests():
    print("=== TEST 1: Xem phim + cafe (10 tiếng: 08:00 - 18:00) ===")
    req1 = OptimizationRequest(
        region="Hanoi",
        start_time="08:00",
        end_time="18:00",
        start_lat=21.0285,
        start_lon=105.8542,
        user_preference="tôi muốn đi xem phim cf",
        weight=80
    )
    res1 = run_route_generation(req1)
    assert res1["status"] == "success", "res1 status failed"
    assert len(res1["routes"]) >= 3, "res1 routes count < 3"

    for r in res1["routes"]:
        places = r["places"]
        place_cats = [p["loai_hinh"] for p in places if p["id"] != "gps_current"]
        print(f"  Route [{r['theme']}]: {r['name']} ({r['total_duration']} ph)")
        print(f"    Categories: {place_cats}")
        # Assert NO food
        assert "Ăn uống" not in place_cats, f"Route contains unwanted food: {place_cats}"
        # Assert NO culture/temples
        assert "Tham quan" not in place_cats, f"Route contains unwanted sightseeing: {place_cats}"
        # Assert only TTTM / Cafe
        for c in place_cats:
            assert c in ("TTTM", "Cafe"), f"Unexpected category {c}"
        # Assert relaxed visit duration
        for p in places:
            if p["loai_hinh"] == "TTTM":
                assert p["visit_time"] >= 120, f"TTTM visit time not relaxed: {p['visit_time']}"
            elif p["loai_hinh"] == "Cafe":
                assert p["visit_time"] >= 90, f"Cafe visit time not relaxed: {p['visit_time']}"

    print("  -> TEST 1 PASSED: 100% chi gom xem phim (TTTM) va Cafe, khong loi am thuc hay di tich!\n")

    print("=== TEST 2: An uong + cafe (10:00 - 16:00) ===")
    req2 = OptimizationRequest(
        region="Hanoi",
        start_time="10:00",
        end_time="16:00",
        start_lat=21.0285,
        start_lon=105.8542,
        user_preference="muốn đi ăn uống cafe",
        weight=80
    )
    res2 = run_route_generation(req2)
    assert res2["status"] == "success"
    has_food_any = False
    has_cafe_any = False
    for r in res2["routes"]:
        place_cats = [p["loai_hinh"] for p in r["places"] if p["id"] != "gps_current"]
        if "Ăn uống" in place_cats:
            has_food_any = True
        if "Cafe" in place_cats:
            has_cafe_any = True
    assert has_food_any, "Expected food to be included for 'an uong cafe'"
    assert has_cafe_any, "Expected cafe to be included for 'an uong cafe'"
    print("  -> TEST 2 PASSED: Am thuc va cafe deu xuat hien dung nhu cau!\n")

    print("=== TEST 3: Tour du lich chung (khong nhap so thich cu the) ===")
    req3 = OptimizationRequest(
        region="Hanoi",
        start_time="08:00",
        end_time="20:00",
        start_lat=21.0285,
        start_lon=105.8542,
        user_preference="",
        weight=50
    )
    res3 = run_route_generation(req3)
    assert res3["status"] == "success"
    assert len(res3["routes"]) >= 3
    print("  -> TEST 3 PASSED: Tour tong the van sinh 5 lo trinh phong phu da dang!\n")

    print("============================================================")
    print("TAT CA TEST ADAPTIVE PREFERENCE DA PASS HOAN HAO!")
    print("============================================================")

if __name__ == "__main__":
    run_tests()
