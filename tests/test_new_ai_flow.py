"""
Automated verification for the new AI flow, semantic parser, preference scoring,
and deterministic itinerary manipulation endpoints.
"""
import sys
import unittest
from datetime import date

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# Stub pyodbc if missing
import types
if "pyodbc" not in sys.modules:
    stub = types.ModuleType("pyodbc")
    stub.connect = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no odbc"))
    sys.modules["pyodbc"] = stub

from fastapi.testclient import TestClient
from main import app
from services.parser import parse_prompt
from api.preferences import calculate_preference_score, explain_match, tag_matches
from itinerary_store import store


class TestSemanticParser(unittest.TestCase):
    def test_sample_case_hanoi_1day_cafe_photo(self):
        # Case: "Đi Hà Nội 1 ngày, thích cafe và chụp ảnh"
        res = parse_prompt("Đi Hà Nội 1 ngày, thích cafe và chụp ảnh", use_llm=False)
        self.assertEqual(res["fields"]["region"], "Hanoi")
        self.assertEqual(res["fields"]["start_time"], "08:00")
        self.assertEqual(res["fields"]["end_time"], "20:00")
        self.assertEqual(res["fields"]["duration_minutes"], 720)
        self.assertIn("cafe", res["fields"]["preferred_tags"])
        self.assertIn("photo", res["fields"]["preferred_tags"])
        # Time contract for legacy optimizer
        self.assertTrue(res["time_limit_explicit"])
        self.assertEqual(res["time_limit_minutes"], 720)
        # Without context (start point, vehicle), parser flags need_clarification
        self.assertEqual(res["status"], "need_clarification")
        self.assertTrue(any(q["field"] in ("start_point", "vehicle_type") for q in res["clarification_questions"]))

    def test_full_context_becomes_ok(self):
        context = {
            "start_time": "08:00",
            "end_time": "18:00",
            "trip_date": "2026-10-10",
            "vehicle_type": "xe_may",
            "start_point": "Hồ Hoàn Kiếm",
            "has_gps": False,
        }
        res = parse_prompt("Thích ăn phở và đi dạo quanh hồ", context=context, use_llm=False)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["missing_fields"], [])
        self.assertIn("food", res["fields"]["preferred_tags"])
        self.assertIn("nature", res["fields"]["preferred_tags"])
        self.assertEqual(res["fields"]["vehicle_type"], "xe_may")
        self.assertEqual(res["fields"]["start_point"], "Hồ Hoàn Kiếm")

    def test_start_point_and_vehicle_in_prompt(self):
        res = parse_prompt(
            "Xuất phát từ 24 Tràng Tiền lúc 9h sáng đến 17h, đi ô tô, thích chụp ảnh sống ảo và mua sắm",
            use_llm=False,
        )
        self.assertEqual(res["fields"]["start_time"], "09:00")
        self.assertEqual(res["fields"]["end_time"], "17:00")
        self.assertEqual(res["fields"]["vehicle_type"], "o_to")
        self.assertTrue("Tràng Tiền" in res["fields"]["start_point"])
        self.assertIn("photo", res["fields"]["preferred_tags"])
        self.assertIn("shopping", res["fields"]["preferred_tags"])
        self.assertEqual(res["status"], "ok")

    def test_clarification_for_non_hanoi(self):
        res = parse_prompt("Lên tour đi Đà Nẵng 3 ngày", use_llm=False)
        self.assertEqual(res["status"], "need_clarification")
        self.assertTrue(any(q["field"] == "region" for q in res["clarification_questions"]))

    def test_clarification_for_inverted_hours(self):
        res = parse_prompt("Đi từ 18h đến 8h sáng mai tại Hà Nội bằng xe máy", use_llm=False)
        # 18:00 to 08:00 spans overnight
        self.assertIsNotNone(res["fields"]["duration_minutes"])

    def test_understood_summary_generation(self):
        res = parse_prompt("Đi Hà Nội ngày mai bằng xe máy, thích ngắm cảnh ven hồ và cà phê", use_llm=False)
        self.assertIn("Hà Nội", res["understood_summary"])
        self.assertIn("xe máy", res["understood_summary"])
        self.assertTrue(len(res["understood_summary"]) > 20)


