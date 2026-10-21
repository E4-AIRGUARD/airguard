'use strict';
let actualSmoke=null;
let smokeReceivedAt=0;
const measured=()=>temperatureData?.available&&!temperatureData.stale?temperatureData.sensor:null;
const smokeFresh=()=>actualSmoke?.cameraOk&&Number.isFinite(actualSmoke.ageSec)&&actualSmoke.ageSec*1000+performance.now()-smokeReceivedAt<6000;
function actualHeading(title,desc,eyebrow){return heading(title,desc,eyebrow,badge(measured()?'실측 데이터 수신 중':'센서 수신 대기',measured()?'green':''));}
function actualMetric(name,ic,value,unit,valid,detail){return `<article class="card metric"><div class="metric-top"><span class="metric-name">${icon(ic)}${name}</span>${badge(valid?'실측':'수신 대기',valid?'green':'')}</div><div class="metric-number">${liveNumber(value)}<small>${unit}</small></div><div class="metric-bottom"><span>${esc(detail)}</span></div></article>`;}
function actualMetrics(){
  const s=measured();
  const fresh=smokeFresh();
  return actualMetric('미세먼지 PM2.5','dust',s?.pm25_ug_m3,'µg/m³',!!s?.pms_ok,`PM1 ${liveNumber(s?.pm1_ug_m3,0)} · PM10 ${liveNumber(s?.pm10_ug_m3,0)} µg/m³`)+
    actualMetric('온도','temp',s?.temperature_c,'°C',!!s?.bme_ok,`BME280 · 기압 ${liveNumber(s?.pressure_hpa)} hPa`)+
    actualMetric('습도','drop',s?.humidity_pct,'%RH',!!s?.bme_ok,'BME280 · 상대습도')+
    `<article class="card metric container-metric"><div class="metric-top"><span class="metric-name">${icon('camera')}비전 연기 감지</span>${badge(fresh?'카메라 수신':'수신 대기',fresh?'green':'')}</div><div class="metric-number" style="color:${fresh&&actualSmoke.smoke?'var(--red)':'var(--green)'}">${fresh?(actualSmoke.smoke?'연기 감지':'연기 없음'):'—'}</div><div class="metric-bottom"><span>${fresh?'모델 점수 '+liveNumber(actualSmoke.score,2):'카메라 연결 확인 중'}</span></div></article>`;
}
function actualFans(){
  const s=measured();
  return ['intake','exhaust'].map((key,i)=>{
    const rpm=s?.[key+'_rpm'],pwm=s?.[key+'_pwm'];
    return `<article class="card fan-card"><div class="fan-info"><div class="fan-circle">${icon('fan',rpm>0?'spin':'')}</div><div><div class="fan-title">${i?'배기':'흡기'} 팬 <span>(${i?'OUT':'IN'})</span></div><div class="rpm">${liveNumber(rpm,0)}<small>RPM</small></div><span class="small-label">ESP32 회전수 피드백</span></div></div><div class="fan-bars">${badge(s?(rpm>0?'회전 피드백 수신':'0 RPM · 정지'):'수신 대기',s?'green':'')}<div class="pwm"><span>장치 PWM</span><div class="track"><i style="width:${pwm??0}%"></i></div><b>${liveNumber(pwm,0)}%</b></div><p class="small-label">모니터링 전용 · 제어 명령 전송 없음</p></div></article>`;
  }).join('');
}
function actualConnections(){
  const s=measured();
  const entries=[['ESP32 USB Serial',!!s],['BME280 온습도',!!s?.bme_ok],['PMS5003 미세먼지',!!s?.pms_ok],['Jetson 카메라',smokeFresh()]];
  return entries.map(([name,ok])=>`<div class="connection">${name}<span><i class="dot" style="background:${ok?'#00a68a':'#adbacb'}"></i>${ok?'수신 중':'수신 대기'}</span></div>`).join('')+`<p class="note">마지막 센서 수신: ${esc(liveTime(temperatureData?.sensor?.timestamp))}<br>${esc(temperatureData?.message||'연결 중…')}</p>`;
}
dashboard=function(){return actualHeading('대시보드','Jetson에 연결된 센서와 카메라의 실시간 측정값입니다.','LIVE OVERVIEW')+`
 <div class="metrics" id="actual-metrics">${actualMetrics()}</div>
 <h2 class="section-label">팬 운전 상태 <span>ESP32 장치 PWM·회전수 피드백</span></h2><div class="fans" id="actual-fans">${actualFans()}</div>
 <div class="dashboard-bottom"><section class="card camera-card"><div class="card-head"><h2>${icon('camera')}챔버 CCTV</h2>${badge('카메라 연결 대기')}</div><div class="camera" id="camera-view"><img hidden alt="Jetson 카메라 실시간 영상"><div class="camera-top"><span class="camera-tag">CAM 01 &nbsp; CHAMBER</span><span class="camera-tag">JETSON CAMERA</span></div><div class="camera-bottom"><div><b>Jetson 카메라 · 실시간</b><small>연기 감지에 사용하는 카메라 영상</small></div><button class="camera-action" id="camera-expand" aria-label="실시간 CCTV 전체 화면">${icon('expand')}</button></div></div><div class="camera-footer"><span>실시간 카메라</span><span>연결 대기</span></div></section>
 <div class="right-stack"><section class="card"><div class="card-head"><h2>${icon('bell')}최근 연기 알림</h2><a class="text-link" href="#events">전체 보기 →</a></div><div class="activity"><p class="note" data-live-empty>현재 서버 세션의 감지 기록을 기다립니다.</p></div></section><section class="card"><div class="card-head"><h2>${icon('link')}실제 연결 상태</h2><a class="text-link" href="#hardware">상세 →</a></div><div class="connections" id="actual-connections">${actualConnections()}</div></section></div></div>`;};

