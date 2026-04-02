# OpenCharacterize

Open-source platform for automated ADC characterization with IEEE Std 1241-2010 methodology. Measures INL, DNL, SNR, THD, ENOB, SFDR using physics-based virtual instrumentation, automated test sequencing, statistical process control, and professional reporting.

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-19%2F19-brightgreen.svg)](tests/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Report](https://img.shields.io/badge/IEEE%20Report-PDF-red.svg)](docs/OpenCharacterize_Report.pdf)

---

## Results

Three virtual DUT profiles were characterized under identical conditions:

| Parameter | ESP32 | STM32F4 | Ideal | Unit |
|-----------|-------|---------|-------|------|
| INL (max) | 14.19 | 3.26 | 0.34 | LSB |
| DNL (max) | 2.85 | 0.75 | 0.33 | LSB |
| Offset Error | 24.7 | 2.4 | 0.1 | mV |
| Gain Error | -2.09 | -0.29 | -0.00 | % |
| Missing Codes | 4 | 0 | 0 | -- |
| SNR | 49.3 | 59.0 | 69.6 | dB |
| THD | -53.3 | -66.7 | -86.3 | dB |
| ENOB | 7.65 | 9.40 | 11.25 | bits |
| SFDR | 53.4 | 67.6 | 100.5 | dB |

*Theoretical 12-bit limit: ENOB = 12.00, SNR = 74.0 dB*

The ESP32 profile reproduces the well-documented poor ADC performance of the Espressif ESP32, achieving only 7.65 effective bits from a 12-bit converter. The ideal reference validates the measurement framework at 69.6 dB SNR against the 74.0 dB theoretical limit.

### INL and DNL (ESP32)

![INL and DNL](docs/fig_inl_dnl.png)

INL exhibits the parabolic bow from SAR capacitor mismatch (-14.19 to +11.36 LSB). DNL shows code-to-code variation with 4 missing codes at the midscale MSB transition and binary-weighted boundaries.

### FFT Spectrum (ESP32)

![FFT Spectrum](docs/fig_fft.png)

Coherent-sampled sine at 1013 Hz. Fundamental at 0 dBFS, H2 dominant at -50 dB, noise floor at -80 to -100 dBFS.

### Noise Distribution (ESP32)

![Noise Distribution](docs/fig_noise.png)

DC input at 1.65 V. RMS noise = 3.94 LSB. Approximately Gaussian, dominated by thermal (kT/C) noise.

---

## Overview

OpenCharacterize implements the full ADC characterization workflow:

**Static characterization** -- INL, DNL, offset error, gain error, missing codes, and monotonicity via ramp test with histogram-based analysis. Trims to valid code range automatically, excluding dead-zone rail codes.

**Dynamic characterization** -- SNR, THD, SINAD, SFDR, ENOB via windowed FFT with coherent sampling (M=83, N=8192, fs=100 kHz) to eliminate spectral leakage.

**Noise analysis** -- RMS noise, peak-to-peak, spectral decomposition, and Gaussianity testing with DC input.

**Histogram analysis** -- Code density method per Analog Devices AN-835 using sine-wave input probability distribution.

**Statistical process control** -- Process capability (Cpk), Gage R&R measurement system analysis, X-bar/R control charts with Western Electric rules. 30-iteration characterization showed Cpk = -33.8 for ENOB, confirming the ESP32 cannot meet its own 9-bit ENOB specification.

**Calibration management** -- Pre-test verification against known references with drift tracking, history database, and recalibration alerts.

**Reporting** -- HTML reports with embedded matplotlib plots, pass/fail summary tables, and full measurement data.

### Virtual ADC Model

The platform includes a physics-based virtual ADC that models real-world SAR non-idealities from first principles, enabling complete characterization without physical hardware:

- Parabolic INL bow from systematic capacitor mismatch
- Binary-weighted periodic INL from bit-transition discontinuities
- Thermal noise (kT/C), flicker noise (1/f), and power supply coupling
- Input dead zones and saturation clamping
- Configurable offset error, gain error, and missing codes

Three pre-built profiles (ESP32, STM32F4, Ideal) are calibrated against published performance data.

---

## Quick Start

```bash
git clone https://github.com/febrobo/OpenCharacterize.git
cd OpenCharacterize
pip install numpy scipy plotly matplotlib

# Full characterization (virtual ESP32)
python main.py

# Compare ESP32 vs STM32 vs Ideal
python main.py --compare

# SPC analysis (30 iterations, Cpk, control charts)
python main.py --spc

# Characterize a specific DUT
python main.py --dut stm32

# Run tests
PYTHONPATH=src python -m pytest tests/ -v
```

---

## Architecture

```
                        HOST (Python)
  +--------------+   +---------------+   +------------------+
  | Test Engine  |-->| Analysis      |-->| Reporting & SPC  |
  |              |   | Engine        |   |                  |
  | - Sequencer  |   | - INL/DNL     |   | - HTML + plots   |
  | - JSON config|   | - SNR/THD     |   | - Cpk, Gage R&R  |
  | - Cal verify |   | - ENOB/SFDR   |   | - Control charts |
  |              |   | - FFT/Hist    |   | - Pass/fail      |
  +--------------+   +---------------+   +------------------+
         |
         |  Python API (virtual) or Serial/TCP (hardware)
         |
  +------v-------------------------------------------+
  | DUT Layer                                        |
  |                                                  |
  | Virtual ADC (physics model)   Real HW (future)   |
  | - Configurable noise          - SCPI over serial |
  | - INL/DNL profiles            - MCP4725 DAC      |
  | - Dead zones, missing codes   - Power monitor    |
  +--------------------------------------------------+
```

---

## Repository Structure

```
OpenCharacterize/
|-- main.py                                 Entry point
|-- requirements.txt
|-- LICENSE
|-- docs/
|   |-- OpenCharacterize_Report.pdf         IEEE technical report
|   |-- fig_inl_dnl.png                     INL/DNL characterization plot
|   |-- fig_fft.png                         FFT spectrum plot
|   +-- fig_noise.png                       Noise distribution plot
|-- src/
|   |-- virtual_dut/adc_model.py            Virtual ADC with non-idealities
|   |-- analysis/characterization_engine.py IEEE 1241 parameter extraction
|   |-- tests/test_sequencer.py             JSON-configurable test execution
|   |-- spc/statistical_process_control.py  Cpk, Gage R&R, control charts
|   |-- calibration/cal_manager.py          Cal verification & drift tracking
|   +-- reporting/report_generator.py       HTML reports with matplotlib
|-- configs/
|   +-- full_characterization.json          Default test sequence
|-- reports/                                Generated HTML reports
|-- data/                                   Calibration history
+-- tests/
    +-- test_characterization.py            19 unit tests
```

---

## Technical Report

A full IEEE-format technical report is included at [`docs/OpenCharacterize_Report.pdf`](docs/OpenCharacterize_Report.pdf), covering the virtual ADC model, characterization methodology, all measurement results, statistical process control analysis, and discussion of coherent sampling and dead-zone handling.

---

## Design Decisions

**Virtual ADC over hardware simulator.** MCU simulators (Wokwi, QEMU) model ideal ADCs with no noise or nonlinearity. The whole point of characterization is measuring imperfections, so the virtual DUT injects physically-motivated errors: parabolic INL from capacitor mismatch, periodic spikes at binary transitions, thermal and 1/f noise, supply coupling, and dead zones.

**Coherent sampling.** Non-coherent FFT measurements initially produced 50 dB SNR for the ideal ADC instead of the expected 74 dB. With the signal-to-noise ratio exceeding 10^7 in power, even 0.01% spectral leakage overwhelmed the noise floor. Using f_signal = M * f_s / N with M = 83 (prime) recovered 69.6 dB.

**Dead-zone handling.** Rail codes (0 and 4095) accumulate dead-zone and saturation hits that distort histogram-based INL/DNL. The engine trims analysis to interior codes with non-zero counts, following standard production practice.

**IEEE 1241 methodology.** INL/DNL via histogram method, SNR/THD via coherent-sampled FFT with Blackman-Harris windowing, process capability via Cpk.

---

## Future Work

- Real hardware integration via SCPI protocol (ESP32 + MCP4725 DAC, under $30)
- PSRR measurement through supply noise injection
- Temperature sweep characterization
- Multi-channel correlation analysis

---

## Dependencies

```
numpy>=1.24
scipy>=1.10
plotly>=5.15
matplotlib>=3.7
```

---

## License

MIT

---

*Febin Wilson -- [febin.wilson777@gmail.com](mailto:febin.wilson777@gmail.com) -- [febrobo.github.io](https://febrobo.github.io)*
