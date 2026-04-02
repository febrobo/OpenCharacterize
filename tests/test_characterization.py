"""
Unit tests for virtual ADC model and characterization engine.
Validates physical reasonableness of all measurements.

Run: pytest tests/ -v
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from virtual_dut.adc_model import VirtualADC, esp32_adc, stm32_adc, ideal_adc
from analysis.characterization_engine import CharacterizationEngine
from spc.statistical_process_control import SPCEngine
from calibration.cal_manager import CalibrationManager


class TestVirtualADC:

    def test_output_range(self):
        dut = ideal_adc()
        codes = dut.convert_array(np.linspace(0, 3.3, 10000))
        assert codes.min() >= 0
        assert codes.max() <= 4095

    def test_monotonicity(self):
        dut = ideal_adc()
        codes = dut.convert_array(np.linspace(0.1, 3.2, 5000))
        assert np.mean(np.diff(codes.astype(float)) >= 0) > 0.95

    def test_esp32_missing_codes(self):
        dut = esp32_adc()
        codes = dut.convert_array(np.linspace(0, 3.3, 200_000))
        assert len(np.unique(codes)) < 4096

    def test_esp32_dead_zone(self):
        dut = esp32_adc()
        codes = dut.convert_array(np.full(100, 0.05))
        assert np.all(codes == 0)

    def test_stm32_lower_noise(self):
        esp = esp32_adc()
        stm = stm32_adc()
        dc = np.full(5000, 1.65)
        assert np.std(stm.convert_array(dc).astype(float)) < np.std(esp.convert_array(dc).astype(float))

    def test_reproducibility(self):
        v = np.linspace(0, 3.3, 1000)
        np.testing.assert_array_equal(
            esp32_adc().convert_array(v),
            esp32_adc().convert_array(v),
        )

    def test_identity(self):
        idn = esp32_adc().get_identity()
        assert all(k in idn for k in ("manufacturer", "model", "serial"))


class TestCharacterization:

    @pytest.fixture
    def engine(self):
        return CharacterizationEngine(resolution_bits=12, vref=3.3)

    def test_ideal_inl(self, engine):
        dut = ideal_adc()
        ramp = np.linspace(0, 3.3, 100_000)
        result = engine.static_characterization(ramp, dut.convert_array(ramp))
        assert abs(result.inl_max) < 1.0
        assert abs(result.inl_min) < 1.0

    def test_esp32_inl_nonzero(self, engine):
        dut = esp32_adc()
        ramp = np.linspace(0, 3.3, 100_000)
        result = engine.static_characterization(ramp, dut.convert_array(ramp))
        assert abs(result.inl_max) > 2.0

    def test_ideal_enob(self, engine):
        dut = ideal_adc()
        fs = 100_000
        n_fft = 8192
        f_sig = 83 * fs / n_fft  # Coherent sampling
        t = np.arange(n_fft) / fs
        codes = dut.convert_array(1.65 + 1.4 * np.sin(2 * np.pi * f_sig * t), sample_rate_hz=fs)
        result = engine.dynamic_characterization(codes, fs, f_sig)
        assert result.enob > 10.0
        assert result.snr_db > 60.0

    def test_esp32_enob_lower(self, engine):
        fs = 100_000
        n_fft = 8192
        f_sig = 83 * fs / n_fft
        t = np.arange(n_fft) / fs
        sine = 1.65 + 1.4 * np.sin(2 * np.pi * f_sig * t)
        ideal_enob = engine.dynamic_characterization(ideal_adc().convert_array(sine, sample_rate_hz=fs), fs, f_sig).enob
        esp_enob = engine.dynamic_characterization(esp32_adc().convert_array(sine, sample_rate_hz=fs), fs, f_sig).enob
        assert esp_enob < ideal_enob

    def test_noise(self, engine):
        codes = esp32_adc().convert_array(np.full(5000, 1.65))
        result = engine.noise_characterization(codes)
        assert result.rms_noise_lsb > 0
        assert result.rms_noise_uv > 0

    def test_pass_fail(self, engine):
        dut = esp32_adc()
        ramp = np.linspace(0, 3.3, 50_000)
        static = engine.static_characterization(ramp, dut.convert_array(ramp))
        verdict = engine.evaluate_pass_fail(static, None, None, dut.get_spec())
        assert "OVERALL" in verdict
        assert "INL_max" in verdict


class TestSPC:

    def test_cpk_capable(self):
        values = np.random.default_rng(42).normal(10.0, 0.5, 100)
        result = SPCEngine().compute_cpk(values, usl=12.0, lsl=8.0)
        assert result.cpk > 1.0

    def test_cpk_off_center(self):
        values = np.random.default_rng(42).normal(11.5, 0.5, 100)
        result = SPCEngine().compute_cpk(values, usl=12.0, lsl=8.0)
        assert result.cpk < result.cp

    def test_control_chart_limits(self):
        values = np.random.default_rng(42).normal(10.0, 0.5, 50)
        chart = SPCEngine().control_chart(values, subgroup_size=5)
        assert chart.x_bar_ucl > chart.x_bar_cl > chart.x_bar_lcl

    def test_stable_no_trend(self):
        values = np.random.default_rng(42).normal(10.0, 0.1, 30)
        assert SPCEngine().detect_trends(values)["7_consecutive_trend"] is False


class TestCalibration:

    def test_ideal_passes(self, tmp_path):
        cal = CalibrationManager(cal_db_path=str(tmp_path / "cal.json"), error_threshold_mv=20.0)
        record = cal.verify_calibration(ideal_adc(), reference_voltages=[1.0, 2.0])
        assert record.overall_pass is True

    def test_history_persistence(self, tmp_path):
        cal = CalibrationManager(cal_db_path=str(tmp_path / "cal.json"))
        cal.verify_calibration(ideal_adc())
        cal.verify_calibration(ideal_adc())
        assert len(CalibrationManager(cal_db_path=str(tmp_path / "cal.json")).history) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