// Split traces at missing measurements and acquisition gaps; no synthetic samples.
function measuredChart(rows,key,title,unit){
  const clean=rows.filter(r=>Number.isFinite(r.timestamp));
  const points=clean.filter(r=>Number.isFinite(r[key]));
  if(points.length<2)return '<div class="empty-events">실제 측정값 2개 이상 수신 후 그래프가 표시됩니다.</div>';
  const lo=Math.min(...points.map(r=>r[key])),hi=Math.max(...points.map(r=>r[key]));
  const pad=Math.max((hi-lo)*.15,1),min=Math.max(0,lo-pad),max=hi+pad;
  const start=clean[0].timestamp,end=clean.at(-1).timestamp;
  const x=t=>45+(t-start)/Math.max(1,end-start)*725,y=v=>180-(v-min)/(max-min)*155;
  const allowedGap=Math.max(5,state.range*60/600*2.5);
  let paths=[],segment=[],previous=null;
  for(const row of clean){
    if(!Number.isFinite(row[key])||(previous&&row.timestamp-previous.timestamp>allowedGap)){if(segment.length)paths.push(segment);segment=[];}
    if(Number.isFinite(row[key]))segment.push(`${x(row.timestamp)},${y(row[key])}`);
    previous=row;
  }
  if(segment.length)paths.push(segment);
  return `<svg class="chart" role="img" aria-label="${esc(title)} 실측 추이" viewBox="0 0 800 220">${[0,1,2,3,4].map(i=>{const v=min+(max-min)*i/4;return `<line x1="45" x2="770" y1="${y(v)}" y2="${y(v)}" stroke="#e6ecf3"/><text x="38" y="${y(v)+4}" text-anchor="end">${v.toFixed(1)}</text>`;}).join('')}${paths.map(p=>`<polyline points="${p.join(' ')}" fill="none" stroke="#2985f8" stroke-width="2.4"/>`).join('')}<text x="45" y="210">${esc(new Date(start*1000).toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul'}))}</text><text x="770" y="210" text-anchor="end">${esc(new Date(end*1000).toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul'}))}</text></svg><p class="note">${points.length}개 실제 표본 · ${esc(unit)} · 결측 구간은 연결하지 않습니다.</p>`;
}
function actualTrendCards(){
  const s=measured(),rows=temperatureData?.history||[];
  return [['PM2.5','pm25_ug_m3','µg/m³'],['온도','temperature_c','°C'],['습도','humidity_pct','%RH']].map(([name,key,unit])=>`<section class="card trend-card"><div class="metric-top"><b>${name}</b>${badge(s?'실측':'수신 지연',s?'green':'red')}</div><div class="metric-number">${liveNumber(s?.[key])}<small>${unit}</small></div>${measuredChart(rows,key,name,unit)}</section>`).join('');
}
trends=function(){return actualHeading('센서 트렌드','센서 수신기를 시작한 이후의 실제 측정 이력입니다.','LIVE SENSOR ANALYTICS')+`<div class="toolbar"><div class="segmented" role="group" aria-label="조회 기간">${[5,15,60].map(n=>`<button data-range="${n}" class="${state.range===n?'active':''}">${n===60?'1시간':n+'분'}</button>`).join('')}</div><span class="small-label">실측 표본만 표시 · 최대 1시간</span></div><div class="trend-grid" id="actual-trends">${actualTrendCards()}</div>`;};

function actualDeviceStatus(d){
  const s=measured();
  let ok=d.name==='PMS5003'?!!s?.pms_ok:d.name==='BME280'?!!s?.bme_ok:d.kind==='fan'||d.name==='ESP32 DevKitC'?!!s:d.kind==='camera'?smokeFresh():!!s||smokeFresh();
  return {ok,label:ok?'실측 수신 중':'수신 대기'};
}
hardware=function(){return actualHeading('하드웨어 연결 상태','현재 API와 ESP32 피드백으로 확인한 연결 상태입니다.','LIVE HARDWARE')+`<div class="hardware-grid">${deviceData.map((d,i)=>{const status=actualDeviceStatus(d);return `<article class="card hardware-card"><div class="device-picture"><span data-device-status="${i}" class="badge ${status.ok?'green':''}">${status.label}</span><img src="assets/hardware/${d.image}" alt="${esc(d.name)} 제품 사진" loading="lazy"><span class="illustration-label">PRODUCT PHOTO</span></div><div class="device-content"><h2>${d.name}</h2><span class="device-model">${d.model}</span><p>${d.role}</p><dl class="device-specs">${specRows(d)}</dl><div class="device-footer"><span>${d.measure}</span><button class="text-link" data-device="${i}">상세 보기 ↗</button></div><a class="photo-credit" href="${d.photoSource}" target="_blank" rel="noopener noreferrer">사진: ${d.credit} ↗</a></div></article>`;}).join('')}</div>`;};

