"""
Calibration verification and tracking.

Verifies measurement system integrity against known references before
each test session. Maintains a JSON history database for drift detection
and recalibration scheduling.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict

import numpy as np

logger = logging.getLogger("OpenCharacterize.Calibration")


@dataclass
class CalibrationPoint:
    reference_voltage: float
    measured_code_mean: float
    measured_voltage: float
    error_mv: float
    error_pct: float
    timestamp: str
    pass_threshold_mv: float
    passed: bool


@dataclass
class CalibrationRecord:
    session_id: str
    timestamp: str
    dut_id: dict
    reference_points: List[CalibrationPoint]
    overall_pass: bool
    max_error_mv: float
    temperature_c: Optional[float] = None
    humidity_pct: Optional[float] = None
    notes: str = ""

    def to_dict(self) -> dict:
        d = {
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "dut_id": self.dut_id,
            "overall_pass": self.overall_pass,
            "max_error_mv": round(self.max_error_mv, 3),
            "notes": self.notes,
            "reference_points": [asdict(p) for p in self.reference_points],
        }
        if self.temperature_c is not None:
            d["temperature_c"] = self.temperature_c
        if self.humidity_pct is not None:
            d["humidity_pct"] = self.humidity_pct
        return d


class CalibrationManager:

    def __init__(
        self,
        cal_db_path: str = "data/calibration_history.json",
        error_threshold_mv: float = 10.0,
        recal_interval_days: int = 30,
    ):
        self.cal_db_path = Path(cal_db_path)
        self.cal_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.error_threshold_mv = error_threshold_mv
        self.recal_interval_days = recal_interval_days
        self.history: List[dict] = self._load_history()

    def _load_history(self) -> List[dict]:
        if self.cal_db_path.exists():
            with open(self.cal_db_path) as f:
                return json.load(f)
        return []

    def _save_history(self):
        with open(self.cal_db_path, "w") as f:
            json.dump(self.history, f, indent=2)

    def verify_calibration(
        self, dut,
        reference_voltages: Optional[List[float]] = None,
        n_samples: int = 1000,
    ) -> CalibrationRecord:
        """
        Measure known reference voltages, compare against expected,
        flag deviations beyond threshold.
        """
        if reference_voltages is None:
            reference_voltages = [0.5, 1.0, 1.65, 2.0, 2.5, 3.0]

        lsb = dut.spec.lsb_voltage
        points = []
        max_error = 0.0
        all_pass = True

        logger.info("Calibration verification")
        logger.info(f"  Points: {reference_voltages}")
        logger.info(f"  Threshold: +/-{self.error_threshold_mv} mV")

        for ref_v in reference_voltages:
            codes = dut.convert_array(np.full(n_samples, ref_v))
            mean_code = float(np.mean(codes))
            measured_v = mean_code * lsb
            err_mv = (measured_v - ref_v) * 1000
            err_pct = (err_mv / (ref_v * 1000)) * 100 if ref_v > 0 else 0

            passed = abs(err_mv) <= self.error_threshold_mv
            if not passed:
                all_pass = False
            max_error = max(max_error, abs(err_mv))

            points.append(CalibrationPoint(
                reference_voltage=ref_v, measured_code_mean=mean_code,
                measured_voltage=round(measured_v, 6),
                error_mv=round(err_mv, 3), error_pct=round(err_pct, 3),
                timestamp=datetime.now().isoformat(),
                pass_threshold_mv=self.error_threshold_mv, passed=passed,
            ))

            status = "PASS" if passed else "FAIL"
            logger.info(f"  {ref_v:.3f}V -> {measured_v:.4f}V  ({err_mv:+.1f} mV)  [{status}]")

        session_id = f"CAL-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        record = CalibrationRecord(
            session_id=session_id, timestamp=datetime.now().isoformat(),
            dut_id=dut.get_identity(), reference_points=points,
            overall_pass=all_pass, max_error_mv=round(max_error, 3),
        )

        self.history.append(record.to_dict())
        self._save_history()

        logger.info(f"  Result: {'PASS' if all_pass else 'FAIL'}  (max error: {max_error:.1f} mV)")
        return record

    def check_recalibration_due(self) -> bool:
        if not self.history:
            logger.warning("No calibration history -- calibration recommended")
            return True

        last_time = datetime.fromisoformat(self.history[-1]["timestamp"])
        days = (datetime.now() - last_time).days

        if days >= self.recal_interval_days:
            logger.warning(f"Last cal {days} days ago (limit: {self.recal_interval_days})")
            return True
        return False

    def detect_drift(self, parameter: str = "max_error_mv", window: int = 5) -> Dict:
        if len(self.history) < window:
            return {"status": "insufficient_history", "n_records": len(self.history)}

        values = np.array([h[parameter] for h in self.history[-window * 2:]])
        x = np.arange(len(values))
        slope = np.polyfit(x, values, 1)[0] if len(x) >= 2 else 0.0
        projected = values[-1] + slope * 10

        return {
            "n_records": len(values),
            "current": float(values[-1]),
            "mean": float(np.mean(values)),
            "slope_per_session": float(slope),
            "projected_10_sessions": float(projected),
            "approaching_limit": projected > self.error_threshold_mv,
        }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from virtual_dut.adc_model import esp32_adc

    dut = esp32_adc()
    cal = CalibrationManager(error_threshold_mv=15.0)
    record = cal.verify_calibration(dut)
    print(f"Session: {record.session_id}  History: {len(cal.history)} records")
