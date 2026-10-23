'use strict';
let actualSmoke=null;
let smokeReceivedAt=0;
const measured=()=>temperatureData?.available&&!temperatureData.stale?temperatureData.sensor:null;
const smokeFresh=()=>actualSmoke?.cameraOk&&Number.isFinite(actualSmoke.ageSec)&&actualSmoke.ageSec*1000+performance.now()-smokeReceivedAt<6000;
function actualHeading(title,desc,eyebrow){return heading(title,desc,eyebrow);}
function sensorValueAbnormal(key,data=temperatureData){
  const sensor=data?.available&&!data.stale?data.sensor:null;
  if(key==='pm25_ug_m3')return !!sensor?.pms_ok&&Number.isFinite(sensor.pm25_ug_m3)&&sensor.pm25_ug_m3>50;
  if(key==='temperature_c')return !!sensor?.bme_ok&&Number.isFinite(sensor.temperature_c)&&
    (sensor.temperature_c>=26||(data.temperatureAlert?.state!=='unknown'&&!!data.abnormal));
  return false;
}
function actualMetric(name,ic,value,unit,valid,detail){return `<article class="card metric"><div class="metric-top"><span class="metric-name">${icon(ic)}${name}</span></div><div class="metric-number${sensorValueAbnormal(ic==='dust'?'pm25_ug_m3':ic==='temp'?'temperature_c':'')?' sensor-abnormal':''}">${liveNumber(value)}<small>${unit}</small></div><div class="metric-bottom"><span>${esc(detail)}</span></div></article>`;}
function actualMetrics(){
  const s=measured();
  const fresh=smokeFresh();
  return actualMetric('미세먼지 PM2.5','dust',s?.pm25_ug_m3,'µg/m³',!!s?.pms_ok,`PM1 ${liveNumber(s?.pm1_ug_m3,0)} · PM10 ${liveNumber(s?.pm10_ug_m3,0)} µg/m³`)+
    actualMetric('온도','temp',s?.temperature_c,'°C',!!s?.bme_ok,`BME280 · 기압 ${liveNumber(s?.pressure_hpa)} hPa`)+
    actualMetric('습도','drop',s?.humidity_pct,'%RH',!!s?.bme_ok,'BME280 · 상대습도')+
    `<article class="card metric container-metric"><div class="metric-top"><span class="metric-name">${icon('camera')}비전 연기 감지</span></div><div class="metric-number" style="color:${fresh&&actualSmoke.smoke?'var(--red)':'var(--green)'}">${fresh?(actualSmoke.smoke?'연기 감지':'연기 없음'):'—'}</div><div class="metric-bottom"><span>${fresh?'모델 점수 '+liveNumber(actualSmoke.score,2):'카메라 연결 확인 중'}</span></div></article>`;
}
function actualFans(){
  const s=measured();
  return ['intake','exhaust'].map((key,i)=>{
    const rpm=s?.[key+'_rpm'],pwm=s?.[key+'_pwm'];
    return `<article class="card fan-card"><div class="fan-info"><div class="fan-circle">${icon('fan',rpm>0?'spin':'')}</div><div><div class="fan-title">${i?'배기':'흡기'} 팬 <span>(${i?'OUT':'IN'})</span></div><div class="rpm">${liveNumber(rpm,0)}<small>RPM</small></div><span class="small-label">ESP32 회전수 피드백</span></div></div><div class="fan-bars"><div class="pwm"><span>장치 PWM</span><div class="track"><i style="width:${pwm??0}%"></i></div><b>${liveNumber(pwm,0)}%</b></div><p class="small-label">모니터링 전용 · 제어 명령 전송 없음</p></div></article>`;
  }).join('');
}
function actualConnections(){
  const s=measured();
  const entries=[['ESP32 USB Serial',!!s],['BME280 온습도',!!s?.bme_ok],['PMS5003 미세먼지',!!s?.pms_ok],['Jetson 카메라',smokeFresh()]];
  return entries.map(([name,ok])=>`<div class="connection">${name}<span><i class="dot" style="background:${ok?'#00a68a':'#adbacb'}"></i>${ok?'수신 중':'수신 대기'}</span></div>`).join('')+`<p class="note">마지막 센서 수신: ${esc(liveTime(temperatureData?.sensor?.timestamp))}<br>${esc(temperatureData?.message||'연결 중…')}</p>`;
}
dashboard=function(){return actualHeading('대시보드','Jetson에 연결된 센서와 카메라의 실시간 측정값입니다.','LIVE OVERVIEW')+`
 <div class="dashboard-layout"><div class="metrics" id="actual-metrics">${actualMetrics()}</div>
 <section class="dashboard-fans"><h2 class="section-label">팬 운전 상태 <span>ESP32 장치 PWM·회전수 피드백</span></h2><div class="fans" id="actual-fans">${actualFans()}</div></section>
 <div class="dashboard-bottom"><section class="card camera-card"><div class="card-head"><h2>${icon('camera')}챔버 CCTV</h2>${badge('카메라 연결 대기')}</div><div class="camera" id="camera-view"><img hidden alt="Jetson 카메라 실시간 영상"><div class="camera-top"><span class="camera-tag">CAM 01 &nbsp; CHAMBER</span><span class="camera-tag">JETSON CAMERA</span></div><div class="camera-bottom"><div><b>Jetson 카메라 · 실시간</b><small>연기 감지에 사용하는 카메라 영상</small></div><button class="camera-action" id="camera-expand" aria-label="실시간 CCTV 전체 화면">${icon('expand')}</button></div></div><div class="camera-footer"><span>실시간 카메라</span><span>연결 대기</span></div></section>
 <div class="right-stack"><section class="card"><div class="card-head"><h2>${icon('bell')}최근 알림</h2><a class="text-link" href="#events">전체 보기 →</a></div><div class="activity"><p class="note" data-live-empty>현재 서버 세션의 감지 기록을 기다립니다.</p></div></section><section class="card"><div class="card-head"><h2>${icon('link')}실제 연결 상태</h2><a class="text-link" href="#hardware">상세 →</a></div><div class="connections" id="actual-connections">${actualConnections()}</div></section></div></div></div>`;};

