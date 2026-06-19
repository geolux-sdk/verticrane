# coding:UTF-8
# Web dashboard to review tilt-log measurements.
#
#   streamlit run dashboard.py
#
# Pick a CSV from data/ (or upload one) and view summary stats, time series,
# the slope distribution, and the sway spectrum (FFT). Can also start a new
# measurement, which launches log_tilt.py as a separate process (the dashboard
# itself never opens the serial port).

import glob
import os
import subprocess
import sys
import tempfile
import time

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import analyze_tilt
import app_config
import read_status

DATA_DIR = "data"

# Judgment criteria are editable by the admin on the hidden /setup page.
_cfg = app_config.load()
SLOPE_THRESHOLD_PCT = float(_cfg["slope_threshold_pct"])  # tilt alarm threshold (%)
MA_SECONDS = float(_cfg["ma_seconds"])                    # moving-average window (s)
MA_LABEL = "{0:g}초".format(MA_SECONDS)                   # e.g. "1초", "0.5초"

# Korean labels for the sensor-status panel, keyed by read_status.STATUS_LAYOUT keys.
STATUS_GROUP_LABELS = {
    "identity": "식별 / 통신",
    "mode": "동작 모드",
    "filters": "필터",
}
STATUS_FIELD_LABELS = {
    "fw_version": "펌웨어 버전",
    "serial_number": "시리얼 번호",
    "modbus_addr": "Modbus 주소",
    "baud_rate": "통신 속도",
    "rs485_delay": "RS485 응답 지연",
    "work_mode": "작동 모드",
    "bandwidth": "대역폭",
    "gyro_range": "자이로 범위",
    "acc_range": "가속도 범위",
    "algorithm": "알고리즘",
    "installation": "설치 방향",
    "power_state": "전원 상태",
    "filter_k": "동적 필터(K)",
    "acc_filter": "가속도 필터",
}


def list_csvs():
    return sorted(glob.glob(os.path.join(DATA_DIR, "*.csv")))


LOGGER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log_tilt.py")
LOGGER_OUT = os.path.join(tempfile.gettempdir(), "verticrane_logger.out")


def logger_running():
    p = st.session_state.get("logger_proc")
    return p is not None and p.poll() is None


def start_logger(minutes):
    # Remember the existing files so we can spot the new one this run creates.
    st.session_state["logger_before"] = set(list_csvs())
    out = open(LOGGER_OUT, "w")
    st.session_state["logger_proc"] = subprocess.Popen(
        [sys.executable, LOGGER_SCRIPT, str(minutes)],
        cwd=os.path.dirname(LOGGER_SCRIPT), stdout=out, stderr=subprocess.STDOUT)
    out.close()  # the child keeps its own dup of the handle
    st.session_state["was_running"] = True


def stop_logger():
    p = st.session_state.get("logger_proc")
    if p is not None and p.poll() is None:
        p.terminate()


def newest_active_csv():
    before = st.session_state.get("logger_before", set())
    new = sorted(set(list_csvs()) - before)
    return new[-1] if new else None


@st.dialog("센서 상태 정보", width="large")
def device_status_dialog():
    # Modal popup; opened by calling this function after a status read.
    status = st.session_state.get("device_status")
    if status is None:
        st.error("센서에 연결하지 못했습니다. 포트 연결과 전원을 확인하고, "
                 "측정 중이라면 정지한 뒤 다시 시도하세요.")
        return
    st.caption("{0} · {1} bps · 읽은 시각 {2}".format(
        status["port"], status["baud"], st.session_state.get("device_status_time", "-")))
    cols = st.columns(len(read_status.STATUS_LAYOUT))
    values = status["values"]
    for col, (gkey, gtitle, fields) in zip(cols, read_status.STATUS_LAYOUT):
        with col:
            st.markdown("**{0}**".format(STATUS_GROUP_LABELS.get(gkey, gtitle)))
            rows = [{"항목": STATUS_FIELD_LABELS.get(fkey, flabel), "값": values.get(fkey, "-")}
                    for fkey, flabel in fields]
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def csv_rows(path):
    try:
        with open(path) as fh:
            return max(sum(1 for _ in fh) - 1, 0)
    except Exception:
        return 0


