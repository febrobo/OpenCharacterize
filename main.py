#!/usr/bin/env python3
"""
OpenCharacterize -- Mixed-Signal IC Characterization Platform

Usage:
    python main.py                      Full characterization (virtual ESP32)
    python main.py --dut stm32         Virtual STM32 ADC
    python main.py --dut ideal         Ideal reference ADC
    python main.py --config cfg.json   Custom test config
    python main.py --spc               SPC analysis (30 iterations)
    python main.py --compare           Side-by-side ESP32 vs STM32 vs Ideal
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from virtual_dut.adc_model import esp32_adc, stm32_adc, ideal_adc, VirtualADC
from tests.test_sequencer import TestSequencer, TestSequenceConfig
from calibration.cal_manager import CalibrationManager
from reporting.report_generator import ReportGenerator
from spc.statistical_process_control import SPCEngine

import numpy as np


logger = logging.getLogger("OpenCharacterize")


def setup_logging(verbose: bool = False):
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("opencharacterize.log", mode="a")],
    )


def get_dut(name: str) -> VirtualADC:
    duts = {"esp32": esp32_adc, "stm32": stm32_adc, "ideal": ideal_adc}
    if name not in duts:
        print(f"Unknown DUT: {name}. Options: {list(duts.keys())}")
        sys.exit(1)
    return duts[name]()


def run_characterization(dut: VirtualADC, config_path: str = None):
    print("=" * 56)
    print("  OpenCharacterize v1.0")
    print("  Mixed-Signal IC Characterization Platform")
    print("=" * 56)

    identity = dut.get_identity()
    print(f"  DUT:          {identity['model']} ({identity['serial']})")
    print(f"  Resolution:   {dut.spec.resolution_bits}-bit")
    print(f"  VREF:         {dut.spec.vref}V")
    print(f"  Architecture: {dut.spec.architecture.value}")
    print()

    # Phase 1: Calibration
    print("[1/3] Calibration Verification")
    print("-" * 40)
    cal = CalibrationManager(error_threshold_mv=15.0)
    cal_record = cal.verify_calibration(dut)
    if not cal_record.overall_pass:
        print("  WARNING: Calibration failed -- results may be unreliable\n")

    # Phase 2: Test execution
    print(f"\n[2/3] Test Execution")
    print("-" * 40)
    if config_path and Path(config_path).exists():
        config = TestSequenceConfig.from_json(config_path)
    else:
        config = TestSequenceConfig.default_full_characterization()

    seq = TestSequencer(dut, config)
    report = seq.run()

    # Phase 3: Report
    print(f"\n[3/3] Report Generation")
    print("-" * 40)
    gen = ReportGenerator()
    dut_name = identity["model"].lower().replace(" ", "_")
    path = gen.generate(report, filename=f"characterization_{dut_name}.html",
                        title=f"{identity['model']} Characterization Report")
    print(f"  Report: {path}")

    # Summary
    print(f"\n{'=' * 56}")
    print("  RESULTS")
    print("=" * 56)
    for param, r in report.pass_fail.items():
        if param == "OVERALL":
            continue
        status = "PASS" if r["pass"] else "FAIL"
        print(f"  {param:.<25} {r['measured']:>8.3f} {r['unit']:<5} "
              f"(limit: {r['limit']})  [{status}]")

    overall = report.pass_fail.get("OVERALL", {})
    print(f"\n  {'OVERALL PASS' if overall.get('pass') else 'OVERALL FAIL'}")
    return report


def run_comparison():
    print("=" * 56)
    print("  ADC Comparison: ESP32 vs STM32 vs Ideal")
    print("=" * 56)

    from analysis.characterization_engine import CharacterizationEngine, CharacterizationReport

    duts = {"ESP32 (12-bit SAR)": esp32_adc(), "STM32F4 (12-bit SAR)": stm32_adc(), "Ideal 12-bit": ideal_adc()}
    engine = CharacterizationEngine(resolution_bits=12, vref=3.3)
    results = {}

    for name, dut in duts.items():
        print(f"\n  Testing: {name}")
        # Full ramp -- engine trims to valid code range automatically
        ramp = np.linspace(0, 3.3, 200_000)
        static = engine.static_characterization(ramp, dut.convert_array(ramp))

        # Coherent sampling: M=83 (prime), N=8192, fs=100kHz
        # f_sig = 83 * 100000 / 8192 = 1013.18 Hz (exact integer cycles)
        fs = 100_000
        n_fft = 8192
        f_sig = 83 * fs / n_fft
        t = np.arange(n_fft) / fs
        sine = 1.65 + 1.4 * np.sin(2 * np.pi * f_sig * t)
        dynamic = engine.dynamic_characterization(dut.convert_array(sine, sample_rate_hz=fs), fs, f_sig)
        results[name] = {"static": static, "dynamic": dynamic}

    header = f"\n{'Parameter':<20} " + "".join(f"{n:<22}" for n in results)
    print(header)
    print("-" * len(header))

    metrics = [
        ("INL max (LSB)", lambda r: f"{max(abs(r['static'].inl_max), abs(r['static'].inl_min)):.2f}"),
        ("DNL max (LSB)", lambda r: f"{max(abs(r['static'].dnl_max), abs(r['static'].dnl_min)):.2f}"),
        ("Offset (mV)", lambda r: f"{r['static'].offset_error_mv:.1f}"),
        ("Gain Error (%)", lambda r: f"{r['static'].gain_error_pct:.2f}"),
        ("Missing Codes", lambda r: f"{len(r['static'].missing_codes)}"),
        ("SNR (dB)", lambda r: f"{r['dynamic'].snr_db:.1f}"),
        ("THD (dB)", lambda r: f"{r['dynamic'].thd_db:.1f}"),
        ("ENOB (bits)", lambda r: f"{r['dynamic'].enob:.2f}"),
        ("SFDR (dB)", lambda r: f"{r['dynamic'].sfdr_db:.1f}"),
    ]

    for label, fn in metrics:
        row = f"  {label:<20}" + "".join(f"{fn(r):<22}" for r in results.values())
        print(row)

    print(f"\n  Theoretical 12-bit: ENOB=12.00, SNR=74.0 dB (6.02N + 1.76)")

    gen = ReportGenerator()
    for name, r in results.items():
        rpt = CharacterizationReport(
            dut_id={"model": name}, static=r["static"], dynamic=r["dynamic"],
            pass_fail=engine.evaluate_pass_fail(r["static"], r["dynamic"], None,
                                                list(duts.values())[0].get_spec()),
        )
        safe = name.lower().replace(" ", "_").replace("(", "").replace(")", "")
        gen.generate(rpt, f"comparison_{safe}.html", f"{name} Characterization")

    print("\n  Reports saved to reports/")


def main():
    parser = argparse.ArgumentParser(description="OpenCharacterize")
    parser.add_argument("--dut", default="esp32", choices=["esp32", "stm32", "ideal"])
    parser.add_argument("--config", help="Test config JSON path")
    parser.add_argument("--spc", action="store_true", help="Run SPC analysis")
    parser.add_argument("--compare", action="store_true", help="Compare all DUTs")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    setup_logging(args.verbose)

    t0 = time.time()

    if args.compare:
        run_comparison()
    elif args.spc:
        from spc.statistical_process_control import run_spc_demo
        run_spc_demo()
    else:
        run_characterization(get_dut(args.dut), args.config)

    print(f"\n  Elapsed: {time.time() - t0:.2f}s")


if __name__ == "__main__":
    main()
