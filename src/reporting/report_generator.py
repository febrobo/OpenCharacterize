"""
HTML report generator for characterization results.
Produces interactive Plotly-based reports with pass/fail summary,
INL/DNL plots, FFT spectrum, noise histogram, and measurement tables.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    HAS_PLOTLY = True
except ImportError:
    HAS_PLOTLY = False

from analysis.characterization_engine import CharacterizationReport


class ReportGenerator:

    def __init__(self, output_dir: str = "reports"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate(
        self, report: CharacterizationReport,
        filename: Optional[str] = None,
        title: str = "ADC Characterization Report",
    ) -> str:
        if filename is None:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"characterization_{ts}.html"

        filepath = self.output_dir / filename
        html = self._build_plotly_report(report, title) if HAS_PLOTLY else self._build_fallback(report, title)
        filepath.write_text(html, encoding="utf-8")
        return str(filepath)

    def _build_plotly_report(self, report: CharacterizationReport, title: str) -> str:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import base64
        from io import BytesIO

        plots_html = ""

        def fig_to_base64(fig):
            buf = BytesIO()
            fig.savefig(buf, format='png', dpi=150, bbox_inches='tight',
                        facecolor='#0a0a0a', edgecolor='none')
            plt.close(fig)
            buf.seek(0)
            return base64.b64encode(buf.read()).decode('utf-8')

        plt_style = {
            'axes.facecolor': '#0a0a0a', 'figure.facecolor': '#0a0a0a',
            'axes.edgecolor': '#333', 'axes.labelcolor': '#ccc',
            'text.color': '#ccc', 'xtick.color': '#999', 'ytick.color': '#999',
            'grid.color': '#222', 'grid.alpha': 0.5,
        }

        if report.static:
            inl = report.static.inl
            dnl = report.static.dnl
            nz = np.where((inl != 0) | (dnl != 0))[0]
            if len(nz) > 0:
                lo, hi = nz[0], nz[-1]
                codes = np.arange(lo, hi + 1)
                inl_v = inl[lo:hi + 1]
                dnl_v = dnl[lo:hi + 1]

                with plt.rc_context(plt_style):
                    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), gridspec_kw={'hspace': 0.35})

                    ax1.fill_between(codes, inl_v, 0, color='#00b4d8', alpha=0.4)
                    ax1.plot(codes, inl_v, color='#00b4d8', linewidth=0.8)
                    ax1.axhline(0, color='#555', linestyle='--', linewidth=0.8)
                    ax1.set_title('Integral Nonlinearity (INL)', color='#eee', fontsize=13)
                    ax1.set_ylabel('LSB', fontsize=11)
                    ax1.set_xlim(lo, hi)
                    ax1.grid(True)

                    ax2.fill_between(codes, dnl_v, 0, color='#e63946', alpha=0.4)
                    ax2.plot(codes, dnl_v, color='#e63946', linewidth=0.8)
                    ax2.axhline(0, color='#555', linestyle='--', linewidth=0.8)
                    ax2.axhline(-1, color='#ff6b6b', linestyle=':', linewidth=1, label='Missing code')
                    ax2.set_title('Differential Nonlinearity (DNL)', color='#eee', fontsize=13)
                    ax2.set_xlabel('Code', fontsize=11)
                    ax2.set_ylabel('LSB', fontsize=11)
                    ax2.set_xlim(lo, hi)
                    ax2.legend(loc='upper right', fontsize=9)
                    ax2.grid(True)

                b64 = fig_to_base64(fig)
                plots_html += f'<div class="plot"><img src="data:image/png;base64,{b64}" style="width:100%;border-radius:8px;"></div>'

        if report.dynamic:
            freqs_khz = report.dynamic.fft_freqs / 1000
            mag = report.dynamic.fft_magnitude
            peak_db = float(np.max(mag[1:])) if len(mag) > 1 else 0

            with plt.rc_context(plt_style):
                fig, ax = plt.subplots(figsize=(10, 4))
                ax.plot(freqs_khz, mag, color='#00b4d8', linewidth=0.6)
                ax.fill_between(freqs_khz, mag, peak_db - 120, color='#00b4d8', alpha=0.15)
                ax.set_ylim(peak_db - 120, peak_db + 10)
                ax.set_xlim(0, report.dynamic.sample_rate_hz / 2000)

                # Mark fundamental
                fund_bin = int(round(report.dynamic.signal_freq_hz *
                                     report.dynamic.num_samples / report.dynamic.sample_rate_hz))
                if fund_bin < len(mag):
                    ax.annotate(f'f0 ({report.dynamic.signal_freq_hz:.0f}Hz)',
                                xy=(freqs_khz[fund_bin], mag[fund_bin]),
                                xytext=(freqs_khz[fund_bin] + 2, mag[fund_bin] - 5),
                                color='#00ff88', fontsize=9,
                                arrowprops=dict(arrowstyle='->', color='#00ff88'))

                for h_idx, h_db in enumerate(report.dynamic.harmonics_db[:5], start=2):
                    h_bin = fund_bin * h_idx
                    if h_bin < len(mag) and h_db > -100:
                        ax.annotate(f'H{h_idx}', xy=(freqs_khz[h_bin], mag[h_bin]),
                                    xytext=(freqs_khz[h_bin] + 0.5, mag[h_bin] + 5),
                                    color='#ff6b6b', fontsize=8,
                                    arrowprops=dict(arrowstyle='->', color='#ff6b6b'))

                ax.set_title(f'FFT Spectrum  |  SNR={report.dynamic.snr_db:.1f}dB  '
                             f'THD={report.dynamic.thd_db:.1f}dB  ENOB={report.dynamic.enob:.2f}',
                             color='#eee', fontsize=13)
                ax.set_xlabel('Frequency (kHz)', fontsize=11)
                ax.set_ylabel('Magnitude (dBFS)', fontsize=11)
                ax.grid(True)

            b64 = fig_to_base64(fig)
            plots_html += f'<div class="plot"><img src="data:image/png;base64,{b64}" style="width:100%;border-radius:8px;"></div>'

        if report.noise:
            with plt.rc_context(plt_style):
                fig, ax = plt.subplots(figsize=(10, 3.5))
                ax.bar(report.noise.histogram_bins[:-1], report.noise.histogram,
                       width=0.8, color='#48cae4', alpha=0.8)
                ax.set_title(f'Noise Distribution  |  RMS={report.noise.rms_noise_lsb:.2f} LSB  '
                             f'Gaussian={"Yes" if report.noise.is_gaussian else "No"}',
                             color='#eee', fontsize=13)
                ax.set_xlabel('ADC Code', fontsize=11)
                ax.set_ylabel('Count', fontsize=11)
                ax.grid(True)

            b64 = fig_to_base64(fig)
            plots_html += f'<div class="plot"><img src="data:image/png;base64,{b64}" style="width:100%;border-radius:8px;"></div>'

        pf_rows = ""
        for param, r in report.pass_fail.items():
            if param == "OVERALL":
                continue
            bg = "#2d6a4f22" if r["pass"] else "#9d020822"
            color = "#2d6a4f" if r["pass"] else "#9d0208"
            label = "PASS" if r["pass"] else "FAIL"
            pf_rows += f"""<tr style="background:{bg}">
                <td>{param}</td><td>{r['measured']:.3f}</td>
                <td>{r['limit']}</td><td>{r['unit']}</td>
                <td style="color:{color};font-weight:bold">{label}</td></tr>"""

        overall = report.pass_fail.get("OVERALL", {})
        v_color = "#2d6a4f" if overall.get("pass") else "#9d0208"
        v_text = "PASS" if overall.get("pass") else "FAIL"

        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><title>{title}</title>
<script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
     background:#0a0a0a;color:#e0e0e0;max-width:1200px;margin:0 auto;padding:2rem}}
h1{{color:#00b4d8;font-size:1.8rem;margin-bottom:.5rem}}
h2{{color:#48cae4;font-size:1.2rem;margin:2rem 0 1rem;border-bottom:1px solid #333;padding-bottom:.5rem}}
.meta{{color:#888;font-size:.85rem;margin-bottom:2rem}}
.verdict{{display:inline-block;padding:.5rem 2rem;border-radius:8px;font-size:1.4rem;font-weight:bold;
         margin:1rem 0;background:{v_color}22;color:{v_color};border:2px solid {v_color}}}
table{{width:100%;border-collapse:collapse;margin:1rem 0}}
th{{background:#1a1a2e;color:#48cae4;padding:.6rem;text-align:left}}
td{{padding:.6rem;border-bottom:1px solid #222}}
.plot{{margin:1.5rem 0;border:1px solid #222;border-radius:8px;overflow:hidden}}
.footer{{margin-top:3rem;padding-top:1rem;border-top:1px solid #333;color:#555;font-size:.8rem}}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="meta">
<p>Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
<p>DUT: {json.dumps(report.dut_id)}</p>
<p>Platform: OpenCharacterize v1.0</p>
</div>
<div class="verdict">{v_text}</div>

<h2>Pass/Fail Summary</h2>
<table><thead><tr><th>Parameter</th><th>Measured</th><th>Limit</th><th>Unit</th><th>Result</th></tr></thead>
<tbody>{pf_rows}</tbody></table>

<h2>Static Characterization</h2>
{self._static_table(report.static) if report.static else '<p>Not performed</p>'}
{plots_html}
<h2>Dynamic Characterization</h2>
{self._dynamic_table(report.dynamic) if report.dynamic else '<p>Not performed</p>'}
<h2>Noise Characterization</h2>
{self._noise_table(report.noise) if report.noise else '<p>Not performed</p>'}

<div class="footer">
<p>OpenCharacterize -- Mixed-Signal IC Characterization Platform</p>
<p>Methodology per IEEE Std 1241-2010</p>
</div>
</body></html>"""

    def _static_table(self, s) -> str:
        mc = ', '.join(map(str, s.missing_codes[:10])) if s.missing_codes else 'None'
        mono = "Yes" if s.monotonic else "No"
        return f"""<table>
<tr><td>INL (peak)</td><td>{s.inl_min:.2f} to {s.inl_max:.2f} LSB</td></tr>
<tr><td>DNL (peak)</td><td>{s.dnl_min:.2f} to {s.dnl_max:.2f} LSB</td></tr>
<tr><td>Offset Error</td><td>{s.offset_error_mv:.1f} mV ({s.offset_error_lsb:.1f} LSB)</td></tr>
<tr><td>Gain Error</td><td>{s.gain_error_pct:.2f}%</td></tr>
<tr><td>Missing Codes</td><td>{len(s.missing_codes)} ({mc})</td></tr>
<tr><td>Monotonic</td><td>{mono}</td></tr>
<tr><td>Codes Tested</td><td>{s.num_codes_tested} / 4096</td></tr></table>"""

    def _dynamic_table(self, d) -> str:
        return f"""<table>
<tr><td>Signal Frequency</td><td>{d.signal_freq_hz:.0f} Hz</td></tr>
<tr><td>Sample Rate</td><td>{d.sample_rate_hz:.0f} Hz</td></tr>
<tr><td>FFT Size</td><td>{d.num_samples} points</td></tr>
<tr><td>SNR</td><td>{d.snr_db:.1f} dB</td></tr>
<tr><td>THD</td><td>{d.thd_db:.1f} dB</td></tr>
<tr><td>SINAD</td><td>{d.sinad_db:.1f} dB</td></tr>
<tr><td>SFDR</td><td>{d.sfdr_db:.1f} dB</td></tr>
<tr><td>ENOB</td><td>{d.enob:.2f} bits</td></tr>
<tr><td>Theoretical ENOB</td><td>12.00 bits (12-bit ideal)</td></tr></table>"""

    def _noise_table(self, n) -> str:
        dist = "Gaussian" if n.is_gaussian else "Non-Gaussian"
        return f"""<table>
<tr><td>RMS Noise</td><td>{n.rms_noise_lsb:.2f} LSB ({n.rms_noise_uv:.0f} uV)</td></tr>
<tr><td>Peak-to-Peak</td><td>{n.pp_noise_lsb:.0f} LSB</td></tr>
<tr><td>Mean Code</td><td>{n.mean_code:.1f}</td></tr>
<tr><td>Distribution</td><td>{dist}</td></tr></table>"""

    def _build_fallback(self, report, title):
        pf = ""
        for p, r in report.pass_fail.items():
            if p != "OVERALL":
                pf += f"<tr><td>{p}</td><td>{r['measured']:.3f}</td><td>{r['limit']}</td><td>{'PASS' if r['pass'] else 'FAIL'}</td></tr>"
        return f"""<!DOCTYPE html><html><head><title>{title}</title>
<style>body{{font-family:monospace;background:#111;color:#ccc;padding:2rem}}
table{{border-collapse:collapse;margin:1rem 0}}td,th{{border:1px solid #333;padding:.5rem}}</style></head>
<body><h1>{title}</h1><p>{datetime.now()}</p>
<table><tr><th>Parameter</th><th>Measured</th><th>Limit</th><th>Result</th></tr>{pf}</table>
<p>Install plotly for interactive charts: pip install plotly</p></body></html>"""
