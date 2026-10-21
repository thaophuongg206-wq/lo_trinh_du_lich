"""
Test suite toàn diện kiểm thử bộ tính năng AI Assistant hoàn chỉnh:
1. AI fallback thông minh khi LLM offline / lỗi kết nối.
2. Quản lý Chat History (lưu trữ multi-turn, xem lịch sử, xóa lịch sử).
3. Quản lý Account & Favorite cơ bản (Profile, Toggle Favorite, Get Favorites list, Login).
4. Tích hợp AI vào cơ chế thích ứng: giải thích nguyên nhân đề xuất đổi route (mưa, kẹt xe, trễ giờ) và tự động đồng bộ vào chat history.
"""

import sys
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from main import app
from itinerary_store import store
from api.db import fetch_all_points


class TestAIAssistantComplete(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.all_pts = fetch_all_points()
        assert len(cls.all_pts) >= 4, "Cần ít nhất 4 địa điểm trong database để test"

    def setUp(self):
        # Tạo session mới trước mỗi test
        self.session = store.create()
        self.session_id = self.session.session_id

        # Thiết lập một itinerary giả định có 3 điểm
        p0 = self.all_pts[0]
        p1 = self.all_pts[1]
        p2 = self.all_pts[2]

        self.mock_itinerary = {
            "route_id": "test_route_1",
            "places": [
                {
                    "id": p0["id"],
                    "ten": p0["ten"],
                    "lat": p0.get("lat") or p0.get("vi_do", 21.0),
                    "lon": p0.get("lon") or p0.get("kinh_do", 105.8),
                    "loai_hinh": p0.get("loai_hinh", "Tham quan"),
                    "category": "tham_quan",
                    "thoi_gian_dung": 45,
                    "gio_den": "08:30",
                    "gio_di": "09:15",
                },
                {
                    "id": p1["id"],
                    "ten": p1["ten"],
                    "lat": p1.get("lat") or p1.get("vi_do", 21.0),
                    "lon": p1.get("lon") or p1.get("kinh_do", 105.8),
                    "loai_hinh": p1.get("loai_hinh", "Cafe"),
                    "category": "cafe",
                    "thoi_gian_dung": 45,
                    "gio_den": "09:30",
                    "gio_di": "10:15",
                },
                {
                    "id": p2["id"],
                    "ten": p2["ten"],
                    "lat": p2.get("lat") or p2.get("vi_do", 21.0),
                    "lon": p2.get("lon") or p2.get("kinh_do", 105.8),
                    "loai_hinh": p2.get("loai_hinh", "Ăn uống"),
                    "category": "an_uong",
                    "thoi_gian_dung": 60,
                    "gio_den": "11:30",
                    "gio_di": "12:30",
                },
            ],
            "timeline": [],
        }
        self.session.set_current_itinerary(self.mock_itinerary)

    # ========================================================
    # 1. TEST AI FALLBACK KHI LLM LỖI
    # ========================================================
    @patch("ai_advisor.call_ai_service")
    def test_ai_fallback_remove_by_order(self, mock_llm):
        """Khi LLM chết hoàn toàn, lệnh 'bỏ điểm 1' phải loại bỏ đúng điểm 1."""
        mock_llm.side_effect = Exception("Ollama connection refused / timeout!")

        pts = self.session.current_itinerary["places"]
        target_pt = pts[0]
        cur_ids = [str(p["id"]) for p in pts]

        resp = self.client.post("/api/ai-refine", json={
            "instruction": "Bỏ điểm 1 đi",
            "current_ids": cur_ids,
            "session_id": self.session_id,
            "start_time": "08:00",
            "end_time": "17:00",
        })

        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        # Điểm 1 phải không còn nằm trong suggested_ids
        self.assertNotIn(str(target_pt["id"]), [str(i) for i in data["suggested_ids"]])
        self.assertIn("loại bỏ", data["advice_text"].lower())
        # Điểm bị bỏ phải được ghi nhận vào excluded_ids
        self.assertIn(str(target_pt["id"]), [str(i) for i in data["excluded_ids"]])

    @patch("ai_advisor.call_ai_service")
    def test_ai_fallback_remove_by_name(self, mock_llm):
        """Khi LLM chết hoàn toàn, lệnh bỏ theo tên phải tìm đúng điểm và loại bỏ."""
        mock_llm.side_effect = Exception("GPU out of memory!")

        pts = self.session.current_itinerary["places"]
        target_pt = pts[1]
        cur_ids = [str(p["id"]) for p in pts]

        resp = self.client.post("/api/ai-refine", json={
            "instruction": f"Xóa điểm {target_pt['ten']} giúp mình",
            "current_ids": cur_ids,
            "session_id": self.session_id,
            "start_time": "08:00",
            "end_time": "17:00",
        })

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertNotIn(str(target_pt["id"]), [str(i) for i in data["suggested_ids"]])
        self.assertIn(target_pt["ten"].lower(), data["advice_text"].lower())

    @patch("ai_advisor.call_ai_service")
    def test_ai_fallback_add_food_place(self, mock_llm):
        """Khi LLM offline, lệnh 'thêm quán ăn trưa' phải tự chọn quán ăn phù hợp và bổ sung."""
        mock_llm.side_effect = Exception("Ollama offline")

        pts = self.session.current_itinerary["places"]
        cur_ids = [str(p["id"]) for p in pts]

        resp = self.client.post("/api/ai-refine", json={
            "instruction": "Thêm quán ăn trưa ngon gần đây",
            "current_ids": cur_ids,
            "session_id": self.session_id,
            "start_time": "08:00",
            "end_time": "17:00",
        })

        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        # Số lượng id gợi ý phải tăng thêm 1
        self.assertGreater(len(data["suggested_ids"]), len(cur_ids))
        self.assertIn("quán ăn", data["advice_text"].lower())

    # ========================================================
    # 2. TEST CHAT HISTORY
    # ========================================================
    def test_chat_history_full_flow(self):
        """Kiểm thử thêm tin nhắn, lấy lịch sử và xóa lịch sử chat."""
        # 1. Ban đầu lịch sử rỗng
        resp1 = self.client.get(f"/api/chat-history?session_id={self.session_id}")
        self.assertEqual(resp1.status_code, 200)
        self.assertEqual(len(resp1.json()["chat_history"]), 0)

        # 2. Ghi 1 tin nhắn user và 1 tin nhắn assistant
        self.client.post("/api/chat-history", json={
            "session_id": self.session_id,
            "role": "user",
            "content": "Gợi ý quán cafe chill quanh Hồ Tây",
        })
        self.client.post("/api/chat-history", json={
            "session_id": self.session_id,
            "role": "assistant",
            "content": "Dưới đây là một số quán cà phê ven hồ rất thoáng đãng...",
        })

        # 3. Lấy lại lịch sử chat
        resp2 = self.client.get(f"/api/chat-history?session_id={self.session_id}")
        self.assertEqual(resp2.status_code, 200)
        hist = resp2.json()["chat_history"]
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[0]["role"], "user")
        self.assertEqual(hist[1]["role"], "assistant")

        # 4. Xóa lịch sử chat
        del_resp = self.client.delete(f"/api/chat-history?session_id={self.session_id}")
        self.assertEqual(del_resp.status_code, 200)

        # 5. Sau khi xóa, lịch sử chat phải trống
        resp3 = self.client.get(f"/api/chat-history?session_id={self.session_id}")
        self.assertEqual(len(resp3.json()["chat_history"]), 0)

    # ========================================================
    # 3. TEST ACCOUNT & FAVORITES
    # ========================================================
    def test_user_profile_and_favorites(self):
        """Kiểm thử xem/sửa profile và thêm/bớt địa điểm yêu thích."""
        # 1. Lấy profile mặc định
        res_prof = self.client.get("/api/user/profile?user_id=traveler_hn")
        self.assertEqual(res_prof.status_code, 200)
        u_data = res_prof.json()["data"]
        self.assertEqual(u_data["id"], "traveler_hn")

        # 2. Cập nhật profile
        res_up = self.client.post("/api/user/profile", json={
            "user_id": "traveler_hn",
            "name": "Nguyễn Văn Du Lịch",
            "default_preference": "Thích chụp ảnh vintage và thưởng thức cà phê trứng",
        })
        self.assertEqual(res_up.status_code, 200)
        self.assertEqual(res_up.json()["data"]["name"], "Nguyễn Văn Du Lịch")

        # 3. Toggle Favorite: Thêm địa điểm 0 vào favorites
        target_pid = str(self.all_pts[0]["id"])
        res_fav1 = self.client.post("/api/user/favorites/toggle", json={
            "user_id": "traveler_hn",
            "place_id": target_pid,
        })
        self.assertEqual(res_fav1.status_code, 200)
        self.assertTrue(res_fav1.json()["is_favorite"])
        self.assertIn(target_pid, res_fav1.json()["favorites"])

        # 4. Lấy danh sách favorites chi tiết
        res_fav_list = self.client.get("/api/user/favorites?user_id=traveler_hn")
        self.assertEqual(res_fav_list.status_code, 200)
        fav_items = res_fav_list.json()["data"]
        self.assertTrue(any(item["id"] == target_pid for item in fav_items))

        # 5. Toggle lần 2: Bỏ địa điểm khỏi favorites
        res_fav2 = self.client.post("/api/user/favorites/toggle", json={
            "user_id": "traveler_hn",
            "place_id": target_pid,
        })
        self.assertEqual(res_fav2.status_code, 200)
        self.assertFalse(res_fav2.json()["is_favorite"])
        self.assertNotIn(target_pid, res_fav2.json()["favorites"])

        # 6. Test Login / Switch user
        res_login = self.client.post("/api/user/login", json={
            "email": "tester@hanoi.vn"
        })
        self.assertEqual(res_login.status_code, 200)
        self.assertEqual(res_login.json()["data"]["id"], "tester@hanoi.vn")

    # ========================================================
    # 4. TEST ADAPTIVE REPLANNING & AI EXPLANATION
    # ========================================================
    def test_adaptive_replanning_rain_explanation(self):
        """Kiểm thử mô phỏng thời tiết mưa: AI tái tối ưu lộ trình và sinh lời giải thích tự nhiên."""
        res = self.client.post("/api/replan/simulate", json={
            "session_id": self.session_id,
            "disruption_type": "rain",
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data.get("replanned"))
        self.assertIn("ai_explanation", data)
        self.assertIn("🌧️", data["ai_explanation"])
        self.assertIn("mưa", data["ai_explanation"].lower())

        # Lời giải thích phải được ghi tự động vào session chat history
        hist = self.session.get_chat_history()
        self.assertGreater(len(hist), 0)
        last_msg = hist[-1]
        self.assertEqual(last_msg["role"], "assistant")
        self.assertIn("🌧️", last_msg["content"])
        self.assertEqual(last_msg.get("meta", {}).get("type"), "adaptive_replan")

    def test_adaptive_replanning_traffic_explanation(self):
        """Kiểm thử mô phỏng ùn tắc giao thông: AI giải thích nguyên nhân né ùn tắc."""
        res = self.client.post("/api/replan/simulate", json={
            "session_id": self.session_id,
            "disruption_type": "traffic",
        })
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("🚗", data["ai_explanation"])
        self.assertIn("ùn", data["ai_explanation"].lower())


if __name__ == "__main__":
    unittest.main()
