# OpenCharacterize

Production-grade open-source platform for characterizing mixed-signal IC performance. Measures INL, DNL, SNR, THD, ENOB, SFDR with automated test sequencing, statistical process control, and professional reporting.

[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## Overview

OpenCharacterize automates the full ADC/DAC characterization workflow using IEEE Std 1241 methodology:

- **Static:** INL, DNL, offset error, gain error, missing codes, monotonicity
- **Dynamic:** SNR, THD, SINAD, SFDR, ENOB via windowed FFT
- **Noise:** RMS noise, peak-to-peak, spectral decomposition, Gaussianity test
- **Histogram:** Code density analysis (sine histogram method per AN-835)
- **SPC:** Process capability (Cpk), Gage R&R, X-bar/R control charts, drift detection
- **Calibration:** Automated verification, history tracking, recalibration alerts

### No Hardware Required

The platform includes a physics-based virtual ADC model with configurable non-idealities. Three pre-built profiles cover a range of real-world behavior:

| Profile | Description | Typical ENOB |
|---------|-------------|--------------|
| `esp32` | ESP32 12-bit SAR -- poor linearity, high noise | ~9.5 |
| `stm32` | STM32F4-class 12-bit SAR -- solid precision | ~10.5 |
| `ideal` | Perfect 12-bit reference -- quantization noise only | ~11.9 |

The virtual ADC models thermal noise (kT/C), flicker noise (1/f), supply coupling, capacitor mismatch, dead zones, and missing codes based on published characterization data.

---

## Quick Start

```bash
git clone https://github.com/febrobo/OpenCharacterize.git
cd OpenCharacterize
pip install -r requirements.txt

# Full characterization
python main.py

# Compare all three ADCs side-by-side
python main.py --compare

# SPC analysis (30 iterations, Cpk, Gage R&R)
python main.py --spc

# Characterize a specific DUT
python main.py --dut stm32
```

Reports are generated as interactive HTML files in `reports/`.

---

## Architecture

```
                        HOST (Python)
  +--------------+   +---------------+   +------------------+
  | Test Engine  |-->| Analysis      |-->| Reporting & SPC  |
  |              |   | Engine        |   |                  |
  | - Sequencer  |   | - INL/DNL     |   | - HTML + Plotly  |
  | - Config     |   | - SNR/THD     |   | - Cpk, Gage R&R  |
  | - Logging    |   | - ENOB/SFDR   |   | - Control charts |
  | - Cal verify |   | - FFT/Hist    |   | - Pass/fail      |
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
|-- main.py
|-- requirements.txt
|-- LICENSE
|-- src/
|   |-- virtual_dut/
|   |   +-- adc_model.py               Virtual ADC with non-idealities
|   |-- analysis/
|   |   +-- characterization_engine.py  IEEE 1241 parameter extraction
|   |-- tests/
|   |   +-- test_sequencer.py           JSON-configurable test execution
|   |-- spc/
|   |   +-- statistical_process_control.py  Cpk, Gage R&R, control charts
|   |-- calibration/
|   |   +-- cal_manager.py             Cal verification & drift tracking
|   +-- reporting/
|       +-- report_generator.py         Interactive HTML reports
|-- configs/
|   +-- full_characterization.json
|-- reports/
|-- data/
|-- tests/
|   |-- test_characterization.py
|   +-- test_spc.py
+-- .github/
    +-- workflows/ci.yml
```

---

## Sample Results (ESP32 Virtual ADC)

| Parameter | Measured | Spec Limit | Result |
|-----------|----------|------------|--------|
| INL (max) | ~8.5 LSB | 12.0 LSB | PASS |
| DNL (max) | ~1.2 LSB | 2.0 LSB | PASS |
| SNR | ~56 dB | 55.0 dB | PASS |
| THD | ~-58 dB | -55.0 dB | PASS |
| ENOB | ~9.3 bits | 9.0 bits | PASS |
| Offset | ~12 mV | 40 mV | PASS |
| Missing Codes | 3 | 0 | FAIL |

The virtual model reproduces the ESP32's known non-linearities, noise characteristics, and dead zones.

---

## Design Decisions

**Virtual ADC over hardware simulator.** MCU simulators (Wokwi, QEMU) model ideal ADCs with no noise or nonlinearity. The whole point of characterization is measuring imperfections, so the virtual DUT injects physically-motivated errors: parabolic INL from capacitor mismatch, periodic spikes at binary transitions, thermal and 1/f noise, supply coupling, and dead zones.

**IEEE 1241 methodology.** INL/DNL via histogram method, SNR/THD via coherent-sampled FFT with Blackman-Harris windowing, process capability via Cpk. Standard methodology used across the semiconductor industry.

**Swappable DUT layer.** The architecture decouples the characterization framework from the data source. Swapping in real hardware means implementing the same `convert_array()` interface over SCPI serial -- no changes to the analysis or reporting code.

---

## Future: Real Hardware Support

The interface is designed for drop-in hardware support:

```python
# Virtual (current)
from virtual_dut.adc_model import esp32_adc
dut = esp32_adc()
codes = dut.convert_array(voltages)

# Real hardware (same API)
from instruments.scpi_client import SCPIInstrument
dut = SCPIInstrument(port="/dev/ttyUSB0", baudrate=115200)
codes = dut.query_array("MEAS:ADC:ALL?")
```

Hardware BOM (under $30): ESP32 DevKit, MCP4725 I2C DAC, second ESP32 for power rail monitoring.

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

*[Febin](https://febrobo.github.io) -- Robotics Engineer, Northeastern University*
