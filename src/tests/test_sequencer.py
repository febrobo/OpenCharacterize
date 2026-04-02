"""
Configurable test execution engine. Chains characterization tests
together with JSON-defined sequences and pass/fail limits pulled
from DUT datasheet specifications.
"""

import json
import time
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum

import numpy as np

from analysis.characterization_engine import (
    CharacterizationEngine,
    CharacterizationReport,
    StaticResults,
    DynamicResults,
    NoiseResults,
    HistogramResults,
)
from virtual_dut.adc_model import VirtualADC, ADCSpec


logger = logging.getLogger("OpenCharacterize.Sequencer")


class TestStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class TestStep:
    name: str
    test_type: str
    enabled: bool = True
    parameters: Dict[str, Any] = field(default_factory=dict)
    status: TestStatus = TestStatus.PENDING
    duration_s: float = 0.0
    result: Any = None
    error_msg: str = ""


@dataclass
class TestSequenceConfig:
    name: str
    description: str
    dut_type: str
    resolution_bits: int = 12
    vref: float = 3.3
    sample_rate_hz: int = 100_000
    steps: List[TestStep] = field(default_factory=list)
    pass_fail_limits: Dict[str, Dict] = field(default_factory=dict)

    @classmethod
    def from_json(cls, filepath: str) -> "TestSequenceConfig":
        with open(filepath) as f:
            data = json.load(f)
        steps = [TestStep(**s) for s in data.pop("steps", [])]
        return cls(**data, steps=steps)

    @classmethod
    def default_full_characterization(cls) -> "TestSequenceConfig":
        return cls(
            name="Full ADC Characterization",
            description="Static + dynamic + noise characterization per IEEE 1241",
            dut_type="virtual_esp32",
            steps=[
                TestStep(
                    name="Ramp Linearity Test",
                    test_type="static",
                    parameters={"num_points": 100_000, "start_v": 0.0, "stop_v": 3.3},
                ),
                TestStep(
                    name="DC Noise — Mid-Scale",
                    test_type="noise",
                    parameters={"dc_voltage": 1.65, "num_samples": 10_000},
                ),
                TestStep(
                    name="DC Noise — Quarter-Scale",
                    test_type="noise",
                    parameters={"dc_voltage": 0.825, "num_samples": 10_000},
                ),
                TestStep(
                    name="DC Noise — Three-Quarter-Scale",
                    test_type="noise",
                    parameters={"dc_voltage": 2.475, "num_samples": 10_000},
                ),
                TestStep(
                    name="Dynamic — 1 kHz Sine",
                    test_type="dynamic",
                    parameters={
                        "signal_freq_hz": 997, "amplitude_v": 1.4,
                        "dc_offset_v": 1.65, "num_samples": 8192,
                        "window": "blackmanharris",
                    },
                ),
                TestStep(
                    name="Dynamic — 10 kHz Sine",
                    test_type="dynamic",
                    parameters={
                        "signal_freq_hz": 9973, "amplitude_v": 1.4,
                        "dc_offset_v": 1.65, "num_samples": 8192,
                        "window": "blackmanharris",
                    },
                ),
                TestStep(
                    name="Sine Histogram (Code Density)",
                    test_type="histogram",
                    parameters={
                        "signal_freq_hz": 997, "amplitude_v": 1.4,
                        "dc_offset_v": 1.65, "num_samples": 500_000,
                    },
                ),
            ],
            pass_fail_limits={
                "INL_max": {"limit": 12.0, "unit": "LSB"},
                "DNL_max": {"limit": 2.0, "unit": "LSB"},
                "SNR": {"limit": 55.0, "unit": "dB"},
                "ENOB": {"limit": 9.0, "unit": "bits"},
                "THD": {"limit": -55.0, "unit": "dB"},
                "Offset_Error": {"limit": 40.0, "unit": "mV"},
                "Gain_Error": {"limit": 3.0, "unit": "%"},
            },
        )

    def to_json(self, filepath: str):
        data = {
            "name": self.name, "description": self.description,
            "dut_type": self.dut_type, "resolution_bits": self.resolution_bits,
            "vref": self.vref, "sample_rate_hz": self.sample_rate_hz,
            "steps": [{"name": s.name, "test_type": s.test_type,
                       "enabled": s.enabled, "parameters": s.parameters}
                      for s in self.steps],
            "pass_fail_limits": self.pass_fail_limits,
        }
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)


