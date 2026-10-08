"""
Crowd-density forecasting.

Training data schema (CSV or DB table):
place_id,timestamp,crowd_index
where crowd_index is 0..1.

If historical observations exist, GradientBoostingRegressor is trained on
calendar/time features + place id. If no observations exist, a transparent
temporal baseline is returned; this is intentionally NOT presented as a
trained prediction model.
"""
from __future__ import annotations
import csv, os, math
from datetime import datetime
from typing import Dict, Iterable, Optional
import numpy as np

try:
    from sklearn.ensemble import GradientBoostingRegressor
except Exception:
    GradientBoostingRegressor = None

class CrowdPredictor:
    def __init__(self, csv_path: str = "crowd_history.csv"):
        self.csv_path = csv_path
        self.model = None
        self.place_map: Dict[str,int] = {}
        self.trained = False
        self.samples = 0
        self._train()

    @staticmethod
    def _features(place_id: str, when: datetime, place_map: Dict[str,int]) -> list:
        pid = place_map.get(str(place_id), 0)
        dow = when.weekday()
        hour = when.hour + when.minute/60
        return [pid, dow, math.sin(2*math.pi*hour/24), math.cos(2*math.pi*hour/24),
                math.sin(2*math.pi*dow/7), math.cos(2*math.pi*dow/7)]

    def _train(self):
        from core import audit
        if not os.path.exists(self.csv_path):
            audit.record("crowd", "history_csv_missing", fallback="heuristic crowd", detail=self.csv_path)
            return
        if GradientBoostingRegressor is None:
            audit.record("crowd", "sklearn_missing", fallback="heuristic crowd")
            return
        rows=[]
        with open(self.csv_path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                try:
                    rows.append((str(r["place_id"]), datetime.fromisoformat(r["timestamp"]), float(r["crowd_index"])))
                except Exception:
                    continue
        if len(rows) < 20:
            audit.record("crowd", "too_few_history_rows", fallback="heuristic crowd", detail=str(len(rows)))
            return
        self.place_map={pid:i for i,pid in enumerate(sorted({r[0] for r in rows}), start=1)}
        X=np.array([self._features(pid,dt,self.place_map) for pid,dt,_ in rows], dtype=float)
        y=np.clip(np.array([v for *_,v in rows]),0,1)
        self.model=GradientBoostingRegressor(random_state=42, n_estimators=120, max_depth=3, learning_rate=.05)
        self.model.fit(X,y)
        self.trained=True; self.samples=len(rows)

    def predict(self, place_id: str, when: datetime) -> Dict[str, object]:
        if self.trained:
            x=np.array([self._features(str(place_id),when,self.place_map)])
            value=float(np.clip(self.model.predict(x)[0],0,1))
            return {"crowd_index":round(value,3),"source":"gradient_boosting","trained":True,"samples":self.samples}
        # Transparent baseline for development only. It is not a learned model.
        h=when.hour + when.minute/60
        rush=max(
            math.exp(-((h-8)/1.7)**2),
            math.exp(-((h-18)/2.0)**2)
        )
        weekend=0.10 if when.weekday()>=5 else 0.0
        value=max(0,min(1,0.20 + 0.55*rush + weekend))
        return {"crowd_index":round(value,3),"source":"temporal_baseline","trained":False,"samples":0}