def moving_average(x, w):
    # Causal trailing average: each point is the mean of the previous w samples.
    x = np.asarray(x, dtype=float)
    if w <= 1 or len(x) < w:
        return x
    c = np.cumsum(np.insert(x, 0, 0.0))
    return (c[w:] - c[:-w]) / w


@st.cache_data
def read_csv(path, _mtime):
    # _mtime busts the cache when the file changes.
    return pd.read_csv(path)


st.set_page_config(page_title="기울기 로그 대시보드", layout="wide")
st.sidebar.title("기울기 로그")

# --- Measurement control: run the logger as a separate process ---
st.sidebar.header("측정")
minutes = st.sidebar.number_input("측정 시간 (분)", min_value=0.1, max_value=120.0,
                                  value=10.0, step=1.0)
b_start, b_stop = st.sidebar.columns(2)
if b_start.button("시작", disabled=logger_running(), width="stretch"):
    start_logger(minutes)
    st.rerun()
if b_stop.button("정지", disabled=not logger_running(), width="stretch"):
    stop_logger()
    st.rerun()


@st.fragment(run_every=2)
def measurement_status():
    # Rendered inside a `with st.sidebar` block, so use plain st.* here.
    if logger_running():
        active = newest_active_csv()
        st.info("측정 중... 샘플 {0}개".format(csv_rows(active) if active else 0))
    elif st.session_state.get("was_running"):
        # Just finished: full refresh so the new file appears in the list below.
        st.session_state["was_running"] = False
        st.rerun()


with st.sidebar:
    measurement_status()

with st.sidebar.expander("로거 출력"):
    try:
        with open(LOGGER_OUT) as fh:
            st.code(fh.read()[-2000:] or "(비어 있음)")
    except FileNotFoundError:
        st.caption("아직 실행된 로거가 없습니다")

# --- Sensor status: read the device configuration on demand ---
# The serial port is single-owner, so this is disabled while a measurement holds it.
st.sidebar.header("센서 정보")
if st.sidebar.button("센서 상태 읽기", disabled=logger_running(), width="stretch"):
    with st.spinner("센서 상태를 읽는 중... (몇 초 걸릴 수 있습니다)"):
        st.session_state["device_status"] = read_status.read_device_status()
    st.session_state["device_status_time"] = time.strftime("%H:%M:%S")
    device_status_dialog()
if logger_running():
    st.sidebar.caption("측정 중에는 상태를 읽을 수 없습니다 (포트 사용 중)")

# --- Choose a data source: a file from data/ or an upload ---
# While measuring, hide the in-progress file (it is still being written).
active_file = newest_active_csv() if logger_running() else None
review_files = [f for f in list_csvs() if f != active_file]
selected = st.sidebar.selectbox(
    "data/ 파일 목록", review_files, index=len(review_files) - 1,
    format_func=os.path.basename) if review_files else None
uploaded = st.sidebar.file_uploader("또는 CSV 업로드", type=["csv"])
st.sidebar.caption("임계값: {0}% (1/1000)".format(SLOPE_THRESHOLD_PCT))

path = None
if uploaded is not None:
    # Save uploads into data/ (assuming unique names) so they appear in the list and
    # get a .txt report, like a logged measurement. Done once: skip if it exists, so
    # Streamlit's per-interaction reruns don't re-save and re-analyze every time.
    path = os.path.join(DATA_DIR, uploaded.name)
    if not os.path.exists(path):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(path, "wb") as f:
            f.write(uploaded.getbuffer())
        txt_path = os.path.splitext(path)[0] + ".txt"
        with open(txt_path, "w", encoding="utf-8") as fh:
            fh.write(analyze_tilt.analyze(path) + "\n")
        st.toast("업로드 파일을 data/에 저장하고 리포트를 생성했습니다: {0}".format(uploaded.name))
