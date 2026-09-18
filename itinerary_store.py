"""
============================================================
ITINERARY STATE STORE — SOURCE OF TRUTH CỦA BACKEND (mục 11)
------------------------------------------------------------
Trước đây backend hoàn toàn stateless: toàn bộ trạng thái chuyến đi nằm ở
`tripState` trong app.js. Hệ quả:

  * Không có chỗ nào ghi nhớ "người dùng đã bỏ điểm X" → Greedy fill-up lấy
    lại X từ DB ở lần tính kế tiếp (lỗi mục 6).
  * Screen 2 và Screen 3 có thể đang nhìn hai tập dữ liệu khác nhau vì mỗi
    lần gọi /api/optimize-route là một lần sinh route mới (lỗi mục 10).
  * Map / timeline / AI context được suy ra từ ba nguồn khác nhau (mục 11).

Module này giữ MỘT trạng thái thống nhất cho mỗi phiên lập kế hoạch:

    TripSession
      ├── params            : tham số Screen 1 (giờ, ngày, phương tiện, sở thích...)
      ├── routes            : 3–5 route ĐÃ SINH SẴN (route_id -> object đầy đủ)
      ├── selected_route_id : route người dùng chốt ở Screen 2
      ├── current_itinerary : SOURCE OF TRUTH cho map + timeline + AI context
      ├── excluded_ids      : ràng buộc cứng, KHÔNG BAO GIỜ được quay lại
      └── pinned_ids        : điểm người dùng/AI muốn giữ (must-visit)

GHI CHÚ TRIỂN KHAI: store nằm trong RAM của process, đủ cho một app FastAPI
chạy single-process như hiện tại (uvicorn main:app --reload). Nếu sau này
chạy nhiều worker hoặc cần giữ state qua lần restart, thay thân của
`SessionStore` bằng Redis/DB — toàn bộ phần còn lại của backend chỉ gọi qua
API của lớp này nên không phải sửa theo.
============================================================
"""

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

# Phiên hết hạn sau 6 tiếng không đụng tới (một chuyến đi trong ngày là đủ dài).
SESSION_TTL_SECONDS = 6 * 3600
# Trần số phiên giữ đồng thời, tránh rò rỉ bộ nhớ nếu có nhiều người dùng.
MAX_SESSIONS = 500