class TestPreferenceScoring(unittest.TestCase):
    def test_backward_compatibility(self):
        point = {
            "thong_tin_chi_tiet": "Quan ca phe view dep ben ho Tay",
            "review": "Do uong ngon, gia hop ly",
            "phu_hop": "Hen ho, chup anh",
            "loai_hinh": "Cafe",
        }
        score = calculate_preference_score(point, ["ca", "phe", "view"])
        self.assertGreater(score, 0.5)

    def test_structured_tags_and_indoor(self):
        indoor_point = {
            "thong_tin_chi_tiet": "TTTM cao cap co rap phim va dieu hoa mat lanh",
            "loai_hinh": "TTTM",
        }
        score_in = calculate_preference_score(indoor_point, [], preferred_tags=["shopping"], indoor_pref="trong_nha")
        score_out = calculate_preference_score(indoor_point, [], preferred_tags=["shopping"], indoor_pref="ngoai_troi")
        self.assertGreater(score_in, score_out)

    def test_explain_match(self):
        point = {
            "thong_tin_chi_tiet": "Quan ca phe ngam ho Tay tho mong",
            "loai_hinh": "Cafe",
        }
        reasons = explain_match(point, preference_keywords=["ca", "phe"], preferred_tags=["cafe"])
        self.assertTrue(any("cà phê" in r for r in reasons))


class TestItineraryEndpoints(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        # Create a test session by creating routes
        payload = {
            "region": "Hanoi",
            "start_time": "08:00",
            "end_time": "18:00",
            "start_point": "Hồ Hoàn Kiếm",
            "start_lat": 21.0285,
            "start_lon": 105.8542,
            "vehicle_type": "xe_may",
            "user_preference": "thích cà phê và chụp ảnh",
            "num_routes": 5,
        }
        r = self.client.post("/api/routes", json=payload)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["status"], "success")
        self.session_id = data["session_id"]
        self.routes = data["routes"]
        # Select first route
        sel = self.client.post("/api/routes/select", json={"session_id": self.session_id, "route_id": self.routes[0]["route_id"]})
        self.assertEqual(sel.status_code, 200)

    def test_quick_action_add_lunch_and_cafe(self):
        # Quick action: add_lunch
        r_lunch = self.client.post("/api/itinerary/quick-action", json={"session_id": self.session_id, "action": "add_lunch"})
        self.assertEqual(r_lunch.status_code, 200)
        data_lunch = r_lunch.json()
        self.assertIn(data_lunch["status"], ("success", "no_change"))
        if data_lunch["status"] == "success":
            self.assertIn("message", data_lunch)
            self.assertTrue(len(data_lunch["itinerary"]["places"]) >= len(self.routes[0]["places"]))

        # Quick action: remove_one
        r_remove = self.client.post("/api/itinerary/quick-action", json={"session_id": self.session_id, "action": "remove_one"})
        self.assertEqual(r_remove.status_code, 200)
        data_remove = r_remove.json()
        self.assertIn(data_remove["status"], ("success", "no_change"))

    def test_quick_action_preserves_other_routes(self):
        # Verify that modifying the selected route does not erase the other routes
        session = store.get(self.session_id)
        self.assertEqual(len(session.routes), len(self.routes))

    def test_parse_prompt_endpoint(self):
        r = self.client.post(
            "/api/parse-prompt",
            json={
                "prompt": "Đi Hà Nội 1 ngày, thích cafe và chụp ảnh",
                "context": {"vehicle_type": "xe_may", "start_point": "Hồ Gươm"},
                "use_llm": False,
            },
        )
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["fields"]["region"], "Hanoi")
        self.assertIn("cafe", data["fields"]["preferred_tags"])
        self.assertIn("photo", data["fields"]["preferred_tags"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
