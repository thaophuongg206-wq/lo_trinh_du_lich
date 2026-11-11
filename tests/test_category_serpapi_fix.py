import sys
sys.stdout.reconfigure(encoding='utf-8')

from api.db import fetch_all_points
from api.schemas import OptimizationRequest
from api.generation import run_route_generation
from categories import category_of

def test_serpapi_enrichment():
    points = fetch_all_points()
    print(f"Total POIs in DB: {len(points)}")
    enriched = [p for p in points if p.get("serpapi_reviews")]
    print(f"Enriched with real SerpApi reviews: {len(enriched)} / {len(points)}")
    assert len(enriched) >= 28, f"Expected >= 28 enriched POIs, got {len(enriched)}"
    
    # Check sample review content
    lotte = next(p for p in points if "lotte" in p["ten"].lower())
    assert len(lotte["serpapi_reviews"]) >= 3, "Lotte should have >= 3 real reviews"
    print(f"Sample review for {lotte['ten']}: {lotte['serpapi_reviews'][0][:60]}...")
    print(" [OK] SerpApi enrichment test passed.")

def test_category_quota_constraint():
    req = OptimizationRequest(
        region="Hanoi",
        start_time="08:00",
        end_time="20:00",
        trip_date="2026-10-10",
        start_point="Hồ Hoàn Kiếm",
        vehicle_type="xe_may",
        user_preference="Đi Hà Nội cả ngày, cực kỳ thích cafe, chụp ảnh và ẩm thực phố cổ",
        weight=80
    )
    res = run_route_generation(req, mode="multi")
    routes = res.get("routes", [])
    print(f"\nGenerated {len(routes)} diverse routes:")
    
    for r in routes:
        places = r.get("places", [])
        cafe_count = sum(1 for p in places if category_of(p) == "cafe")
        food_count = sum(1 for p in places if category_of(p) == "an_uong")
        print(f"  - {r['route_id']}: {len(places)} điểm | {cafe_count} quán cafe | {food_count} quán ăn | {r.get('route_name')}")
        assert cafe_count <= 2, f"Lỗi: Lộ trình {r['route_id']} có tận {cafe_count} quán cafe (vượt quá trần 2)!"
        assert food_count <= 2, f"Lỗi: Lộ trình {r['route_id']} có tận {food_count} quán ăn (vượt quá trần 2)!"
    
    print(" [OK] Category quota constraint test passed: Không lộ trình nào bị quá 2 quán cafe!")

if __name__ == "__main__":
    test_serpapi_enrichment()
    test_category_quota_constraint()
    print("\n============================================================")
    print("TẤT CẢ TEST FIX QUOTA CATEGORY & SERPAPI ĐỀU THÀNH CÔNG RỰC RỠ!")
    print("============================================================")