class TripSession:
    """Trạng thái của MỘT phiên lập lộ trình (Screen 1 → 2 → 3 → chatbot)."""

    def __init__(self, session_id: str, params: Optional[Dict[str, Any]] = None):
        self.session_id: str = session_id
        self.params: Dict[str, Any] = dict(params or {})

        # 3–5 route đã sinh sẵn trước khi Screen 2 render (mục 2, mục 3).
        self.routes: Dict[str, dict] = {}
        self.route_order: List[str] = []

        # Route người dùng chốt ở Screen 2 (mục 10).
        self.selected_route_id: Optional[str] = None

        # Lịch trình đang hiển thị — nguồn duy nhất cho map/timeline/AI (mục 11).
        self.current_itinerary: Optional[dict] = None

        # RÀNG BUỘC CỨNG: id đã bị loại, Greedy tuyệt đối không được thêm lại
        # (mục 6, 7, 8, 9). Đây là set id — KHÔNG đụng gì tới database.
        self.excluded_ids: set = set()

        # Điểm phải giữ lại khi tính lại lịch trình (must-visit).
        self.pinned_ids: set = set()

        self.created_at: float = time.time()
        self.updated_at: float = time.time()

    # ---------------- params ----------------

    def update_params(self, params: Dict[str, Any]) -> None:
        for k, v in (params or {}).items():
            if v is not None:
                self.params[k] = v
        self.touch()

    def touch(self) -> None:
        self.updated_at = time.time()

    # ---------------- routes ----------------

    def set_routes(self, routes: List[dict]) -> None:
        """Lưu tập route candidate. Mỗi route là một THỰC THỂ ĐỘC LẬP (mục 3):
        Screen 3 chỉ việc lấy lại đúng object này, không sinh lại (mục 10)."""
        self.routes = {}
        self.route_order = []
        for r in routes:
            rid = r["route_id"]
            self.routes[rid] = r
            self.route_order.append(rid)
        # Route cũ không còn tồn tại thì bỏ lựa chọn cũ đi cho khỏi trỏ lung tung.
        if self.selected_route_id not in self.routes:
            self.selected_route_id = None
        self.touch()

    def list_routes(self) -> List[dict]:
        return [self.routes[rid] for rid in self.route_order if rid in self.routes]

    def get_route(self, route_id: str) -> Optional[dict]:
        return self.routes.get(route_id)

    def select_route(self, route_id: str) -> Optional[dict]:
        """Chốt route. KHÔNG sinh lại route mới — trả về đúng object đã lưu."""
        route = self.routes.get(route_id)
        if route is None:
            return None
        self.selected_route_id = route_id
        self.current_itinerary = route
        self.pinned_ids = {p["id"] for p in route.get("places", []) if p.get("id") != "gps_current"}
        self.touch()
        return route

    # ---------------- itinerary ----------------

    def set_current_itinerary(self, itinerary: dict, repin: bool = True) -> None:
        self.current_itinerary = itinerary
        if repin:
            self.pinned_ids = {
                p["id"] for p in itinerary.get("places", []) if p.get("id") != "gps_current"
            }
        self.touch()

    def current_place_ids(self) -> List[str]:
        if not self.current_itinerary:
            return []
        return [
            p["id"]
            for p in self.current_itinerary.get("places", [])
            if p.get("id") != "gps_current"
        ]

    # ---------------- exclusion (mục 6, 7, 8, 9) ----------------

    def exclude(self, ids) -> List[str]:
        """Đánh dấu loại trừ. KHÔNG xoá dữ liệu khỏi database — chỉ thêm id vào
        một set ràng buộc, nên người dùng có thể khôi phục bất cứ lúc nào."""
        added = []
        for i in ids or []:
            i = str(i)
            if i and i != "gps_current" and i not in self.excluded_ids:
                self.excluded_ids.add(i)
                added.append(i)
            self.pinned_ids.discard(i)
        if added:
            self.touch()
        return added

    def restore(self, ids) -> List[str]:
        """Bỏ loại trừ (khi người dùng đổi ý, hoặc AI hiểu nhầm ý)."""
        removed = []
        for i in ids or []:
            i = str(i)
            if i in self.excluded_ids:
                self.excluded_ids.discard(i)
                removed.append(i)
        if removed:
            self.touch()
        return removed

    def pin(self, ids) -> None:
        for i in ids or []:
            i = str(i)
            if i and i != "gps_current" and i not in self.excluded_ids:
                self.pinned_ids.add(i)
        self.touch()

    # ---------------- serialize ----------------

    def snapshot(self) -> dict:
        """Ảnh chụp trạng thái để trả cho Frontend / debug / test."""
        return {
            "session_id": self.session_id,
            "params": self.params,
            "route_ids": list(self.route_order),
            "selected_route_id": self.selected_route_id,
            "excluded_ids": sorted(self.excluded_ids),
            "pinned_ids": sorted(self.pinned_ids),
            "current_place_ids": self.current_place_ids(),
            "updated_at": self.updated_at,
        }


class SessionStore:
    """Kho phiên, thread-safe (uvicorn có thể chạy nhiều thread cho endpoint sync)."""

    def __init__(self):
        self._lock = threading.RLock()
        self._sessions: Dict[str, TripSession] = {}

    def _evict_expired_locked(self) -> None:
        now = time.time()
        dead = [
            sid for sid, s in self._sessions.items() if now - s.updated_at > SESSION_TTL_SECONDS
        ]
        for sid in dead:
            self._sessions.pop(sid, None)
        # Nếu vẫn quá tải, bỏ các phiên cũ nhất.
        if len(self._sessions) > MAX_SESSIONS:
            for sid, _ in sorted(self._sessions.items(), key=lambda kv: kv[1].updated_at)[
                : len(self._sessions) - MAX_SESSIONS
            ]:
                self._sessions.pop(sid, None)

    def create(self, params: Optional[Dict[str, Any]] = None) -> TripSession:
        with self._lock:
            self._evict_expired_locked()
            sid = uuid.uuid4().hex[:16]
            session = TripSession(sid, params)
            self._sessions[sid] = session
            return session

    def get(self, session_id: Optional[str]) -> Optional[TripSession]:
        if not session_id:
            return None
        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.touch()
            return session

    def get_or_create(
        self, session_id: Optional[str], params: Optional[Dict[str, Any]] = None
    ) -> TripSession:
        session = self.get(session_id)
        if session is None:
            session = self.create(params)
        elif params:
            session.update_params(params)
        return session

    def drop(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def count(self) -> int:
        with self._lock:
            return len(self._sessions)


# Instance dùng chung toàn app.
store = SessionStore()
