"""
Test Suite: AI Refine for Complex Requests, Candidate Options & Multi-Turn Chat History.
Verifies requirements:
1. AI Refine handles complex requests ("Tìm chỗ yên tĩnh hơn gần đây", "Thay quán này bằng nơi phù hợp trời mưa").
2. AI returns candidate options (options list) for the user to choose rather than picking a single place arbitrarily.
3. Chat history is saved in the session (multi-turn).
4. AI assists in adjusting itinerary without replacing basic UI CRUD operations.
"""
import unittest
from fastapi.testclient import TestClient

from main import app
from itinerary_store import store
from ai_advisor import refine_route, AIRefineRequest


class TestAIRefineOptions(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.session = store.get_or_create("test_refine_session")
        self.session.clear_chat_history()

    def test_find_quiet_place_near_here(self):
        """Yêu cầu: 'Tìm chỗ yên tĩnh hơn gần đây' -> trả về options yên tĩnh, không gian chill, gần điểm hiện tại."""
        req = AIRefineRequest(
            start_time="08:00",
            end_time="17:00",
            current_ids=["12", "26", "10"], # Nộm Hàng Bạc, Đền Ngọc Sơn, Nhà thờ Lớn
            instruction="Tìm chỗ yên tĩnh hơn gần đây",
            session_id=self.session.session_id,
        )
        res = refine_route(req)

        self.assertEqual(res.intent, "options")
        self.assertGreaterEqual(len(res.options), 2)
        self.assertTrue(any("yên tĩnh" in opt.reason.lower() or "thư giãn" in opt.reason.lower() for opt in res.options))
        # Các điểm đông khách chen chúc không được có mặt trong options
        for opt in res.options:
            self.assertNotIn("hay đông khách", [t.lower() for t in opt.tags])
            self.assertIsNotNone(opt.place_id)
            self.assertIsNotNone(opt.ten)
            self.assertIsNotNone(opt.rating)

        # Lịch sử chat trong session phải được lưu
        hist = self.session.get_chat_history()
        self.assertGreaterEqual(len(hist), 2)
        self.assertEqual(hist[0]["role"], "user")
        self.assertEqual(hist[0]["content"], "Tìm chỗ yên tĩnh hơn gần đây")
        self.assertEqual(hist[1]["role"], "assistant")
        self.assertGreaterEqual(len(hist[1]["options"]), 2)

    def test_replace_with_indoor_rain_friendly(self):
        """Yêu cầu: 'Thay quán này bằng nơi phù hợp trời mưa' -> gợi ý các điểm trong nhà/TTTM/cafe có mái che."""
        req = AIRefineRequest(
            start_time="08:00",
            end_time="17:00",
            current_ids=["12", "26", "10"],
            instruction="Thay quán này bằng nơi phù hợp trời mưa",
            session_id=self.session.session_id,
        )
        res = refine_route(req)

        self.assertEqual(res.intent, "options")
        self.assertIsNotNone(res.target_place_name)
        self.assertGreaterEqual(len(res.options), 2)
        
        # Tất cả options đề xuất cho trời mưa đều phải là trong nhà / có mái che
        for opt in res.options:
            self.assertIn("replace", opt.action)
            tags_str = " ".join(opt.tags).lower()
            self.assertTrue("trong nhà" in tags_str or "mưa" in tags_str or "rạp phim" in tags_str or "ấm cúng" in tags_str)

    def test_multi_turn_selection_via_chat(self):
        """User nhận options xong nói 'chọn quán 1' -> hệ thống nhận diện và cập nhật đúng ID."""
        # Turn 1: Hỏi tìm điểm trời mưa
        req1 = AIRefineRequest(
            start_time="08:00",
            end_time="17:00",
            current_ids=["12", "26", "10"],
            instruction="Thay quán này bằng nơi phù hợp trời mưa",
            session_id=self.session.session_id,
        )
        res1 = refine_route(req1)
        self.assertGreaterEqual(len(res1.options), 1)
        chosen_opt = res1.options[0]

        # Turn 2: Người dùng gõ "chọn quán 1"
        req2 = AIRefineRequest(
            start_time="08:00",
            end_time="17:00",
            current_ids=["12", "26", "10"],
            instruction="chọn 1",
            session_id=self.session.session_id,
        )
        res2 = refine_route(req2)
        self.assertEqual(res2.intent, "select_previous")
        self.assertIn(chosen_opt.place_id, res2.suggested_ids)
        self.assertIn(chosen_opt.ten, res2.advice_text)

        # Chat history ghi nhận đủ 4 lượt trao đổi
        hist = self.session.get_chat_history()
        self.assertEqual(len(hist), 4)

    def test_chat_history_api_endpoints(self):
        """Kiểm tra API GET /api/chat-history và POST /api/chat-history."""
        # Gọi GET chat-history
        response = self.client.get(f"/api/chat-history?session_id={self.session.session_id}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertIsInstance(data["chat_history"], list)

        # Gọi POST chat-history
        post_res = self.client.post("/api/chat-history", json={
            "session_id": self.session.session_id,
            "role": "user",
            "content": "Test message recording"
        })
        self.assertEqual(post_res.status_code, 200)
        self.assertEqual(post_res.json()["status"], "success")

        # Kiểm tra lại số lượng tin nhắn
        get_res2 = self.client.get(f"/api/chat-history?session_id={self.session.session_id}")
        last_msg = get_res2.json()["chat_history"][-1]
        self.assertEqual(last_msg["content"], "Test message recording")


if __name__ == "__main__":
    unittest.main()
