const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const banner={hidden:true,innerHTML:'',setAttribute(){}},listeners={};
let mounts=0;
const context=vm.createContext({
  temperatureData:null,actualSmoke:null,cameraFresh:false,
  liveNumber:v=>Number.isFinite(v)?v.toFixed(1):'—',esc:s=>s,
  document:{createElement:()=>banner,querySelector:()=>({after:()=>mounts++})},
  window:{addEventListener:(name,fn)=>listeners[name]=fn}
});
vm.runInContext('const smokeFresh=()=>cameraFresh;',context);
vm.runInContext(fs.readFileSync('dist/global-alerts.js','utf8'),context);
assert.equal(banner.hidden,true);
context.temperatureData={available:true,stale:false,abnormal:true,temperatureAlert:{state:'active'},sensor:{bme_ok:true,pms_ok:true,temperature_c:32,pm25_ug_m3:65}};
context.actualSmoke={smoke:true};context.cameraFresh=true;
listeners['airguard:telemetry']();
assert.equal(banner.hidden,false);
assert.match(banner.innerHTML,/href="#dashboard">현재 장비 상태 보기/);
for(const label of ['온도 이상','PM2.5 기준 초과','카메라 연기 감지'])assert.ok(banner.innerHTML.includes(label));
// Page selection does not control the persistent banner, including after popup acknowledgment.
for(const page of ['dashboard','trends','events','hardware','cfd-current','cfd-select']){
  context.state={page};listeners['airguard:telemetry']();assert.equal(banner.hidden,false);
}
assert.equal(mounts,1);
context.temperatureData.stale=true;context.cameraFresh=false;
listeners['airguard:smoke-status']();assert.equal(banner.hidden,true);
context.temperatureData.stale=false;context.temperatureData.abnormal=false;
context.temperatureData.sensor.pm25_ug_m3=50;
context.actualSmoke={smoke:false};context.cameraFresh=true;
listeners['airguard:telemetry']();assert.equal(banner.hidden,true,'Normal readings clear the warning');
context.temperatureData.sensor.pm25_ug_m3=51;
listeners['airguard:telemetry']();assert.match(banner.innerHTML,/PM2.5/);
context.temperatureData.sensor.pms_ok=false;
listeners['airguard:telemetry']();assert.equal(banner.hidden,true,'Invalid sensors cannot raise a fresh warning');
context.temperatureData=null;context.actualSmoke=null;
listeners['airguard:telemetry']();assert.equal(banner.hidden,true);
console.log('PASS: global temperature/PM/smoke alerts, all routes, recovery, thresholds, stale and invalid telemetry.');
