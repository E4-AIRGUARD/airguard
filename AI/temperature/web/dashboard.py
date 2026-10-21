"""AirGuard 실시간 챔버 모니터링 대시보드.

실행: python3 -m streamlit run AI/temperature/web/dashboard.py
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

import pandas as pd
import streamlit as st


TARGET_C = 26.0
MAX_RECOVERY_SECONDS = 600


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--database", default=str(Path(__file__).resolve().parents[1] / "runtime/edge_events.db"))
    return parser.parse_known_args()[0]


def read_table(database: Path, query: str, parameters: tuple = ()) -> pd.DataFrame:
    if not database.exists():
        return pd.DataFrame()
    try:
        with sqlite3.connect(database, timeout=1) as connection:
            rows = pd.read_sql_query(query, connection, params=parameters)
            # 이전 런타임이 NumPy 정수를 SQLite BLOB으로 저장한 기록을 읽습니다.
            for column in ('intake_percent', 'exhaust_percent', 'intake_pwm', 'exhaust_pwm',
                           'intake_rpm', 'exhaust_rpm', 'recovery_seconds', 'command_sent'):
                if column in rows:
                    rows[column] = rows[column].map(decode_legacy_integer)
            return rows
    except (sqlite3.Error, pd.errors.DatabaseError):
        return pd.DataFrame()


def sensor_history(database: Path, seconds: int) -> pd.DataFrame:
    rows = read_table(database, "SELECT * FROM sensor_readings WHERE timestamp >= ? ORDER BY timestamp", (time.time() - seconds,))
    if not rows.empty:
        rows["time"] = pd.to_datetime(rows["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Seoul")
        rows = rows.set_index("time")
    return rows


def latest_control(database: Path) -> pd.Series | None:
    rows = read_table(database, "SELECT * FROM control_status WHERE id = 1")
    return None if rows.empty else rows.iloc[0]


def candidate_comparison(control: pd.Series | None) -> pd.DataFrame:
    if control is None:
        return pd.DataFrame()
    try:
        return pd.DataFrame(json.loads(control.get("candidate_json") or "[]"))
    except (TypeError, json.JSONDecodeError):
        return pd.DataFrame()


def decode_legacy_integer(value: object) -> object:
    """기존 NumPy int64 바이트를 정수로 복원합니다."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return int.from_bytes(raw, byteorder='little', signed=True) if len(raw) == 8 else None
    return value


def number(value: object, suffix: str = "", digits: int = 1) -> str:
    value = decode_legacy_integer(value)
    if value is None or pd.isna(value):
        return "—"
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError, OverflowError):
        return "—"


def connection_state(latest: pd.Series | None) -> tuple[str, str, str]:
    if latest is None:
        return "미연결", "offline", "센서 데이터를 기다리는 중"
    age = max(0.0, time.time() - float(latest["timestamp"]))
    if age <= 3:
        return "정상", "ok", f"수신 데이터 {age:.1f}초 전"
    if age <= 10:
        return "지연", "warn", f"수신 데이터 {age:.1f}초 전"
    return "데이터 없음", "offline", f"마지막 수신 {age:.0f}초 전"


def recovery_text(control: pd.Series | None) -> str:
    if control is None or pd.isna(control.get("recovery_seconds")):
        return f"{MAX_RECOVERY_SECONDS}초 이내 회복 예측 불가"
    seconds = int(control["recovery_seconds"])
    return "0초 · 이미 목표 범위" if seconds == 0 else f"약 {seconds}초"


def feedback_status(latest: pd.Series | None, control: pd.Series | None) -> tuple[str, str]:
    """명령 PWM과 ESP32가 보고한 PWM을 비교해 실제 운전 확인 상태를 만듭니다."""
    if latest is None:
        return "센서 데이터 없음", "offline"
    actual_intake, actual_exhaust = latest.get("intake_pwm"), latest.get("exhaust_pwm")
    if pd.isna(actual_intake) or pd.isna(actual_exhaust):
        return "ESP32 PWM/RPM 피드백 대기", "warn"
    if control is None:
        return "PWM 수신 · 제어 명령 대기", "warn"
    command_intake, command_exhaust = control.get("intake_percent"), control.get("exhaust_percent")
    if pd.isna(command_intake) or pd.isna(command_exhaust):
        return "PWM 수신 · 명령값 없음", "warn"
    matches = abs(float(actual_intake) - float(command_intake)) <= 2 and abs(float(actual_exhaust) - float(command_exhaust)) <= 2
    return ("명령값과 ESP32 PWM 일치", "ok") if matches else ("명령값과 ESP32 PWM 불일치", "warn")