class TestSequencer:

    def __init__(self, dut: VirtualADC, config: TestSequenceConfig):
        self.dut = dut
        self.config = config
        self.engine = CharacterizationEngine(
            resolution_bits=config.resolution_bits, vref=config.vref)
        self.report = CharacterizationReport(dut_id=dut.get_identity())
        self._callbacks: List = []

    def on_step_complete(self, callback):
        self._callbacks.append(callback)

    def run(self) -> CharacterizationReport:
        logger.info(f"Sequence: {self.config.name}")
        logger.info(f"DUT: {self.dut.get_identity()}")
        logger.info(f"Steps: {len(self.config.steps)}")

        self.dut.reset()

        for i, step in enumerate(self.config.steps):
            if not step.enabled:
                step.status = TestStatus.SKIPPED
                logger.info(f"  [{i+1}/{len(self.config.steps)}] SKIP  {step.name}")
                continue

            logger.info(f"  [{i+1}/{len(self.config.steps)}] RUN   {step.name}")
            step.status = TestStatus.RUNNING
            t0 = time.time()

            try:
                handler = {
                    "static": self._run_static,
                    "dynamic": self._run_dynamic,
                    "noise": self._run_noise,
                    "histogram": self._run_histogram,
                }.get(step.test_type)

                if handler is None:
                    raise ValueError(f"Unknown test type: {step.test_type}")

                step.result = handler(step.parameters)

                attr = step.test_type if step.test_type != "dynamic" else "dynamic"
                setattr(self.report, attr, step.result)
                step.status = TestStatus.PASSED

            except Exception as e:
                step.status = TestStatus.ERROR
                step.error_msg = str(e)
                logger.error(f"         ERROR: {e}")

            step.duration_s = time.time() - t0

            for cb in self._callbacks:
                cb(step, i, len(self.config.steps))

        self.report.pass_fail = self.engine.evaluate_pass_fail(
            self.report.static, self.report.dynamic,
            self.report.noise, self.dut.get_spec())

        overall = "PASS" if self.report.pass_fail.get("OVERALL", {}).get("pass") else "FAIL"
        logger.info(f"Sequence complete -- {overall}")
        return self.report

    def _run_static(self, params: dict) -> StaticResults:
        n = params.get("num_points", 100_000)
        stimulus = np.linspace(params.get("start_v", 0.0), params.get("stop_v", self.config.vref), n)
        codes = self.dut.convert_array(stimulus)
        return self.engine.static_characterization(stimulus, codes)

    def _run_dynamic(self, params: dict) -> DynamicResults:
        fs = self.config.sample_rate_hz
        n = params.get("num_samples", 8192)
        f_sig = params.get("signal_freq_hz", 997)
        t = np.arange(n) / fs
        stimulus = params.get("dc_offset_v", 1.65) + params.get("amplitude_v", 1.4) * np.sin(2 * np.pi * f_sig * t)
        codes = self.dut.convert_array(stimulus, sample_rate_hz=fs)
        return self.engine.dynamic_characterization(codes, fs, f_sig, window=params.get("window", "blackmanharris"))

    def _run_noise(self, params: dict) -> NoiseResults:
        n = params.get("num_samples", 10_000)
        stimulus = np.full(n, params.get("dc_voltage", 1.65))
        codes = self.dut.convert_array(stimulus, sample_rate_hz=self.config.sample_rate_hz)
        return self.engine.noise_characterization(codes, self.config.sample_rate_hz)

    def _run_histogram(self, params: dict) -> HistogramResults:
        fs = self.config.sample_rate_hz
        n = params.get("num_samples", 500_000)
        f_sig = params.get("signal_freq_hz", 997)
        t = np.arange(n) / fs
        stimulus = params.get("dc_offset_v", 1.65) + params.get("amplitude_v", 1.4) * np.sin(2 * np.pi * f_sig * t)
        codes = self.dut.convert_array(stimulus, sample_rate_hz=fs)
        return self.engine.histogram_characterization(codes, input_type="sine")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from virtual_dut.adc_model import esp32_adc

    dut = esp32_adc()
    config = TestSequenceConfig.default_full_characterization()
    config.to_json("configs/full_characterization.json")

    seq = TestSequencer(dut, config)
    report = seq.run()

    print("\n" + "-" * 50)
    for param, r in report.pass_fail.items():
        if param != "OVERALL":
            status = "PASS" if r["pass"] else "FAIL"
            print(f"  {param:.<25} {r['measured']:.2f} {r['unit']} (limit: {r['limit']}) [{status}]")
    overall = report.pass_fail.get("OVERALL", {})
    print(f"\n  {'OVERALL PASS' if overall.get('pass') else 'OVERALL FAIL'}")
