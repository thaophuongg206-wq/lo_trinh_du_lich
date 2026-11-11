"""
User & Favorites Storage Module.
Provides lightweight, thread-safe user profile management and POI favorites persistence.
"""
import os
import json
import threading
from typing import Dict, Any, List, Optional

USER_DATA_FILE = "user_data.json"
_lock = threading.RLock()

DEFAULT_USER = {
    "user_id": "traveler_hn",
    "name": "Du khách Hà Nội",
    "email": "traveler@teravue.vn",
    "handle": "@traveler_hn",
    "avatar_url": "https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=120&q=80",
    "default_preference": "Yên tĩnh, thích cà phê cổ điển & di tích văn hóa",
    "favorites": ["18", "26", "2"],
}


class UserStore:
    def __init__(self, filepath: str = USER_DATA_FILE):
        self.filepath = filepath
        self.users: Dict[str, dict] = {}
        self._load()

    def _load(self):
        with _lock:
            if os.path.exists(self.filepath):
                try:
                    with open(self.filepath, "r", encoding="utf-8") as f:
                        self.users = json.load(f)
                except Exception:
                    self.users = {}
            if "traveler_hn" not in self.users:
                self.users["traveler_hn"] = dict(DEFAULT_USER)
                self._save()

    def _save(self):
        with _lock:
            try:
                with open(self.filepath, "w", encoding="utf-8") as f:
                    json.dump(self.users, f, ensure_ascii=False, indent=2)
            except Exception:
                pass

    def get_user(self, user_id: str = "traveler_hn") -> dict:
        with _lock:
            if not user_id:
                user_id = "traveler_hn"
            if user_id not in self.users:
                self.users[user_id] = {
                    "user_id": user_id,
                    "name": user_id.split("@")[0] if "@" in user_id else "Du khách",
                    "email": user_id if "@" in user_id else f"{user_id}@teravue.vn",
                    "handle": f"@{user_id.split('@')[0]}",
                    "avatar_url": DEFAULT_USER["avatar_url"],
                    "default_preference": DEFAULT_USER["default_preference"],
                    "favorites": ["18", "26"],
                }
                self._save()
            data = dict(self.users[user_id])
            data["id"] = data.get("user_id", user_id)
            data["favorites_count"] = len(data.get("favorites", []))
            return data

    def update_profile(self, user_id: str, updates: dict) -> dict:
        with _lock:
            if not user_id:
                user_id = "traveler_hn"
            user = self.get_user(user_id)
            for k in ("name", "email", "handle", "default_preference", "avatar_url"):
                if k in updates and updates[k] is not None:
                    user[k] = updates[k]
            self.users[user_id] = user
            self._save()
            user["id"] = user.get("user_id", user_id)
            user["favorites_count"] = len(user.get("favorites", []))
            return user

    def toggle_favorite(self, user_id: str, place_id: str) -> tuple[bool, List[str]]:
        with _lock:
            if not user_id:
                user_id = "traveler_hn"
            user = self.get_user(user_id)
            favs = set(map(str, user.get("favorites", [])))
            pid = str(place_id)
            if pid in favs:
                favs.remove(pid)
                is_fav = False
            else:
                favs.add(pid)
                is_fav = True
            user["favorites"] = sorted(favs)
            self.users[user_id] = user
            self._save()
            return is_fav, user["favorites"]

    def get_favorites(self, user_id: str = "traveler_hn") -> List[str]:
        user = self.get_user(user_id)
        return list(user.get("favorites", []))


user_store = UserStore()
