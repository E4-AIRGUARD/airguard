'use strict';
let temperatureData = null;
const liveNumber = (v, digits=1) => typeof v === 'number' && Number.isFinite(v) ? v.toFixed(digits) : '—';
const liveTime = v => typeof v === 'number' && Number.isFinite(v) ? new Date(v*1000).toLocaleString('ko-KR', {timeZone:'Asia/Seoul'}) : '—';
function smokeAddress() {
  const param = new URLSearchParams(location.search).get('smoke');
  if (param === 'off') return '';
  try { return param || localStorage.getItem('airguard.smokeApi') || ''; } catch { return param || ''; }
}
function sensorAddress() {
  const explicit=new URLSearchParams(location.search).get('sensors');
  if(explicit==='off')return '';
  if(explicit)return explicit.replace(/\/+$/,'');
  const smoke=smokeAddress();
  if(!smoke)return '';
  try{const url=new URL(smoke);url.port='8095';return url.origin;}catch{return '';}
}
function livePage() {
  return heading('장비 데이터 연동', 'ESP32 실측 센서·팬 피드백과 Jetson 연기 알림을 확인합니다.', 'DEVICE MONITOR', badge('조회 전용')) + `
    <section class="card live-connection"><h2>연기 감지 서버</h2>
    <p class="note">Jetson에서 smoke_alert.py 실행 후 서버 주소를 입력하세요. 연결되면 감지 배너와 우측 아래 수신 상태가 표시됩니다.</p>
    <form id="smoke-connect"><label for="smoke-url">연기 API 주소</label><div class="live-form"><input id="smoke-url" type="url" placeholder="http://Jetson주소:8092" value="${esc(smokeAddress())}"><button class="button" type="submit">연결</button><button class="button" type="button" id="smoke-off">연결 해제</button></div></form>
    <p id="smoke-config-status" class="note">${smokeAddress()?'주소 설정됨 · 실제 연결 여부는 우측 아래 상태 표시를 확인하세요.':'주소 미설정 · 연기 알림 연결 대기'}</p></section>
    <div id="temperature-live">${temperatureMarkup()}</div>
    <div class="info-strip">${icon('info')}<div>센서와 팬 수치는 ESP32 실측값입니다. 연기 기록은 현재 서버 세션의 실제 감지 결과입니다. 팬 전력은 RPM 기반 추정값이며, 이 웹 서버는 팬 명령을 보내지 않습니다.</div></div>`;
}
function temperatureMarkup() {
  const d = temperatureData;
  if (!d || !d.available) return `<section class="card live-connection"><h2>센서·제어 기록</h2><p role="status">${esc(d?.message || '온도 DB 확인 중…')}</p><p class="note">통합 센서·팬 API 연결을 확인하세요. 최신 데이터가 도착하면 자동으로 표시합니다.</p></section>`;
  const s=d.sensor,c=d.control;
  const card=(name,value,unit)=>`<article class="card live-metric"><span class="metric-name">${name}</span><div class="metric-number">${value}<small>${unit}</small></div><small>${d.stale?'마지막 기록 · 갱신 지연':'DB 최신 수신값'}</small></article>`;
  const candidates=Array.isArray(c?.candidates)?c.candidates:[];
  const series=d.history.filter(r=>typeof r.temperature_c==='number'&&Number.isFinite(r.temperature_c));
  const graph=series.length>1 ? chart(series.map(r=>r.temperature_c),{min:Math.min(...series.map(r=>r.temperature_c))-1,max:Math.max(...series.map(r=>r.temperature_c))+1,height:190,width:900,times:series.map(r=>r.timestamp),labels:series.map((r,i)=>i===0||i===series.length-1?new Date(r.timestamp*1000).toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul'}):''),label:'DB 온도 기록',decimals:1}) : '<p class="note">그래프를 그릴 센서 기록을 기다립니다.</p>';
  return `<section class="card live-connection"><div class="card-head"><h2>센서·제어 기록</h2>${badge(esc(d.message),d.stale?'red':'green')}</div><p class="note">마지막 센서 기록 ${esc(liveTime(s.timestamp))} · 수신 경과 ${liveNumber(d.ageSec)}초</p></section>
    <div class="metrics">${card('온도',liveNumber(s.temperature_c),'°C')}${card('습도',liveNumber(s.humidity_pct),'%RH')}${card('PM2.5',liveNumber(s.pm25_ug_m3),'µg/m³')}${card('팬 합산 추정 전력',liveNumber(d.estimatedPowerW,3),'W')}</div>
    <div class="fans">${['intake','exhaust'].map((key,i)=>`<section class="card live-connection"><h2>${i?'배기':'흡기'} 팬</h2><div class="metric-number">${liveNumber(s[key+'_rpm'],0)}<small>RPM</small></div><p>장치 PWM ${liveNumber(s[key+'_pwm'],0)}% · 제어 제안 ${liveNumber(c?.[key+'_percent'],0)}%</p></section>`).join('')}</div>
    <section class="card live-connection"><h2>제어 판단</h2><p>${c?`${badge(c.stale?'제어 기록 지연':'최신 제어 기록',c.stale?'red':'green')} ${badge(c.demand_only?'온도 요구값 · 통합 전':c.command_sent?'명령 전송 기록 있음':'명령 미전송 / dry-run')} · ${esc(c.model_name || '—')}`:'제어 판단 기록 대기'}</p><p>${esc(c?.reason || '판단 이유 기록 없음')}</p><p class="note">예측 온도 ${liveNumber(c?.predicted_temp_c)} °C · 정책 상한 ${liveNumber(c?.policy_limit_c)} °C · 제어 기록 ${esc(liveTime(c?.timestamp))}</p>
    ${candidates.length?`<details><summary>후보 조합 비교 (${candidates.length}개)</summary><pre class="live-json">${esc(JSON.stringify(candidates,null,2))}</pre></details>`:''}</section>
    <section class="card live-connection"><h2>최근 실제 수신 구간 · 온도</h2>${graph}<p class="note">최근 5분의 저장된 센서 표본. 구간 팬 추정 에너지 ${liveNumber(d.energy?.energy_wh,5)} Wh · 유효 ${liveNumber(d.energy?.covered_seconds)}초 / 제외 ${liveNumber(d.energy?.excluded_seconds)}초. 누락·2.5초 초과 간격은 적분에서 제외합니다.</p></section>
    <section class="card live-connection"><h2>최근 통합 명령 기록</h2><div class="live-table"><table><thead><tr><th>시각</th><th>온도</th><th>흡기 / 배기 목표</th><th>이유</th></tr></thead><tbody>${(d.controlEvents||d.events||[]).slice(0,20).map(e=>`<tr><td>${esc(liveTime(e.timestamp))}</td><td>${liveNumber(e.temperature_c)} °C</td><td>${liveNumber(e.intake_percent,0)} / ${liveNumber(e.exhaust_percent,0)}%</td><td>${esc(e.reason || '')}</td></tr>`).join('') || '<tr><td colspan="4">제어 변경 기록 없음</td></tr>'}</tbody></table></div><p class="note">최근 20건 · 주기적 재전송 포함 · 실제 회전 피드백과 구분합니다.</p></section>`;
}
function bindDeviceLink() {
  document.querySelector('#smoke-connect')?.addEventListener('submit', e=>{
    e.preventDefault();
    try {
      const raw=document.querySelector('#smoke-url').value.trim(), url=new URL(raw);
      if(!['http:','https:'].includes(url.protocol)||url.username||url.password||url.search||url.hash||url.pathname!=='/') throw new Error('주소는 http://호스트:포트 형식으로 입력하세요.');
      if(location.protocol==='https:'&&url.protocol==='http:') throw new Error('HTTPS 화면에서는 HTTPS 연기 서버가 필요합니다.');
      const target=new URL(location.href); target.searchParams.set('smoke',url.origin); location.href=target.href;
    } catch(err) { document.querySelector('#smoke-config-status').textContent=err.message; }
  });
  document.querySelector('#smoke-off')?.addEventListener('click',()=>{const target=new URL(location.href);target.searchParams.set('smoke','off');location.href=target.href;});
}
function startDeviceLink() {
  const style=document.createElement('style');
  style.textContent='.live-connection{padding:22px;margin-bottom:18px}.live-connection h2{font-size:16px;margin:0 0 12px}.live-form{display:flex;gap:10px;margin-top:8px;flex-wrap:wrap}.live-form input{flex:1;min-width:200px;padding:10px;border:1px solid #cbd5e1;border-radius:6px}.live-metric{padding:22px;margin-bottom:18px}.live-json{white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto}.live-table{overflow-x:auto}.live-table table{width:100%;border-collapse:collapse}.live-table td,.live-table th{padding:10px;text-align:left;border-bottom:1px solid #edf1f7}.live-connection .chart{width:100%}';
  document.head.appendChild(style);
  async function pollTemperature() {
    try {const selectedDate=typeof temperatureEventDate!=='undefined'?temperatureEventDate:'';const response=await fetch(`${sensorAddress()}/api/temperature?seconds=${state.page==='trends'?state.range*60:300}${selectedDate?'&date='+encodeURIComponent(selectedDate):''}`,{cache:'no-store',signal:AbortSignal.timeout(3500)});if(!response.ok)throw new Error();temperatureData=await response.json();}
    catch {temperatureData={available:false,message:'센서 API 연결 끊김 · Jetson 수신기 상태를 확인하세요.'};}
    const panel=document.querySelector('#temperature-live');
    // Preserve an open candidate comparison while live data refreshes.
    if(panel){const expanded=panel.querySelector('details')?.open;panel.innerHTML=temperatureMarkup();if(expanded&&panel.querySelector('details'))panel.querySelector('details').open=true;}
    window.dispatchEvent(new CustomEvent('airguard:telemetry',{detail:temperatureData}));
    setTimeout(pollTemperature,1000);
  }
  pollTemperature();
}