function actualEventList(){
  const list=actualSmoke?.incidents||[];
  if(!list.length)return '<section class="card"><div class="empty-events">현재 연기 서버 세션에 감지 기록이 없습니다. 감지 시 자동으로 추가됩니다.</div></section>';
  const api=smokeAddress();
  return list.map(e=>`<section class="card live-connection"><h2>${esc(e.id)} ${badge(e.end?'해제':'감지 중',e.end?'green':'red')}</h2><p>시작 ${esc(e.start)}${e.end?' · 해제 '+esc(e.end):''}</p><p>최고 점수 ${liveNumber(e.peak,2)} · 지속 ${e.durationSec==null?'진행 중':esc(e.durationSec)+'초'}</p><details><summary>감지 순간 사진</summary><img class="actual-event-photo" loading="lazy" src="${esc(api)}/event/${encodeURIComponent(e.id)}.jpg" alt="${esc(e.id)} 감지 순간 카메라 사진"></details></section>`).join('');
}
eventsPage=function(){return actualHeading('연기 감지·해제 기록','현재 연기 서버 세션의 실제 사건입니다. 서버 재시작 시 기록이 초기화됩니다.','LIVE EVENT HISTORY')+`<p class="note">최대 30건 · PM2.5·온도 기준 초과 사건 저장은 아직 연결되지 않았습니다.</p><div id="actual-events">${actualEventList()}</div>`;};

const originalCombo=combo;
combo=function(){const s=measured();return state.page==='cfd-current'&&s?{situation:smokeFresh()&&actualSmoke.smoke?'SMOKE':'HEAT',intake:s.intake_pwm,exhaust:s.exhaust_pwm}:originalCombo();};
const originalCfd=cfd;
cfd=function(){
  if(state.page==='cfd-current'){
    const s=measured();
    return actualHeading('CFD · 현재 팬 조합','현재 장치 PWM을 표시합니다. 실시간 CFD 계산이나 검증된 기류 영상은 연결되지 않았습니다.','CFD REFERENCE')+`<section class="card live-connection"><h2>현재 장치 피드백</h2><p>흡기 ${liveNumber(s?.intake_pwm,0)}% · 배기 ${liveNumber(s?.exhaust_pwm,0)}%</p><p class="note">실제 기류 분포로 오해하지 않도록 개념 애니메이션은 표시하지 않습니다. 등록할 사전 계산 영상은 선택 팬 조합 메뉴에서 확인할 수 있습니다.</p></section>`;
  }
  return originalCfd();
};
function refreshMeasurements(){
  for(const [id,build] of [['actual-metrics',actualMetrics],['actual-fans',actualFans],['actual-connections',actualConnections],['actual-trends',actualTrendCards]]){const el=document.getElementById(id);if(el)el.innerHTML=build();}
  document.querySelectorAll('[data-device-status]').forEach(el=>{const status=actualDeviceStatus(deviceData[Number(el.dataset.deviceStatus)]);el.textContent=status.label;el.className='badge '+(status.ok?'green':'');});
  const header=document.querySelector('.demo-tag');
  if(header)header.textContent=state.page.startsWith('cfd')?'CFD 참고 자료':measured()?'실측 데이터':'센서 수신 대기';
  const system=document.querySelector('.system-tag');if(system)system.textContent=measured()?'● Jetson 실측 수신 중':'● 장비 연결 확인 중';
  const note=document.querySelector('.sidebar-bottom p');if(note)note.textContent='온습도 · 미세먼지 · 팬 · 카메라';
  const foot=document.querySelector('.page-footer');if(foot)foot.lastElementChild.textContent='Jetson 실측 모니터링 · 팬 제어 명령 전송 없음';
  const head=document.querySelector('.page-heading .badge');if(head&&!state.page.startsWith('cfd')&&state.page!=='live'){head.textContent=measured()?'실측 데이터 수신 중':'센서 수신 대기';head.className='badge '+(measured()?'green':'');}
}
const measurementBaseRender=render;
render=function(){measurementBaseRender();refreshMeasurements();};
window.addEventListener('airguard:telemetry',refreshMeasurements);
window.addEventListener('airguard:smoke-status',event=>{
  actualSmoke=event.detail;smokeReceivedAt=performance.now();refreshMeasurements();
  const el=document.getElementById('actual-events');
  if(el){const open=[...el.querySelectorAll('details')].map(d=>d.open);el.innerHTML=actualEventList();el.querySelectorAll('details').forEach((d,i)=>d.open=!!open[i]);}
});
