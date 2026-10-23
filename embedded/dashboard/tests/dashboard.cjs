const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
let csvBlob;
const element={classList:{add(){}},click(){}};
const context=vm.createContext({Intl,Date,Map,Blob,window:{matchMedia:()=>({matches:false})},document:{querySelector:()=>element,createElement:()=>element},URL:{createObjectURL:b=>(csvBlob=b,'blob:test')},setTimeout:()=>0,clearTimeout(){},assert});
const code=fs.readFileSync('dist/app.js','utf8').split("document.querySelectorAll('[data-icon]')")[0];
vm.runInContext(code,context);
vm.runInContext(`
assert.equal(events.length,9);
assert.equal(new Set(events.map(e=>e.id)).size,9);
state.eventPage=1;assert.equal(eventWindow(filteredEvents()).rows.length,5);
state.eventPage=2;assert.equal(eventWindow(filteredEvents()).rows.length,4);
state.filter='active';assert.equal(eventWindow(filteredEvents()).rows.length,1);assert.equal(state.eventPage,1);
state.filter='all';state.date='2000-01-01';assert.equal(eventWindow(filteredEvents()).rows.length,0);
state.date=today();state.eventPage=2;
// Every supplied situation/PWM combination resolves to its permanent original clip.
state.page='cfd-select';
for(const situation of ['BASE','HEAT','SMOKE'])for(const intake of [35,60,100])for(const exhaust of [35,60,100]){
 Object.assign(state,{situation,intake,exhaust});
 const c=combo(),clip=selectedClip(c);
 assert.equal(clip.url,'assets/cfd/'+situation+'/I'+intake+'E'+exhaust+'_'+situation+'.mp4');
 assert.equal(clip.bundled,true);
 assert.ok(cfd().includes('src="'+clip.url+'"'));
 assert.ok(!cfd().includes('id="clip-upload"'));
 assert.ok(!cfd().includes('class="cfd-foot"'));
 assert.ok(!cfd().includes('원본 영상 미등록'));
 assert.ok(cfd().includes('value="BASE"'));
}
for(const intake of [0,50,null,NaN,'35'])assert.equal(bundledClip({situation:'BASE',intake,exhaust:35}),null);
assert.equal(bundledClip({situation:'UNKNOWN',intake:35,exhaust:35}),null);
assert.equal(bundledClip({situation:'BASE',intake:35,exhaust:99}),null);
Object.assign(state,{situation:'BASE',intake:35,exhaust:35});
state.clips.set(clipKey(combo()),{url:'blob:local',name:'local.mp4'});
assert.equal(selectedClip(combo()).url,'blob:local');
assert.ok(cfd().includes('이 브라우저 세션에서만 재생'));
state.clips.clear();assert.equal(selectedClip(combo()).bundled,true);
state.page='cfd-current';assert.equal(selectedClip(combo()).name,'I60E60_SMOKE.mp4');
assert.ok(cfd().includes('현재 조합 · 예시'));
state.page='dashboard';

assert.ok(Math.abs(eventEnergy(events[0])-0.038)<1e-12);
assert.equal(eventTimeline(events[1]).at(-1),138);
assert.equal(eventTimeline(events[2]).at(-1),102);
assert.match(fanReason(events[2]),/온도/);
assert.equal(deviceData.length,6);
assert.equal(deviceData.filter(d=>d.kind==='fan').length,1);
downloadCSV();
`,context);
csvBlob.text().then(text=>{assert.equal(text.trim().split('\r\n').length,10);console.log('PASS: pagination, filter bounds, empty state, energy integration, event timing, 6 device types, CSV exports all 9 filtered records from page 2.');});

const crypto=require('node:crypto');
const manifest=JSON.parse(fs.readFileSync('dist/assets/cfd/manifest.json','utf8'));
assert.equal(manifest.files.length,27);
assert.equal(new Set(manifest.files.map(f=>f.path)).size,27);
for(const file of manifest.files){
 const data=fs.readFileSync('dist/assets/cfd/'+file.path);
 assert.equal(data.length,file.bytes);
 assert.equal(crypto.createHash('sha256').update(data).digest('hex'),file.sha256);
}
console.log('PASS: 27 OpenFOAM combinations, original video hashes, invalid PWM, local override and demo current clip.');

// Live current mode keeps device feedback and never assigns a CFD scenario from it.
context.document.addEventListener=()=>{};
vm.runInContext("let currentSensor=null;const measured=()=>currentSensor;const actualHeading=heading,liveNumber=v=>v==null?'—':String(v);",context);
const liveCode=fs.readFileSync('dist/live-measurements.js','utf8');
vm.runInContext(liveCode.slice(liveCode.indexOf("let currentCfdSituation="),liveCode.indexOf('function refreshMeasurements()')),context);
vm.runInContext(`
state.page='cfd-current';
assert.ok(cfd().includes('흡기 —% · 배기 —%'));
assert.ok(!cfd().includes('<video'));
assert.ok(!cfd().includes('href="#cfd-select"'));
for(const situation of ['BASE','HEAT','SMOKE'])for(const intake of [35,60,100])for(const exhaust of [35,60,100]){
  currentCfdSituation=situation;currentSensor={intake_pwm:intake,exhaust_pwm:exhaust};
  assert.ok(cfd().includes('assets/cfd/'+situation+'/I'+intake+'E'+exhaust+'_'+situation+'.mp4'));
  assert.ok(cfd().includes('controls autoplay muted loop playsinline'));
}
currentSensor={intake_pwm:0,exhaust_pwm:0};assert.ok(!cfd().includes('<video'));
currentSensor={intake_pwm:50,exhaust_pwm:60};assert.ok(!cfd().includes('<video'));
let panelWrites=0;
const testPanel={dataset:{},set innerHTML(value){panelWrites++;},querySelector:()=>null};
document.getElementById=()=>testPanel;
currentSensor={intake_pwm:35,exhaust_pwm:35};
refreshCurrentCfd();refreshCurrentCfd();assert.equal(panelWrites,1,'Unchanged telemetry preserves video playback');
currentSensor={intake_pwm:60,exhaust_pwm:35};refreshCurrentCfd();assert.equal(panelWrites,2);
currentSensor=null;refreshCurrentCfd();assert.equal(panelWrites,3,'Lost feedback clears the previous clip');
state.page='cfd-select';
Object.assign(state,{situation:'HEAT',intake:60,exhaust:100});
assert.ok(cfd().includes('assets/cfd/HEAT/I60E100_HEAT.mp4'));
`,context);
console.log('PASS: current inline videos, 27 exact PWM/scenario matches, missing feedback, unsupported PWM, playback preservation and selected mode.');