elif selected:
    path = selected

if path is None:
    st.info("데이터가 없습니다. 측정을 실행(python log_tilt.py)하거나 CSV를 업로드하세요.")
    st.stop()

df = read_csv(path, os.path.getmtime(path))
label = os.path.basename(path)
st.title("기울기 로그 분석")
st.caption(label)

# --- Derived series ---
t = df["elapsed_s"].to_numpy()
roll = df["Roll_deg"].to_numpy()
pitch = df["Pitch_deg"].to_numpy()
slope = df["slope_pct"].to_numpy()
n = len(df)
dur = float(t[-1] - t[0]) if n > 1 else 0.0
fs = (n - 1) / dur if dur > 0 else 25.0

# --- Moving average (window from config); its max is the uprightness metric ---
ma_win = max(1, int(round(MA_SECONDS * fs)))
slope_ma = moving_average(slope, ma_win)
slope_peak = float(np.nanmax(slope_ma)) if len(slope_ma) else float("nan")

# Resultant tilt angle (deg) from the slope %; always >= 0 (it is a magnitude).
tilt = np.degrees(np.arctan(slope / 100.0))
tilt_ma = np.degrees(np.arctan(slope_ma / 100.0))

# --- Summary metrics ---
cols = st.columns(6)
cols[0].metric("샘플 수", n)
cols[1].metric("샘플링 (Hz)", "{0:.1f}".format(fs))
cols[2].metric("Roll 표준편차 (deg)", "{0:.4f}".format(np.nanstd(roll)))
cols[3].metric("Pitch 표준편차 (deg)", "{0:.4f}".format(np.nanstd(pitch)))
cols[4].metric("기울기 평균 (%)", "{0:.4f}".format(np.nanmean(slope)))
cols[5].metric("기울기 {0}평균 피크 (%)".format(MA_LABEL), "{0:.4f}".format(slope_peak))

if slope_peak > SLOPE_THRESHOLD_PCT:
    st.warning("{0} 평균 기울기 피크 {1:.4f}%가 임계값 {2}%를 초과했습니다".format(
        MA_LABEL, slope_peak, SLOPE_THRESHOLD_PCT))
else:
    st.success("{0} 평균 기울기 피크 {1:.4f}%가 임계값 {2}% 이내입니다".format(
        MA_LABEL, slope_peak, SLOPE_THRESHOLD_PCT))

# --- Time series: resultant tilt, read on both deg (left) and % (right) axes ---
ts = make_subplots(specs=[[{"secondary_y": True}]])
ts.add_trace(go.Scatter(x=t, y=tilt, name="기울기", line=dict(width=1, color="crimson")),
             secondary_y=False)
if len(tilt_ma):
    # Align x to the actual moving-average length: the window-1 trailing offset
    # in the normal case, or the full series when the log is shorter than ma_win.
    ts.add_trace(go.Scatter(x=t[-len(tilt_ma):], y=tilt_ma, name="기울기 {0}평균".format(MA_LABEL),
                            line=dict(width=2, color="darkred")), secondary_y=False)
# Invisible anchor on the right axis so its % ticks and the 0.1% line render
# (tilt and slope are the same curve, so no separate visible line is drawn).
ts.add_trace(go.Scatter(x=t, y=slope, showlegend=False, hoverinfo="skip",
                        line=dict(width=0)), secondary_y=True)
ts.add_hline(y=SLOPE_THRESHOLD_PCT, line=dict(color="red", dash="dash"),
             secondary_y=True, annotation_text="{0}%".format(SLOPE_THRESHOLD_PCT))
