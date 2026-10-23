'use strict';
// A persistent, read-only summary outside the page content survives navigation.
function globalAlertItems(data, smoke, cameraFresh){
  const items=[],sensor=data?.available&&!data.stale?data.sensor:null;
  if(sensor?.bme_ok&&data.temperatureAlert?.state!=='unknown'&&data.abnormal)
    items.push(`온도 이상 · ${liveNumber(sensor.temperature_c)} °C`);
  if(sensor?.pms_ok&&Number.isFinite(sensor.pm25_ug_m3)&&sensor.pm25_ug_m3>50)
    items.push(`PM2.5 기준 초과 · ${liveNumber(sensor.pm25_ug_m3)} µg/m³`);
  if(cameraFresh&&smoke?.smoke)items.push('카메라 연기 감지');
  return items;
}
(() => {
  const banner=document.createElement('section');
  banner.className='global-abnormal-alert';
  banner.hidden=true;
  banner.setAttribute('role','alert');
  banner.setAttribute('aria-atomic','true');
  document.querySelector('.topbar').after(banner);
  function update(){
    const items=globalAlertItems(temperatureData,actualSmoke,smokeFresh());
    const markup=items.length?`<strong>이상 알림</strong><span>${items.map(esc).join(' / ')}</span><a href="#dashboard">현재 장비 상태 보기 →</a>`:'';
    if(banner.innerHTML!==markup)banner.innerHTML=markup;
    banner.hidden=!items.length;
  }
  window.addEventListener('airguard:telemetry',update);
  window.addEventListener('airguard:smoke-status',update);
  update();
})();
