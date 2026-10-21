'use strict';
let temperatureEventDate='';
const integratedStatus=()=>temperatureData?.integratedControl||null;
const tempHealthy=()=>!!measured()?.bme_ok&&temperatureData?.temperatureAlert?.state!=='unknown';
function combinedCondition(){
  const hot=tempHealthy()&&temperatureData.abnormal;
  const smoky=smokeFresh()&&actualSmoke.smoke;
  return {bad:hot||smoky,known:tempHealthy()&&smokeFresh(),
    label:hot||smoky?'이상':tempHealthy()&&smokeFresh()?'정상':'수신 확인 필요',
    detail:[hot?'온도 이상':tempHealthy()?'온도 정상':'온도 수신 대기',smoky?'연기 감지':smokeFresh()?'연기 없음':'카메라 수신 대기'].join(' · ')};
}
actualMetrics=function(){
  const s=measured(),c=combinedCondition();
  const forecast=temperatureData?.control&&!temperatureData.controlStale?temperatureData.predictedTemperatureC:null;
  return actualMetric('미세먼지 PM2.5','dust',s?.pm25_ug_m3,'µg/m³',!!s?.pms_ok,`PM1 ${liveNumber(s?.pm1_ug_m3,0)} · PM10 ${liveNumber(s?.pm10_ug_m3,0)} µg/m³`)+
    actualMetric('온도','temp',s?.temperature_c,'°C',!!s?.bme_ok,`5초 예측 ${liveNumber(forecast)} °C · 이상 기준 26 °C`)+
    actualMetric('습도','drop',s?.humidity_pct,'%RH',!!s?.bme_ok,`BME280 · 기압 ${liveNumber(s?.pressure_hpa)} hPa`)+
    `<article class="card metric container-metric"><div class="metric-top"><span class="metric-name">${icon('camera')}컨테이너 상태</span>${badge(c.bad?'이상':c.known?'실측':'확인 필요',c.bad?'red':c.known?'green':'')}</div><div class="metric-number" style="color:${c.bad?'var(--red)':c.known?'var(--green)':'#68798f'}">${c.label}</div><div class="metric-bottom"><span>${esc(c.detail)}</span></div></article>`;
};
actualFans=function(){
  const s=measured(),c=integratedStatus();
  return ['intake','exhaust'].map((key,i)=>{
    const rpm=s?.[key+'_rpm'],pwm=s?.[key+'_pwm'];
    return `<article class="card fan-card"><div class="fan-info"><div class="fan-circle">${icon('fan',rpm>0?'spin':'')}</div><div><div class="fan-title">${i?'배기':'흡기'} 팬 <span>(${i?'OUT':'IN'})</span></div><div class="rpm">${liveNumber(rpm,0)}<small>RPM</small></div><span class="small-label">ESP32 회전수 피드백</span></div></div><div class="fan-bars">${badge(s?(rpm>0?'회전 피드백 수신':'0 RPM · 정지'):'수신 대기',s?'green':'')}<div class="pwm"><span>장치 PWM</span><div class="track"><i style="width:${pwm??0}%"></i></div><b>${liveNumber(pwm,0)}%</b></div><p class="small-label">${c?.mode==='live'?'자동 운전 목표':'통합 요구'} ${liveNumber(c?.proposed?.[key],0)}% · ${c?.mode==='live'?'마지막 전송 '+liveNumber(c.lastSent?.[i],0)+'%':'명령 미전송 / 상태 확인'}</p></div></article>`;
  }).join('');
};
function actualControl(){
  const c=integratedStatus(),t=temperatureData;
  if(!c)return '<h2>통합 팬 제어</h2><p>제어 상태 수신 대기 · 이전 목표값은 표시하지 않습니다.</p>';
  if(c.mode==='paused')return '<h2>통합 팬 제어 · 정지</h2><p>대시보드 연결 대기 · 팬 목표 0% / 0%</p><p class="note">대시보드가 연결되면 자동 운전합니다. 모든 탭을 닫거나 연결이 15초 끊기면 팬을 정지합니다.</p>';
  return `<h2>통합 팬 제어 ${badge(c.mode==='live'?'자동 운전':'요구값 계산',c.mode==='live'?'green':'')}</h2><p>PM2.5 요구 ${liveNumber(c.smokeDemand.intake,0)}/${liveNumber(c.smokeDemand.exhaust,0)}% · 온도 요구 ${liveNumber(c.temperatureDemand.intake,0)}/${liveNumber(c.temperatureDemand.exhaust,0)}% → 통합 목표 ${liveNumber(c.proposed.intake,0)}/${liveNumber(c.proposed.exhaust,0)}%</p><p class="note">흡기·배기 각각 더 큰 요구값 적용 · 카메라 연기 점수는 팬 제어 입력에 포함하지 않습니다.</p><p>${esc(c.temperatureDemand.reason)}</p><p class="note">${esc(c.temperatureDemand.model)} · 회복 예상 ${liveNumber(c.temperatureDemand.recoverySeconds,0)}초<br>RPM 기반 추정 전력 ${liveNumber(t?.estimatedPowerW,3)} W · 최근 구간 에너지 ${liveNumber(t?.energy?.energy_wh,5)} Wh (실측 전력 아님)</p>`;
}
const temperatureBaseDashboard=dashboard;
dashboard=function(){return temperatureBaseDashboard().replace('<div class="dashboard-bottom">',`<section class="card live-connection" id="actual-control">${actualControl()}</section><div class="dashboard-bottom">`);};
const temperatureBaseEvents=actualEventList;
actualEventList=function(){
  const list=temperatureData?.incidents||[];
  const labels={active:'이상 진행',recovered:'회복 확인',interrupted:'수신 공백 · 회복 미확인',unconfirmed:'상태 미확인'};
  const temperature=list.map(e=>`<section class="card live-connection"><h2>온도 ${badge(labels[e.status]||'확인 필요',e.status==='recovered'?'green':e.status==='active'?'red':'')}</h2><p>시작 ${esc(e.start)}${e.end?' · 회복 '+esc(e.end):''}</p><p>최고 ${liveNumber(e.peak_c)} °C · ${e.trigger==='temperature_rise'?'상승률 감지':'26 °C 기준 초과'} · 지속 ${e.durationSec==null?'미확정':liveNumber(e.durationSec,0)+'초'}</p><p class="note">기록 구간 RPM 추정 에너지 ${liveNumber(e.energy_wh,5)} Wh</p></section>`).join('');
  return `<h2 class="section-label">온도 이상·회복 기록</h2>${temperature||'<p class="note">조회 날짜의 온도 사건 없음</p>'}<h2 class="section-label">카메라 연기 기록</h2>${temperatureBaseEvents()}`;
};
eventsPage=function(){return actualHeading('이상·회복 기록','온도 사건은 저장된 실측 DB에서 조회하며 연기 사건은 현재 카메라 서버 세션의 기록입니다.','LIVE EVENT HISTORY')+`<div class="toolbar"><label>온도 기록 날짜 <input id="temperature-event-date" type="date" value="${esc(temperatureEventDate||temperatureData?.date||'')}"></label></div><div id="actual-events">${actualEventList()}</div>`;};
document.addEventListener('change',e=>{if(e.target.id==='temperature-event-date')temperatureEventDate=e.target.value;});
const temperatureBaseRefresh=refreshMeasurements;
refreshMeasurements=function(){
  temperatureBaseRefresh();
  const panel=document.getElementById('actual-control');if(panel)panel.innerHTML=actualControl();
  const events=document.getElementById('actual-events');if(events){const open=[...events.querySelectorAll('details')].map(d=>d.open);events.innerHTML=actualEventList();events.querySelectorAll('details').forEach((d,i)=>d.open=!!open[i]);}
  const foot=document.querySelector('.page-footer');if(foot)foot.lastElementChild.textContent=integratedStatus()?.mode==='live'?'Jetson 실측 · 온도/PM 통합 팬 자동 운전':'Jetson 실측 · 팬 제어 상태 확인';
};
window.addEventListener('airguard:telemetry',refreshMeasurements);