ts.update_xaxes(title_text="경과 시간 (s)")
# Tilt is a magnitude (>= 0). Slope % = tan(angle)*100, so link the right (%) axis to
# the left (deg) axis: the same plot height is the same physical tilt on both scales.
ANGLE_RANGE_DEG = 2.0
slope_eq_pct = float(np.tan(np.radians(ANGLE_RANGE_DEG)) * 100.0)
ts.update_yaxes(title_text="기울기 (deg)", range=[0, ANGLE_RANGE_DEG], secondary_y=False)
ts.update_yaxes(title_text="기울기 (%)", range=[0, slope_eq_pct], secondary_y=True)
ts.update_layout(height=420, margin=dict(t=30, b=10), legend=dict(orientation="h"))
st.subheader("시계열")
st.plotly_chart(ts, width="stretch")

left, right = st.columns(2)

# --- Slope distribution ---
with left:
    st.subheader("기울기 분포")
    hist = go.Figure(go.Histogram(x=slope[~np.isnan(slope)], nbinsx=60))
    hist.update_layout(height=350, margin=dict(t=10), xaxis_title="기울기 (%)", yaxis_title="빈도")
    st.plotly_chart(hist, width="stretch")

# --- Sway spectrum (FFT of slope) ---
with right:
    st.subheader("흔들림 스펙트럼 (기울기 FFT)")
    s = slope[~np.isnan(slope)].astype(float)
    s = s - s.mean()
    spec = go.Figure()
    if len(s) > 1:
        window = np.hanning(len(s))
        amp = np.abs(np.fft.rfft(s * window))
        freq = np.fft.rfftfreq(len(s), d=1.0 / fs)
        spec.add_trace(go.Scatter(x=freq, y=amp, line=dict(width=1)))
        # Top-3 spectral peaks (local maxima, excluding the DC bin).
        ac = amp.copy()
        ac[0] = 0.0
        maxima = [i for i in range(1, len(ac) - 1)
                  if ac[i] > ac[i - 1] and ac[i] >= ac[i + 1]]
        if not maxima and len(ac) > 1:
            maxima = [int(np.argmax(ac))]
        maxima.sort(key=lambda i: ac[i], reverse=True)
        top = maxima[:3]
        if top:
            spec.add_trace(go.Scatter(
                x=freq[top], y=amp[top], mode="markers+text",
                marker=dict(color="crimson", size=9),
                text=["{0:.3f} Hz".format(freq[i]) for i in top],
                textposition="top center", showlegend=False))
            labels = ["#{0} {1:.3f} Hz".format(r + 1, freq[i])
                      for r, i in enumerate(top)]
            spec.update_layout(title_text="피크: " + ", ".join(labels))
    spec.update_xaxes(title_text="주파수 (Hz)", range=[0, min(5.0, fs / 2)])
    spec.update_yaxes(title_text="진폭")
    spec.update_layout(height=350, margin=dict(t=30))
    st.plotly_chart(spec, width="stretch")

# --- Optional raw channels ---
with st.expander("자이로 및 온도"):
    extra = make_subplots(specs=[[{"secondary_y": True}]])
    for ax in ("GyroX_dps", "GyroY_dps", "GyroZ_dps"):
        if ax in df:
            extra.add_trace(go.Scatter(x=t, y=df[ax].to_numpy(), name=ax, line=dict(width=1)),
                            secondary_y=False)
    if "Temp_C" in df:
        extra.add_trace(go.Scatter(x=t, y=df["Temp_C"].to_numpy(), name="온도",
                                   line=dict(width=1.5, color="orange")), secondary_y=True)
    extra.update_xaxes(title_text="경과 시간 (s)")
    extra.update_yaxes(title_text="자이로 (deg/s)", secondary_y=False)
    extra.update_yaxes(title_text="온도 (C)", secondary_y=True)
    extra.update_layout(height=350, margin=dict(t=10), legend=dict(orientation="h"))
    st.plotly_chart(extra, width="stretch")

# --- Full text report (reuses analyze_tilt) ---
with st.expander("전체 분석 리포트"):
    # language=None disables syntax highlighting so plain-text lines (e.g. the
    # "#1 ... Hz" peak lines, otherwise dimmed as Python comments) render uniformly.
    st.code(analyze_tilt.analyze(path), language=None)