def command_age_text(control: pd.Series | None) -> str:
    if control is None or pd.isna(control.get("last_command_at")):
        return "명령 전송 기록 없음"
    age = max(0.0, time.time() - float(control["last_command_at"]))
    sent = bool(control.get("command_sent", False))
    return f"{'이번 주기 명령 전송' if sent else '마지막 명령 유지'} · {age:.1f}초 전"


def metric_card(label: str, value: str, accent: str, detail: str = "") -> str:
    return f'''<div class="metric-card"><div class="metric-head">{label}</div><div class="metric-value {accent}">{value}</div><div class="metric-detail">{detail or '&nbsp;'}</div></div>'''


st.set_page_config(page_title="E4 AirGuard", page_icon="◈", layout="wide", initial_sidebar_state="expanded")
st.markdown(
    """
    <style>
    :root { --ink:#272633; --coral:#ff8068; --mint:#eaf7f5; --line:#d9eae8; --green:#86d986; --blue:#247cf0; }
    .stApp { background:var(--mint); color:var(--ink); }
    header[data-testid="stHeader"] { background:transparent; }
    #MainMenu, footer { visibility:hidden; }
    section[data-testid="stSidebar"] { background:#272633; }
    section[data-testid="stSidebar"] * { color:#f9f8fb !important; }
    section[data-testid="stSidebar"] [data-testid="stSelectbox"] label { color:#aeb0ba !important; }
    section[data-testid="stSidebar"] div[data-baseweb="select"] > div { background:#373645; border-color:#515061; }
    .brand { font-size:1.65rem; font-weight:800; letter-spacing:-.04em; margin:1.25rem 0 .2rem; }
    .brand span { color:var(--coral); }
    .side-caption { color:#aeb0ba !important; font-size:.82rem; margin-bottom:2rem; }
    .nav-item { border-left:3px solid transparent; color:#c7c7cf !important; padding:.72rem .7rem; margin:.18rem 0; font-weight:600; }
    .nav-item.active { border-color:var(--coral); color:white !important; background:#333240; }
    .app-title { font-size:2rem; font-weight:800; color:var(--ink); letter-spacing:-.045em; margin:0; }
    .app-title span { color:var(--coral); }
    .subtitle { color:#67747b; margin:.2rem 0 1.1rem; }
    .status { text-align:right; font-size:.92rem; font-weight:700; padding-top:.55rem; }
    .status.ok { color:#258755; }.status.warn { color:#b47300; }.status.offline { color:#c44a42; }
    .metric-card { background:#fff; min-height:126px; border-radius:10px; overflow:hidden; box-shadow:0 3px 10px #9eb7b322; margin-bottom:1rem; }
    .metric-head { background:var(--ink); color:#fff; font-size:.82rem; font-weight:800; letter-spacing:.04em; padding:.65rem .85rem; text-transform:uppercase; }
    .metric-value { font-weight:800; font-size:1.8rem; padding:.55rem .85rem .12rem; line-height:1; }.metric-value.blue { color:var(--blue); }.metric-value.coral { color:#ec624f; }.metric-value.green { color:#269155; }.metric-value.ink { color:var(--ink); }
    .metric-detail { color:#758087; padding:.15rem .85rem .65rem; font-size:.78rem; }
    .panel { background:#fff; padding:1rem 1.05rem .75rem; border-radius:10px; box-shadow:0 3px 10px #9eb7b322; margin-bottom:1rem; }
    .panel-title { color:var(--ink); font-weight:800; margin:0 0 .1rem; font-size:1.02rem; }.panel-caption { color:#758087; font-size:.78rem; margin-bottom:.65rem; }
    .control-panel { background:#272633; color:#fff; padding:1rem 1.05rem; border-radius:10px; margin-bottom:1rem; }.control-panel h3 { color:#fff; margin:0 0 .75rem; }.control-panel p { color:#d1d0d7; margin:.25rem 0; font-size:.87rem; }.control-value { color:var(--green); font-size:1.65rem; font-weight:800; }
    .note { color:#758087; font-size:.78rem; }.stDataFrame { border-radius:8px; overflow:hidden; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.fragment(run_every="1s")
def render_dashboard(database: Path, history_seconds: int) -> None:
    history = sensor_history(database, history_seconds)
    latest = None if history.empty else history.iloc[-1]
    control = latest_control(database)
    status, tone, detail = connection_state(latest)

    title, badge = st.columns((3, 1))
    with title:
        st.markdown('<p class="app-title">E4 <span>AIRGUARD</span> · 챔버 모니터링</p><p class="subtitle">ESP32 센서 수신 · 온도 예측 · 팬 제어 기록</p>', unsafe_allow_html=True)
    with badge:
        st.markdown(f'<p class="status {tone}">● {status}<br><span class="note">{detail}</span></p>', unsafe_allow_html=True)

    if latest is None:
        temp = smoothed_temp = humidity = pm25 = actual_intake = actual_exhaust = None
    else:
        temp, humidity, pm25 = latest.get("temperature_c"), latest.get("humidity_pct"), latest.get("pm25_ug_m3")
        smoothed_temp = latest.get("smoothed_temperature_c")
        actual_intake, actual_exhaust = latest.get("intake_pwm"), latest.get("exhaust_pwm")
    prediction = None if control is None else control.get("predicted_temp_c")
    command_intake = None if control is None else control.get("intake_percent")
    command_exhaust = None if control is None else control.get("exhaust_percent")
    fan_status, fan_tone = feedback_status(latest, control)
    cards = st.columns(5)
    temperature_detail = f"평활 {number(smoothed_temp, '°C')} · 목표 상한 {TARGET_C:.0f}°C"
    with cards[0]: st.markdown(metric_card("원본 온도 · °C", number(temp), "coral", temperature_detail), unsafe_allow_html=True)
    with cards[1]: st.markdown(metric_card("습도 · %RH", number(humidity), "blue", "실시간 센서값"), unsafe_allow_html=True)
    with cards[2]: st.markdown(metric_card("PM2.5 · µg/m³", number(pm25), "green", "stale이면 표시하지 않음"), unsafe_allow_html=True)
    with cards[3]: st.markdown(metric_card("10초 뒤 예측", number(prediction, "°C"), "coral", "Physics 또는 LSTM"), unsafe_allow_html=True)
    with cards[4]: st.markdown(metric_card("센서 연결", status, "green" if tone == "ok" else "coral", "안전 규칙 우선 적용"), unsafe_allow_html=True)

    left, right = st.columns((2.15, 1))
    with left:
        st.markdown('<div class="panel"><p class="panel-title">온도 추세와 예측 기준</p><p class="panel-caption">현재 온도 · 습도 · PM2.5 / 온도 상한 26°C</p>', unsafe_allow_html=True)
        if history.empty:
            st.info("센서 데이터가 들어오면 최근 추세를 표시합니다.")
        else:
            chart_columns = (
                "temperature_c", "smoothed_temperature_c", "humidity_pct", "pm25_ug_m3"
            )
            chart = history[[
                column for column in chart_columns
                if column in history and history[column].notna().any()
            ]]
            st.line_chart(chart, height=285, width="stretch")
            st.caption(f"온도 제어 상한: {TARGET_C:.1f}°C · 실제 센서 수신 시각 기준")
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="panel"><p class="panel-title">팬 운전 추세</p><p class="panel-caption">실제 PWM과 RPM 피드백</p>', unsafe_allow_html=True)
        if not history.empty:
            columns = [column for column in ("intake_pwm", "exhaust_pwm", "intake_rpm", "exhaust_rpm") if history[column].notna().any()]
            if columns: st.line_chart(history[columns], height=215, width="stretch")
            else: st.info("팬 피드백을 기다리고 있습니다.")
        st.markdown('</div>', unsafe_allow_html=True)

    with right:
        command_intake_label, command_exhaust_label = number(command_intake, "%"), number(command_exhaust, "%")
        actual_intake_label, actual_exhaust_label = number(actual_intake, "%"), number(actual_exhaust, "%")
        model_name = "대기" if control is None else str(control.get("model_name") or "대기")
        reason = "아직 제어 기록이 없습니다." if control is None else str(control.get("reason") or "—")
        policy_value = None if control is None else control.get("policy_mode")
        policy_mode = "정책 정보 갱신 대기" if policy_value is None or pd.isna(policy_value) else str(policy_value)
        policy_limit = None if control is None else control.get("policy_limit_c")
        st.markdown(f'<div class="control-panel"><h3>AI 환기 최적화</h3><p>현재 적용 명령 PWM</p><div class="control-value">흡기 {command_intake_label} · 배기 {command_exhaust_label}</div><p>운영 정책: <b>{policy_mode}</b>{" · 내부 한계 " + number(policy_limit, "°C") if policy_limit is not None and not pd.isna(policy_limit) else ""}</p><p>예상 회복시간: <b>{recovery_text(control)}</b></p><p>{command_age_text(control)}</p><p>제어기: {model_name}</p><p>사유: {reason}</p></div>', unsafe_allow_html=True)
        st.markdown('<div class="panel"><p class="panel-title">실제 팬 피드백</p><p class="panel-caption">ESP32가 보고한 PWM·RPM입니다.</p>', unsafe_allow_html=True)
        st.write(f"피드백 상태: **{fan_status}**")
        st.write(f"흡기 PWM: **{actual_intake_label}** · RPM: **{number(None if latest is None else latest.get('intake_rpm'))}**")
        st.write(f"배기 PWM: **{actual_exhaust_label}** · RPM: **{number(None if latest is None else latest.get('exhaust_rpm'))}**")
        if fan_tone != "ok":
            st.caption("현재 값이 `—`이면 ESP32 DATA 프레임에 PWM/RPM 필드가 없거나 가상 센서 실행 중입니다.")
        st.caption("배기 PWM ≥ 흡기 PWM은 운전 제약이며, 차압·음압을 증명하지 않습니다.")
        st.markdown('</div>', unsafe_allow_html=True)
        candidates = candidate_comparison(control)
        if not candidates.empty:
            if 'total_relative_energy' not in candidates:
                candidates['total_relative_energy'] = None
            if 'recovery_seconds' not in candidates:
                candidates['recovery_seconds'] = None
            st.markdown('<div class="panel"><p class="panel-title">PWM 후보 비교</p><p class="panel-caption">상대 팬 에너지 기준</p>', unsafe_allow_html=True)
            candidates["판정"] = candidates.apply(lambda x: "선택" if x["selected"] else "목표 충족" if x["safe"] else "목표 초과", axis=1)
            candidates = candidates.rename(columns={"intake_pwm":"흡기", "exhaust_pwm":"배기", "predicted_temp_c":"예측 °C", "relative_energy":"상대 전력", "total_relative_energy":"전력×회복시간", "recovery_seconds":"회복시간(초)"})
            st.dataframe(candidates[["흡기", "배기", "예측 °C", "상대 전력", "회복시간(초)", "전력×회복시간", "판정"]], hide_index=True, width="stretch")
            st.caption('후보 PWM 유지 시의 예상 회복시간 × 상대 전력(PWM³)으로 비교합니다. 회복시간은 정책 목표 기준이며, 600초는 추정 탐색 상한입니다. 장기 회복시간은 실험 검증이 필요합니다.')
            st.markdown('</div>', unsafe_allow_html=True)


def main() -> None:
    args = parse_args()
    database = Path(args.database)
    with st.sidebar:
        st.markdown('<p class="brand">E4 <span>AIRGUARD</span></p><p class="side-caption">Chamber thermal control</p>', unsafe_allow_html=True)
        st.markdown('<p class="nav-item active">◈ 챔버 모니터링</p><p class="nav-item">◷ 제어 이력</p><p class="nav-item">⌁ 모델 평가</p><p class="nav-item">⚙ 시스템 설정</p>', unsafe_allow_html=True)
        st.divider()
        st.caption("표시 범위")
        history_seconds = st.selectbox("최근 기록", (60, 300, 900, 3600), index=1, format_func=lambda value: f"최근 {value // 60}분")
        st.caption(f"DB: {database}")
        st.caption("화면은 1초마다 자동 갱신됩니다.")
    render_dashboard(database, history_seconds)


if __name__ == "__main__":
    main()
