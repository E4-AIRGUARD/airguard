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
function startDeviceLink() {
  const style=document.createElement('style');
  style.textContent='.live-connection{padding:22px;margin-bottom:18px}.live-connection h2{font-size:16px;margin:0 0 12px}.live-form{display:flex;gap:10px;margin-top:8px;flex-wrap:wrap}.live-form input{flex:1;min-width:200px;padding:10px;border:1px solid #cbd5e1;border-radius:6px}.live-metric{padding:22px;margin-bottom:18px}.live-json{white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto}.live-table{overflow-x:auto}.live-table table{width:100%;border-collapse:collapse}.live-table td,.live-table th{padding:10px;text-align:left;border-bottom:1px solid #edf1f7}.live-connection .chart{width:100%}';
  document.head.appendChild(style);
  async function pollTemperature() {
    try {const selectedDate=typeof temperatureEventDate!=='undefined'?temperatureEventDate:'';const response=await fetch(`${sensorAddress()}/api/temperature?seconds=${state.page==='trends'?state.range*60:300}${selectedDate?'&date='+encodeURIComponent(selectedDate):''}`,{cache:'no-store',signal:AbortSignal.timeout(3500)});if(!response.ok)throw new Error();temperatureData=await response.json();}
    catch {temperatureData={available:false,message:'센서 API 연결 끊김 · Jetson 수신기 상태를 확인하세요.'};}
    window.dispatchEvent(new CustomEvent('airguard:telemetry',{detail:temperatureData}));
    setTimeout(pollTemperature,1000);
  }
  pollTemperature();
}
