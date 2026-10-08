"""Pydantic request models (single place, shared by every router)."""
from typing import Dict
from typing import List, Optional
from pydantic import BaseModel, field_validator

class OptimizationRequest(BaseModel):
    nsga_seeded: bool = True          # False -> random-init NSGA-II (baseline)
    nsga_local_search: bool = True    # False -> no 2-opt on children (baseline)
    region: str
    start_time: str
    end_time: str
    trip_date: str = ""           # Ngày khởi hành (YYYY-MM-DD)
    start_point: str = ""         # Tên điểm xuất phát (tìm theo tên)
    vehicle_type: str = "xe_may"  # Phương tiện
    start_lat: Optional[float] = None   # Vĩ độ GPS (khi dùng vị trí hiện tại)
    start_lon: Optional[float] = None   # Kinh độ GPS
    user_preference: str = ""     # Mô tả sở thích không gian của người dùng (VD: "yên tĩnh, view đẹp")
    weight: int = 50              # 0 = ưu tiên khoảng cách (đi gần) .. 100 = ưu tiên đúng sở thích (trải nghiệm)
    algorithm: str = "legacy"     # legacy | hybrid_nsga2; legacy giữ flow cũ, hybrid phục vụ mô hình nghiên cứu
    ai_selected_ids: Optional[List[str]] = None  # ID điểm đến do AI (Ollama) gợi ý từ /api/ai-suggest.
                                                   # ĐÂY CHỈ LÀ ƯU TIÊN (must-visit), KHÔNG phải danh sách
                                                   # duy nhất: Greedy vẫn được dùng toàn bộ candidate pool
                                                   # để fill-up quỹ thời gian còn trống (mục 5).

    # ---- STATE / RÀNG BUỘC (mục 6, 7, 8, 11) ----
    session_id: Optional[str] = None              # Phiên lập lộ trình; nếu có, backend nhớ excluded_ids
    excluded_ids: Optional[List[str]] = None      # Điểm bị loại BỔ SUNG cho lần gọi này (gộp với state phiên)
    num_routes: int = 5                           # Số route mong muốn (chặn trong khoảng 3..5)
    prompt: str = ""                              # Câu hỏi tự do -> LLM-as-Parser -> ràng buộc cho optimizer (hybrid)
    return_to_start: bool = False                 # Hybrid: có điểm kết thúc N+1 = điểm xuất phát
    category_caps: Optional[Dict[str, int]] = None  # Hạn mức theo loại, vd {"cafe":1,"an_uong":2,"food_cafe_total":3}; mặc định tự tính theo thời lượng

    @field_validator("num_routes")
    @classmethod
    def validate_num_routes(cls, v):
        if v is None:
            return 5
        return max(3, min(5, int(v)))

    @field_validator("weight")
    @classmethod
    def validate_weight(cls, v):
        if v is None:
            return 50
        if not (0 <= v <= 100):
            raise ValueError("weight phải nằm trong khoảng 0-100")
        return v