// Split traces at missing measurements and acquisition gaps; no synthetic samples.
function measuredChart(rows,key,title,unit,options={}){
  const clean=rows.filter(r=>Number.isFinite(r.timestamp));
  const points=clean.filter(r=>Number.isFinite(r[key]));
  if(points.length<2)return `<div class="empty-events">${esc(options.emptyMessage||'실제 측정값 2개 이상 수신 후 그래프가 표시됩니다.')}</div>`;
  const threshold=Number.isFinite(options.threshold)?options.threshold:null;
  const values=points.map(r=>r[key]);
  if(threshold!==null)values.push(threshold);
  const lo=Math.min(...values),hi=Math.max(...values);
  const pad=Math.max((hi-lo)*.15,1),min=Math.max(0,lo-pad),max=hi+pad;
  const start=clean[0].timestamp,end=clean.at(-1).timestamp;
  const width=options.width||800,left=options.left??45,right=width-(options.right??30);
  const x=t=>left+(t-start)/Math.max(1,end-start)*(right-left),y=v=>180-(v-min)/(max-min)*155;
  const allowedGap=options.maxGap??Math.max(5,state.range*60/600*2.5);
  let paths=[],segment=[],previous=null;
  for(const row of clean){
    if(!Number.isFinite(row[key])||(previous&&row.timestamp-previous.timestamp>allowedGap)){if(segment.length)paths.push(segment);segment=[];}
    if(Number.isFinite(row[key]))segment.push(`${x(row.timestamp)},${y(row[key])}`);
    previous=row;
  }
  if(segment.length)paths.push(segment);
  const timeOptions=options.timeFormat==='24h-minute'?{timeZone:'Asia/Seoul',hourCycle:'h23',hour:'2-digit',minute:'2-digit'}:{timeZone:'Asia/Seoul'};
  const thresholdLine=threshold===null?'':`<line class="chart-threshold" x1="${left}" x2="${right}" y1="${y(threshold)}" y2="${y(threshold)}" stroke="#df4963" stroke-width="1.8" stroke-dasharray="6 4"/><text class="threshold-label" x="${right}" y="${y(threshold)-8}" text-anchor="end">임계값 ${esc(threshold)} ${esc(unit)}</text>`;
  return `<svg class="chart" role="img" aria-label="${esc(title)} ${options.estimated?'추정':'실측'} 추이" viewBox="0 0 ${width} 220">${[0,1,2,3,4].map(i=>{const v=min+(max-min)*i/4;return `<line x1="${left}" x2="${right}" y1="${y(v)}" y2="${y(v)}" stroke="#e6ecf3"/><text class="chart-y-label" x="${left-7}" y="${y(v)+4}" text-anchor="end">${v.toFixed(1)}</text>`;}).join('')}${thresholdLine}${paths.map(p=>`<polyline points="${p.join(' ')}" fill="none" stroke="#2985f8" stroke-width="2.4"/>`).join('')}<text x="${left}" y="210">${esc(new Date(start*1000).toLocaleTimeString('ko-KR',timeOptions))}</text><text x="${right}" y="210" text-anchor="end">${esc(new Date(end*1000).toLocaleTimeString('ko-KR',timeOptions))}</text></svg><p class="note">${points.length}개 ${options.estimated?'RPM 기반 추정':'실제'} 표본 · ${esc(unit)} · 결측 구간은 연결하지 않습니다.</p>`;
}
function trendThreshold(rows,key){
  const limit=key==='pm25_ug_m3'?50:key==='temperature_c'?26:null;
  if(limit===null)return null;
  const exceeded=value=>Number.isFinite(value)&&(key==='temperature_c'?value>=limit:value>limit);
  return rows.some(row=>exceeded(row[key]))||exceeded(measured()?.[key])||
    (key==='temperature_c'&&measured()?.bme_ok&&temperatureData?.abnormal)?limit:null;
}
function actualTrendCards(){
  const s=measured(),rows=temperatureData?.history||[];
  return [['PM2.5','pm25_ug_m3','µg/m³'],['온도','temperature_c','°C'],['습도','humidity_pct','%RH']].map(([name,key,unit])=>`<section class="card trend-card"><div class="metric-top"><b>${name}</b></div><div class="metric-number${sensorValueAbnormal(key)?' sensor-abnormal':''}">${liveNumber(s?.[key])}<small>${unit}</small></div>${measuredChart(rows,key,name,unit,{width:420,left:64,right:50,timeFormat:'24h-minute',threshold:trendThreshold(rows,key)})}</section>`).join('');
}
trends=function(){return actualHeading('센서 트렌드','센서 수신기를 시작한 이후의 실제 측정 이력입니다.','LIVE SENSOR ANALYTICS')+`<div class="toolbar"><div class="segmented" role="group" aria-label="조회 기간">${[5,15,60].map(n=>`<button data-range="${n}" class="${state.range===n?'active':''}">${n===60?'1시간':n+'분'}</button>`).join('')}</div><span class="small-label">실측 표본만 표시 · 최대 1시간</span></div><div class="trend-grid" id="actual-trends">${actualTrendCards()}</div>`;};

