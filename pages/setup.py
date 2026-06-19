# coding:UTF-8
# Hidden admin page (URL: /setup) to change the judgment criteria and the PIN.
#
# Reachable only by typing the URL (the page navigation is hidden via
# .streamlit/config.toml). Protected by a PIN; the default is 01023538099 and
# should be changed on first use below.

import streamlit as st

import app_config

st.set_page_config(page_title="설정 (관리자)", layout="centered")
st.title("관리자 설정")
st.page_link("dashboard.py", label="← 대시보드로 돌아가기")

cfg = app_config.load()

# --- PIN gate ---
if not st.session_state.get("admin_ok"):
    st.caption("PINCODE를 입력하세요.")
    pin = st.text_input("PINCODE", type="password")
    if st.button("확인"):
        if app_config.verify_pin(cfg, pin):
            st.session_state["admin_ok"] = True
            st.rerun()
        else:
            st.error("PINCODE가 올바르지 않습니다.")
    st.stop()

if app_config.verify_pin(cfg, app_config.DEFAULT_PIN):
    st.warning("초기 PINCODE를 사용 중입니다. 아래에서 반드시 변경하세요.")

# --- Judgment criteria ---
st.subheader("판단 기준")
threshold = st.number_input(
    "기울기 경보 임계값 (%)", min_value=0.001, max_value=10.0,
    value=float(cfg["slope_threshold_pct"]), step=0.01, format="%.3f")
ma_seconds = st.number_input(
    "이동평균 윈도우 (초)", min_value=0.1, max_value=60.0,
    value=float(cfg["ma_seconds"]), step=0.5)
if st.button("기준 저장"):
    app_config.update_settings(cfg, threshold, ma_seconds)
    app_config.save(cfg)
    st.success("기준을 저장했습니다. 대시보드에 즉시 반영됩니다.")

st.divider()

# --- Change PIN ---
st.subheader("PINCODE 변경")
new1 = st.text_input("새 PINCODE", type="password", key="new_pin_1")
new2 = st.text_input("새 PINCODE 확인", type="password", key="new_pin_2")
if st.button("PINCODE 변경"):
    if not new1:
        st.error("새 PINCODE를 입력하세요.")
    elif new1 != new2:
        st.error("두 PINCODE가 일치하지 않습니다.")
    else:
        app_config.set_pin(cfg, new1)
        app_config.save(cfg)
        st.success("PINCODE를 변경했습니다.")

st.divider()
if st.button("로그아웃"):
    st.session_state["admin_ok"] = False
    st.rerun()
