'use strict';
// One chronological activity stream; recovery comes from recorder decisions.
const recentSavedEvents=new Map();
function recentAlertEntries(){
  const events=new Map(recentSavedEvents);
  for(const event of recoveryState.list?.events||[])events.set(event.event_id,event);
  for(const event of recoveryLiveEvents.values())events.set(event.event_id,recoveryShownEvent(event));
  const rows=[];
  for(const event of events.values()){
    if(!['pm25','temperature'].includes(event.category))continue;
    const label=event.category==='temperature'?'온도':'PM2.5';
    if(event.started_at)rows.push({id:event.event_id+'-start',time:event.started_at,title:label+' 이상',kind:'bell',detail:'이상 감지 · 이력번호 : '+event.event_id});
    if(event.status==='completed'&&event.recovered_at)rows.push({id:event.event_id+'-end',time:event.recovered_at,title:label+' 회복',kind:'check',detail:'회복 완료 · 소요 '+liveNumber(event.recovery_seconds,1)+'초'});
  }
  for(const event of actualSmoke?.incidents||[]){
    rows.push({id:event.id+'-start',time:event.start,title:'영상 연기 감지',kind:'bell',detail:'카메라 감지 기록'});
    if(event.end)rows.push({id:event.id+'-end',time:event.end,title:'영상 연기 해제',kind:'check',detail:'카메라 해제 확인'});
  }
  return rows.filter(row=>Number.isFinite(Date.parse(row.time))).sort((a,b)=>Date.parse(b.time)-Date.parse(a.time)).slice(0,40);
}
function refreshRecentAlerts(){
  if(state.page!=='dashboard')return;
  const box=document.querySelector('.activity');if(!box)return;
  const rows=recentAlertEntries();
  const html=rows.length?rows.map(row=>`<div class="activity-row"><time>${esc(new Date(row.time).toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul',hour12:false,hour:'2-digit',minute:'2-digit'}))}</time><span class="activity-icon">${icon(row.kind)}</span><div><b>${esc(row.title)}</b><p>${esc(row.detail)}</p></div></div>`).join(''):'<p class="note">최근 알림을 기다리고 있습니다.</p>';
  if(box.innerHTML!==html)box.innerHTML=html;
}
const recentAlertsBaseRender=render;
render=function(){recentAlertsBaseRender();refreshRecentAlerts();};
window.addEventListener('airguard:telemetry',refreshRecentAlerts);
window.addEventListener('airguard:smoke-status',refreshRecentAlerts);
loadRecovery(true).then(refreshRecentAlerts);
for(const category of ['temperature','pm25']){
  fetch('/api/recovery/events?category='+category,{cache:'no-store',signal:AbortSignal.timeout(20000)})
    .then(response=>response.ok?response.json():null).then(data=>{
      for(const event of data?.events||[])recentSavedEvents.set(event.event_id,event);
      refreshRecentAlerts();
    }).catch(()=>{});
}