function actualDeviceStatus(d){
  const s=measured();
  let ok=d.name==='PMS5003'?!!s?.pms_ok:d.name==='BME280'?!!s?.bme_ok:d.kind==='fan'||d.name==='ESP32 DevKitC'?!!s:d.kind==='camera'?smokeFresh():!!s||smokeFresh();
  return {ok,label:ok?'수신 중':'수신 대기'};
}
hardware=function(){return actualHeading('하드웨어 연결 상태','현재 API와 ESP32 피드백으로 확인한 연결 상태입니다.','LIVE HARDWARE')+`<div class="hardware-grid">${deviceData.map((d,i)=>{const status=actualDeviceStatus(d);return `<article class="card hardware-card"><div class="device-picture"><span data-device-status="${i}" class="badge ${status.ok?'green':''}">${status.label}</span><img src="assets/hardware/${d.image}" alt="${esc(d.name)} 제품 사진" loading="lazy"><span class="illustration-label">PRODUCT PHOTO</span></div><div class="device-content"><h2>${d.name}</h2><span class="device-model">${d.model}</span><p>${d.role}</p><dl class="device-specs">${specRows(d)}</dl><div class="device-footer"><span>${d.measure}</span><button class="text-link" data-device="${i}">상세 보기 ↗</button></div><a class="photo-credit" href="${d.photoSource}" target="_blank" rel="noopener noreferrer">사진: ${d.credit} ↗</a></div></article>`;}).join('')}</div>`;};

function actualEventList(){
  const list=actualSmoke?.incidents||[];
  if(!list.length)return '<section class="card"><div class="empty-events">현재 연기 서버 세션에 감지 기록이 없습니다. 감지 시 자동으로 추가됩니다.</div></section>';
  const api=smokeAddress();
  return list.map(e=>`<section class="card live-connection"><h2>${esc(e.id)} ${badge(e.end?'해제':'감지 중',e.end?'green':'red')}</h2><p>시작 ${esc(e.start)}${e.end?' · 해제 '+esc(e.end):''}</p><p>최고 점수 ${liveNumber(e.peak,2)} · 지속 ${e.durationSec==null?'진행 중':esc(e.durationSec)+'초'}</p><details><summary>감지 순간 사진</summary><img class="actual-event-photo" loading="lazy" src="${esc(api)}/event/${encodeURIComponent(e.id)}.jpg" alt="${esc(e.id)} 감지 순간 카메라 사진"></details></section>`).join('');
}
eventsPage=function(){return actualHeading('연기 감지·해제 기록','현재 연기 서버 세션의 실제 사건입니다. 서버 재시작 시 기록이 초기화됩니다.','LIVE EVENT HISTORY')+`<p class="note">최대 30건 · PM2.5·온도 기준 초과 사건 저장은 아직 연결되지 않았습니다.</p><div id="actual-events">${actualEventList()}</div>`;};

