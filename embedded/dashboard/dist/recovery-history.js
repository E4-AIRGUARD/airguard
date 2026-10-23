'use strict';
const recoveryState={date:'',followLatest:true,category:'all',status:'all',page:1,list:null,detail:null,selected:null,loading:false,detailLoading:false,error:'',detailError:'',request:0,detailRequest:0};
const recoveryCategories={pm25:'PM2.5',temperature:'온도',smoke:'영상 연기',combined:'온도·PM 복합 기준',unknown:'항목 미기록'};
const recoveryStatuses={active:'이상 진행',completed:'회복 완료',interrupted:'중단 · 회복 미확인',post:'회복 후 기록'};
const recoveryLiveEvents=new Map();
let recoveryRetryTimer=null;
function recoveryShownEvent(event){
  const live=recoveryLiveEvents.get(event.event_id);
  return live?.status==='completed'&&event.status!=='completed'?{...event,...live}:event;
}
function recoveryEventDate(event){return event.started_at?new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(event.started_at)):'';}
function recoveryDisplayDate(){
  const s=recoveryState;
  return s.followLatest?[s.date,...[...recoveryLiveEvents.values()].map(recoveryEventDate)].sort().at(-1):s.date;
}
function recoveryVisibleEvents(){
  const s=recoveryState,date=recoveryDisplayDate(),events=new Map();
  for(const event of s.list?.events||[])events.set(event.event_id,recoveryShownEvent(event));
  if(s.page===1){
    const oldest=s.list?.events?.at(-1)?.started_at;
    for(const event of recoveryLiveEvents.values()){
      if(!events.has(event.event_id)&&(!oldest||event.started_at>=oldest))events.set(event.event_id,event);
    }
  }
  return [...events.values()].filter(e=>(!e.started_at||recoveryEventDate(e)===date)&&
    (s.category==='all'||e.category===s.category)&&(s.status==='all'||e.status===s.status))
    .sort((a,b)=>Date.parse(b.started_at)-Date.parse(a.started_at));
}
function recoveryScheduleRetry(){
  clearTimeout(recoveryRetryTimer);recoveryRetryTimer=null;
  const s=recoveryState,id=s.selected;
  if(!s.awaitingCompletion||!id||state.page!=='events')return;
  recoveryRetryTimer=setTimeout(()=>{
    recoveryRetryTimer=null;
    if(s.selected===id&&s.awaitingCompletion&&state.page==='events')loadRecoveryDetail(id,true);
  },1000);
}
window.addEventListener('airguard:telemetry',event=>{
  const data=event.detail;
  if(!data?.available||data.stale||!Array.isArray(data.integratedControl?.recoveryEvents))return;
  let changed=false;
  for(const row of data.integratedControl.recoveryEvents){
    if(!row.event_id||!['active','completed','interrupted'].includes(row.status))continue;
    const previous=recoveryLiveEvents.get(row.event_id);
    if(previous?.status==='completed'&&row.status!=='completed')continue;
    if(recoveryChanged(previous,row)){recoveryLiveEvents.set(row.event_id,row);changed=true;}
  }
  while(recoveryLiveEvents.size>200)recoveryLiveEvents.delete(recoveryLiveEvents.keys().next().value);
  if(!changed)return;
  const s=recoveryState;
  if(recoveryLiveEvents.get(s.selected)?.status==='completed'&&s.detail?.event.status!=='completed'){
    s.awaitingCompletion=true;
    if(!s.detailLoading&&!s.detailBusy)recoveryScheduleRetry();
  }
  renderRecovery(true);
});
function recoveryTime(value){return value?new Date(value).toLocaleString('ko-KR',{timeZone:'Asia/Seoul',hour12:false}):'—';}
function recoveryClock(value){return value?new Date(value).toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul',hour12:false}):'—';}
function recoveryBadge(event){event=recoveryShownEvent(event);return badge(esc(recoveryStatuses[event.status]||event.status),event.status==='completed'?'green':event.status==='active'?'red':'');}
function recoveryCriteria(event){
  if(event.category==='smoke')return '카메라의 연기 감지·해제 판정';
  const c=event.criteria||{},thresholds=c.thresholds||{};
  const items=[];
  if(Number.isFinite(thresholds.pm25??c.pm_limit))items.push(`PM2.5 ≤ ${thresholds.pm25??c.pm_limit} µg/m³`);
  if(Number.isFinite(thresholds.temperature))items.push(`온도 ${c.recovery_operator==='<'?'<':'≤'} ${thresholds.temperature} °C`);
  if(Number.isFinite(c.hold_seconds))items.push(`정상 유지 ${c.hold_seconds}초`);
  return items.join(' · ')||'기준 미기록';
}
function recoveryThreshold(event,key){
  if(key!=='pm25'&&key!=='temperature')return null;
  const criteria=event.criteria||{};
  const limit=criteria.thresholds?.[key]??(key==='pm25'?criteria.pm_limit:null);
  return Number.isFinite(limit)?limit:null;
}
function recoveryPowerW(sample){
  // Same fan model as integrations/handoff_v6/src/fan_power.py.
  const rpm=[sample.intake_rpm,sample.exhaust_rpm];
  if(!rpm.every(v=>Number.isFinite(v)&&v>=0))return null;
  return rpm.reduce((sum,v)=>sum+5*.27*(v/7500)**3,0);
}
function recoveryDetailMarkup(){
  const s=recoveryState;
  if(s.awaitingCompletion){
    const event=recoveryShownEvent(s.detail?.event||recoveryLiveEvents.get(s.selected)||{event_id:s.selected,status:'active'});
    return `<section class="card recovery-detail" aria-busy="true"><div class="card-head"><h2>선택 사건</h2>${recoveryBadge(event)}</div><p class="note">이력번호 : ${esc(s.selected)}</p><div class="recovery-loading" role="status"><span class="recovery-spinner" aria-hidden="true"></span><strong>불러오는 중</strong><p>사건 기록의 DB 저장 완료를 기다리고 있습니다.</p><small>${s.detailError?'연결을 다시 확인하고 있습니다. · ':''}1초마다 추가 조회합니다.</small></div></section>`;
  }
  if(s.detailLoading)return '<section class="card recovery-detail" aria-live="polite">사건의 측정값과 사진을 불러오는 중…</section>';
  if(s.detailError)return `<section class="card recovery-detail" role="status">${esc(s.detailError)}</section>`;
  if(!s.detail)return '<section class="card recovery-detail"><div class="empty-events">목록에서 사건을 선택하면 실측 추이와 사진이 표시됩니다.</div></section>';
  const {event:e,samples,photos}=s.detail;
  const photoCount=s.detail.photoCount??photos.length,previewPhotos=photos.slice(0,6);
  const rows=samples.map(r=>({timestamp:Date.parse(r.measured_at)/1000,pm25:r.pm25,temperature:r.temperature,power_w:recoveryPowerW(r)}));
  const charts=e.category==='smoke'?[]:[['pm25','PM2.5','µg/m³'],['temperature','온도','°C']].filter(([key])=>!['pm25','temperature'].includes(e.category)||key===e.category);
  if(e.category!=='smoke')charts.push(['power_w','팬 전력 소모','W']);
  return `<section class="card recovery-detail"><div class="card-head"><h2>${esc(recoveryCategories[e.category])} 사건</h2>${recoveryBadge(e)}</div>
    <p class="note">이력번호 : ${esc(e.event_id)}</p>
    <dl class="recovery-times"><div><dt>이상 시작</dt><dd>${esc(recoveryTime(e.started_at))}</dd></div><div><dt>최초 기준 도달</dt><dd>${esc(recoveryTime(e.first_reached_at))}</dd></div><div><dt>회복 확정</dt><dd>${esc(recoveryTime(e.recovered_at))}</dd></div><div><dt>기록 종료</dt><dd>${esc(recoveryTime(e.recording_ended_at))}</dd></div></dl>
    <p><b>회복 소요 ${e.recovery_seconds==null?'미확정':liveNumber(e.recovery_seconds,1)+'초'}</b></p><p class="note">${esc(recoveryCriteria(e))}</p>
    ${e.categoryBasis==='configured_thresholds'?'<p class="note">항목은 저장된 판정 기준으로 구분했습니다. 개별 센서가 사건을 시작시켰는지는 기록되지 않았습니다.</p>':''}
    <div class="recovery-charts">${charts.map(([key,title,unit])=>{const power=key==='power_w';return `<div><h3>${title}${power?' · 추정':' 회복 추이'}</h3>${measuredChart(rows,key,title,unit,{width:380,maxGap:2.5,timeFormat:'24h-minute',threshold:recoveryThreshold(e,key),estimated:power,emptyMessage:power?'양쪽 팬 RPM 기록이 2개 이상 있어야 전력을 표시할 수 있습니다.':''})}${power?'<p class="note">흡기·배기 팬 합산 · RPM 기반 추정값이며 실측 전력이 아닙니다. 팬당 5 V × 0.27 A × (RPM / 7,500)³. RPM 미기록 구간은 표시하지 않습니다.</p>':''}</div>`;}).join('')}</div>
    <p class="note">조회 표본 ${samples.length}개 · 시각 KST · 센서 오류는 빈 값으로 표시합니다.${s.detail.samplesTruncated?' 상세 조회는 처음 20,000개 표본입니다. 전체 기록은 CSV로 확인하세요.':''}</p>
    <div class="recovery-detail-actions"><a class="button" href="/api/recovery/events/${encodeURIComponent(e.event_id)}/csv" download>${icon('download')}선택 사건 전체 CSV</a>${photoCount?`<a class="button" href="/api/recovery/events/${encodeURIComponent(e.event_id)}/photos.zip" download>${icon('download')}사건 사진 전체 다운로드 (ZIP)</a>`:'<button class="button" disabled>사건 사진 전체 다운로드 (ZIP)</button>'}</div>
    <h3>사건 사진 <small>전체 ${photoCount}장 · 미리보기 최대 6장</small></h3><div class="event-photos recovery-photos">${previewPhotos.length?previewPhotos.map(p=>`<figure><figcaption>${esc(({start:'발생',progress:'진행',recovered:'회복',end:'종료'})[p.stage]||p.stage)} · ${esc(recoveryTime(p.captured_at))}</figcaption>${p.available?`<img data-recovery-photo src="${esc(p.url)}" alt="${esc(p.stage)} 시점 사건 사진" loading="lazy"><p class="note" hidden>원본 사진을 불러올 수 없습니다.</p>`:'<div class="photo-empty">원본 사진 없음</div>'}</figure>`).join(''):'<p class="note">이 사건에 저장된 사진이 없습니다.</p>'}</div>
    <details><summary>측정 표본 · 최근 20개</summary><div class="live-table"><table><thead><tr><th>시각 (KST)</th><th>PM2.5</th><th>온도</th><th>습도</th><th>흡기/배기 PWM</th><th>흡기/배기 RPM</th><th>구간</th></tr></thead><tbody>${samples.slice(-20).map(r=>`<tr><td>${esc(recoveryTime(r.measured_at))}</td><td>${liveNumber(r.pm25)}</td><td>${liveNumber(r.temperature)}</td><td>${liveNumber(r.humidity)}</td><td>${liveNumber(r.intake_pwm,0)} / ${liveNumber(r.exhaust_pwm,0)}</td><td>${liveNumber(r.intake_rpm,0)} / ${liveNumber(r.exhaust_rpm,0)}</td><td>${esc(r.phase)}</td></tr>`).join('')}</tbody></table></div></details></section>`;
}
function recoveryPaginationMarkup(list){
  if(!list)return '';
  const start=Math.floor((list.page-1)/5)*5+1,end=Math.min(start+4,list.pages);
  const numbers=Array.from({length:end-start+1},(_,i)=>start+i);
  return `<nav class="pagination" aria-label="저장 사건 페이지"><button data-recovery-page="${Math.max(1,start-5)}" ${start===1?'disabled':''} aria-label="이전 페이지 묶음">&lt;</button>${numbers.map(page=>`<button data-recovery-page="${page}" aria-label="${page}페이지" ${page===list.page?'aria-current="page"':''}>${page}</button>`).join('')}<button data-recovery-page="${end+1}" ${end>=list.pages?'disabled':''} aria-label="다음 페이지 묶음">&gt;</button></nav>`;
}
function recoveryMarkup(){
  const s=recoveryState,l=s.list,visibleEvents=recoveryVisibleEvents();
  const status=s.error|| (s.loading?'저장 기록을 조회하고 있습니다.':!l?'저장 기록 연결 대기':'');
  return `<div class="toolbar recovery-toolbar"><div class="toolbar-group"><label>기록 날짜 (KST) <input type="date" id="recovery-date" value="${esc(s.date)}"></label><label>항목 <select id="recovery-category">${Object.entries({all:'전체 항목',...recoveryCategories}).map(([k,v])=>`<option value="${k}" ${s.category===k?'selected':''}>${v}</option>`).join('')}</select></label><label>상태 <select id="recovery-status">${Object.entries({all:'전체 상태',...recoveryStatuses}).map(([k,v])=>`<option value="${k}" ${s.status===k?'selected':''}>${v}</option>`).join('')}</select></label></div><button class="button" data-recovery-refresh ${s.loading?'disabled':''}>${s.loading?'조회 중…':'새로고침'}</button></div>
    <label class="recovery-follow"><input id="recovery-follow" type="checkbox" ${s.followLatest?'checked':''}> 최근 기록 날짜 따라가기 · 10초마다 확인, 변경 시 갱신</label>${status?`<p class="note" role="status">${esc(status)}</p>`:''}
    ${l?.latestDate&&l.latestDate!==s.date?`<button class="text-link" data-recovery-latest>최근 기록 날짜 ${esc(l.latestDate)} 보기 →</button>`:''}
    ${l?.truncated?'<p class="note">선택 날짜의 최신 5,000건을 표시합니다.</p>':''}
    <div class="events-layout recovery-layout"><section class="card events-list"><h2>저장된 이상·회복 사건</h2><div class="live-table"><table><thead><tr><th>발생 시각 (KST)</th><th>항목</th><th>상태</th><th>회복 시간</th></tr></thead><tbody>${visibleEvents.map(e=>`<tr class="${s.selected===e.event_id?'selected':''}"><td><button class="event-select" data-recovery-event="${esc(e.event_id)}">${esc(recoveryClock(e.started_at))}</button><small class="recovery-device">${esc(e.device_id)}</small></td><td>${esc(recoveryCategories[e.category])}</td><td>${recoveryBadge(e)}</td><td>${e.recovery_seconds==null?'—':liveNumber(e.recovery_seconds,1)+'초'}</td></tr>`).join('')}</tbody></table></div>
    ${!visibleEvents.length?`<div class="empty-events">${s.loading?'조회 중…':s.error?'DB 연결 상태를 확인한 뒤 새로고침하세요.':'선택한 조건에 저장된 사건이 없습니다.'}</div>`:''}
    ${recoveryPaginationMarkup(l)}
    <p class="note">Aiven에는 이상 사건 중심의 기록이 저장됩니다. 실시간 센서 값은 메인 대시보드와 트렌드에서 확인하세요.</p></section>${recoveryDetailMarkup()}</div>`;
}
function renderRecovery(preserveView=false){
  const el=document.getElementById('recovery-history');
  if(el){
    const expanded=preserveView&&el.querySelector('.recovery-detail details')?.open;
    el.innerHTML=recoveryMarkup();
    if(expanded){const details=el.querySelector('.recovery-detail details');if(details)details.open=true;}
  }
  
}
function recoveryChanged(before,after){return JSON.stringify(before)!==JSON.stringify(after);}
async function loadRecoveryDetail(id,background=false){
  const s=recoveryState;
  if(background&&s.detailBusy===id)return;
  clearTimeout(recoveryRetryTimer);recoveryRetryTimer=null;
  const seq=++s.detailRequest;s.selected=id;s.detailBusy=id;
  let changed=!background;
  if(!background){
    const live=recoveryLiveEvents.get(id),saved=s.list?.events.find(event=>event.event_id===id);
    s.awaitingCompletion=live?.status==='completed'||['active','post'].includes(live?.status||saved?.status);
    s.detail=null;s.detailError='';s.detailLoading=true;renderRecovery();
  }
  try{
    const r=await fetch('/api/recovery/events/'+encodeURIComponent(id),{cache:'no-store',signal:AbortSignal.timeout(20000)});
    if(!r.ok)throw new Error();const data=await r.json();
    if(seq!==s.detailRequest)return;
    const waiting=['active','post'].includes(data.event.status)||
      (recoveryLiveEvents.get(id)?.status==='completed'&&data.event.status!=='completed');
    changed=changed||!!s.detailError||s.awaitingCompletion!==waiting||recoveryChanged(s.detail,data);s.detail=data;s.detailError='';s.awaitingCompletion=waiting;
    if(data.event.status==='completed'&&s.list){
      s.list.events=s.list.events.map(event=>event.event_id===id?data.event:event);
    }
  }catch{
    if(seq!==s.detailRequest)return;
    const error='사건 상세를 불러올 수 없습니다. 목록의 사건을 다시 선택하세요.';
    changed=changed||s.detailError!==error;s.detailError=error;
  }
  if(seq===s.detailRequest){s.detailLoading=false;s.detailBusy=null;if(changed)renderRecovery(background);recoveryScheduleRetry();}
}
async function loadRecovery(background=false){
  const s=recoveryState;if(background&&s.polling)return;
  if(background)s.polling=true;
  const seq=++s.request;s.loading=true;
  if(!background){++s.detailRequest;clearTimeout(recoveryRetryTimer);s.detailBusy=null;s.awaitingCompletion=false;s.error='';s.list=null;s.detail=null;s.detailLoading=false;s.detailError='';renderRecovery();}
  const query=new URLSearchParams({category:s.category,status:s.status,page:String(s.page)});if(s.date&&!s.followLatest)query.set('date',s.date);
  try{
    const r=await fetch('/api/recovery/events?'+query,{cache:'no-store',signal:AbortSignal.timeout(20000)});
    if(!r.ok)throw new Error();const data=await r.json();if(seq!==s.request)return;
    const changed=!background||!!s.error||recoveryChanged(s.list,data),previousSelected=s.selected;
    s.list=data;s.date=data.date;s.page=data.page;s.loading=false;s.error='';
    const visible=recoveryVisibleEvents();
    s.selected=visible.some(e=>e.event_id===s.selected)?s.selected:visible[0]?.event_id||null;
    if(s.selected!==previousSelected){++s.detailRequest;clearTimeout(recoveryRetryTimer);s.detailBusy=null;s.awaitingCompletion=false;s.detail=null;s.detailLoading=false;s.detailError='';}
    if(!s.selected)s.detail=null;
    if(changed)renderRecovery(background&&s.selected===previousSelected);
    // Samples and photos can change even when the list metadata stays the same.
    if(s.selected&&!s.detailLoading&&!s.awaitingCompletion)await loadRecoveryDetail(s.selected,background&&s.detail?.event.event_id===s.selected);
    if(seq===s.request)return data;
  }catch{
    if(seq!==s.request)return;
    const error='Aiven 기록 연결 실패 · 이전 결과를 현재 조회 결과로 표시하지 않습니다.';
    const changed=!background||s.error!==error||!!s.list||!!s.detail;
    s.loading=false;s.error=error;
    if(!s.awaitingCompletion){++s.detailRequest;s.detailBusy=null;s.detailLoading=false;s.list=null;s.detail=null;}
    if(changed)renderRecovery(background);
  }finally{if(background)s.polling=false;}
}
eventsPage=function(){return actualHeading('이상·회복 기록','Aiven에 저장된 사건의 측정값과 사진을 조회합니다.','RECOVERY HISTORY')+`<section id="recovery-history">${recoveryMarkup()}</section>`;};
const recoveryBaseRender=render;
render=function(){recoveryBaseRender();recoveryScheduleRetry();if(state.page==='events'&&!recoveryState.list&&!recoveryState.loading&&!recoveryState.error)loadRecovery();};
document.addEventListener('change',e=>{
  if(e.target.id==='recovery-follow'){recoveryState.followLatest=e.target.checked;recoveryState.page=1;loadRecovery();return;}
  const keys={'recovery-date':'date','recovery-category':'category','recovery-status':'status'},key=keys[e.target.id];
  if(key){recoveryState[key]=e.target.value;if(key==='date')recoveryState.followLatest=false;recoveryState.page=1;loadRecovery();}
});
document.addEventListener('click',e=>{
  const b=e.target.closest('[data-recovery-event],[data-recovery-page],[data-recovery-refresh],[data-recovery-latest]');if(!b||b.disabled)return;
  if(b.dataset.recoveryEvent)loadRecoveryDetail(b.dataset.recoveryEvent);
  else if(b.dataset.recoveryPage){recoveryState.page=Number(b.dataset.recoveryPage);loadRecovery();}
  else if(b.hasAttribute('data-recovery-latest')){recoveryState.followLatest=true;recoveryState.date=recoveryState.list.latestDate;recoveryState.page=1;loadRecovery();}
  else loadRecovery();
});
document.addEventListener('error',e=>{if(e.target.matches?.('[data-recovery-photo]')){e.target.hidden=true;e.target.nextElementSibling.hidden=false;}},true);
setInterval(()=>{if(state.page==='events'&&!document.hidden&&!recoveryState.loading&&!recoveryState.detailLoading&&!['INPUT','SELECT'].includes(document.activeElement?.tagName))loadRecovery(true);},10000);