let currentCfdSituation='BASE';
const originalCombo=combo;
combo=function(){const s=measured();return state.page==='cfd-current'?{situation:currentCfdSituation,intake:s?.intake_pwm??null,exhaust:s?.exhaust_pwm??null}:originalCombo();};
function currentCfdSignature(){return JSON.stringify(combo());}
function currentCfdPanel(){
  const c=combo(),clip=bundledClip(c),key=clipKey(c);
  return `<div class="cfd-toolbar"><b>흡기 ${liveNumber(c.intake,0)}% · 배기 ${liveNumber(c.exhaust,0)}%</b><div class="combination"><label>상황<select id="current-cfd-situation">${Object.entries(cfdSituations).map(([value,label])=>`<option value="${value}" ${c.situation===value?'selected':''}>${label}</option>`).join('')}</select></label></div></div>${clip?`<video class="cfd-video" controls autoplay muted loop playsinline preload="metadata" src="${esc(clip.url)}" aria-label="${key} 사전 계산 영상"></video>`:`<div class="empty-events">${c.intake===null||c.exhaust===null?'현재 팬 PWM 수신 대기 중입니다.':'현재 팬 조합에 해당하는 영상이 없습니다. 제공 영상은 흡기·배기 각각 35%, 60%, 100% 조합입니다.'}</div>`}<p class="note" data-current-video-error hidden></p>`;
}
function refreshCurrentCfd(){
  const panel=document.getElementById('current-cfd-player');
  if(!panel)return;
  const signature=currentCfdSignature();
  if(panel.dataset.signature===signature)return;
  panel.innerHTML=currentCfdPanel();
  panel.dataset.signature=signature;
  panel.querySelector('video')?.addEventListener('error',()=>{
    const note=panel.querySelector('[data-current-video-error]');
    note.textContent='영상을 재생할 수 없습니다. 파일 경로와 브라우저 지원 형식을 확인해 주세요.';
    note.hidden=false;note.setAttribute('role','alert');
  });
}
document.addEventListener('change',event=>{
  if(event.target.id==='current-cfd-situation'&&Object.hasOwn(cfdSituations,event.target.value)){
    currentCfdSituation=event.target.value;
    refreshCurrentCfd();
  }
});
const originalCfd=cfd;
cfd=function(){
  if(state.page==='cfd-current'){
    return actualHeading('CFD · 현재 팬 조합','','CFD REFERENCE')+`<section class="card" id="current-cfd-player">${currentCfdPanel()}</section>`;
  }
  return originalCfd();
};
function refreshMeasurements(){
  refreshCurrentCfd();
  for(const [id,build] of [['actual-metrics',actualMetrics],['actual-fans',actualFans],['actual-connections',actualConnections],['actual-trends',actualTrendCards]]){const el=document.getElementById(id);if(el)el.innerHTML=build();}
  document.querySelectorAll('[data-device-status]').forEach(el=>{const status=actualDeviceStatus(deviceData[Number(el.dataset.deviceStatus)]);el.textContent=status.label;el.className='badge '+(status.ok?'green':'');});
  const system=document.querySelector('.system-tag');if(system)system.textContent=measured()?'● Jetson 수신 중':'● 장비 연결 확인 중';
  const note=document.querySelector('.sidebar-bottom p');if(note)note.textContent='온습도 · 미세먼지 · 팬 · 카메라';
}
const measurementBaseRender=render;
render=function(){measurementBaseRender();refreshMeasurements();};
window.addEventListener('airguard:telemetry',refreshMeasurements);
window.addEventListener('airguard:smoke-status',event=>{
  actualSmoke=event.detail;smokeReceivedAt=performance.now();refreshMeasurements();
  const el=document.getElementById('actual-events');
  if(el){const open=[...el.querySelectorAll('details')].map(d=>d.open);el.innerHTML=actualEventList();el.querySelectorAll('details').forEach((d,i)=>d.open=!!open[i]);}
});
