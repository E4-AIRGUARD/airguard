const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const context=vm.createContext({assert,Date,Intl,URLSearchParams,AbortSignal,setInterval:()=>0,setTimeout:()=>0,clearTimeout(){},
  document:{addEventListener(){},getElementById(){return null;}},window:{addEventListener(){}},
  esc:v=>String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),
  badge:v=>v,icon:()=>'',liveNumber:v=>Number.isFinite(v)?String(v):'—',measuredChart:()=>'<svg></svg>',
  actualHeading:()=>'',actualEventList:()=>'',render(){},state:{page:'dashboard'},temperatureEventDate:'',temperatureData:null,
  eventsPage(){},fetch:async()=>{throw new Error('offline');}});
vm.runInContext(fs.readFileSync('dist/recovery-history.js','utf8'),context);
// Page groups stop at five numbers and never link past the available records.
for(const [page,pages,expected,previous,next] of [
  [1,1,[1],null,null],
  [3,5,[1,2,3,4,5],null,null],
  [5,6,[1,2,3,4,5],null,6],
  [6,6,[6],1,null],
  [10,11,[6,7,8,9,10],1,11],
  [11,11,[11],6,null]
]){
  const html=vm.runInContext(`recoveryPaginationMarkup({page:${page},pages:${pages}})`,context);
  const buttons=[...html.matchAll(/<button\b([^>]*)>(.*?)<\/button>/g)];
  assert.deepEqual(buttons.slice(1,-1).map(b=>Number(b[2])),expected);
  assert.equal(buttons.filter(b=>b[1].includes('aria-current="page"')).length,1);
  assert.equal(Number(buttons.find(b=>b[1].includes('aria-current="page"'))[2]),page);
  for(const [button,target] of [[buttons[0],previous],[buttons.at(-1),next]]){
    assert.equal(/\bdisabled\b/.test(button[1]),target===null);
    if(target!==null)assert.equal(Number(button[1].match(/data-recovery-page="(\d+)"/)[1]),target);
  }
  assert.ok(!html.includes(' / '));
}
vm.runInContext(`
assert.match(recoveryMarkup(),/기록 날짜/);
assert.equal(recoveryCriteria({criteria:{thresholds:{temperature:26,pm25:50},hold_seconds:10}}),'PM2.5 ≤ 50 µg/m³ · 온도 ≤ 26 °C · 정상 유지 10초');
recoveryState.list={events:[],total:0,page:1,pages:1,date:'2026-10-21',latestDate:'2026-10-18'};
recoveryState.date='2026-10-21';assert.match(recoveryMarkup(),/최근 기록 날짜 2026-10-18/);
assert.match(recoveryMarkup(),/선택한 조건에 저장된 사건이 없습니다/);
recoveryState.detail={event:{event_id:'test',device_id:'<script>evil</script>',status:'interrupted',category:'pm25',categoryBasis:'configured_thresholds',criteria:{pm_limit:50,hold_seconds:3}},samples:[],photos:[{stage:'start',available:false,captured_at:null}]};
assert.match(recoveryDetailMarkup(),/원본 사진 없음/);
assert.match(recoveryDetailMarkup(),/회복 소요 미확정/);
assert.ok(!recoveryDetailMarkup().includes('<script>evil'));
assert.match(recoveryDetailMarkup(),/전체 CSV/);
recoveryState.detail.photoCount=205;
recoveryState.detail.photos=Array.from({length:8},(_,i)=>({stage:'progress',available:true,url:'/photo-'+i,captured_at:null}));
const photoMarkup=recoveryDetailMarkup();
assert.equal((photoMarkup.match(/<figure>/g)||[]).length,6);
assert.ok(!photoMarkup.includes('/photo-6'));
assert.match(photoMarkup,/전체 205장/);
assert.ok(photoMarkup.includes('href="/api/recovery/events/test/photos.zip" download'));
assert.ok(photoMarkup.indexOf('/csv')<photoMarkup.indexOf('/photos.zip'));
recoveryState.detail.photoCount=0;recoveryState.detail.photos=[];
assert.match(recoveryDetailMarkup(),/<button class="button" disabled>사건 사진 전체 다운로드/);
assert.equal(recoveryPowerW({intake_rpm:7500,exhaust_rpm:7500}),2.7);
assert.equal(recoveryPowerW({intake_rpm:3750,exhaust_rpm:3750}),.3375);
assert.equal(recoveryPowerW({intake_rpm:0,exhaust_rpm:0}),0);
for(const bad of [null,undefined,NaN,Infinity,-1,'7500']){
  assert.equal(recoveryPowerW({intake_rpm:bad,exhaust_rpm:7500}),null);
  assert.equal(recoveryPowerW({intake_rpm:7500,exhaust_rpm:bad}),null);
}
const chartCalls=[];
measuredChart=(rows,key,title,unit,options)=>{chartCalls.push({rows,key,unit,options});return '<svg></svg>';};
recoveryState.detail.samples=[{measured_at:'2026-10-22T00:00:00Z',pm25:65,temperature:32,humidity:55,intake_rpm:7500,exhaust_rpm:7500},{measured_at:'2026-10-22T00:00:01Z',pm25:45,temperature:25,humidity:56,intake_rpm:null,exhaust_rpm:7500}];
for(const [category,keys] of [['pm25',['pm25','power_w']],['temperature',['temperature','power_w']],['combined',['pm25','temperature','power_w']],['unknown',['pm25','temperature','power_w']]]){
  chartCalls.length=0;
  recoveryState.detail.event.category=category;
  const html=recoveryDetailMarkup();
  assert.deepEqual(chartCalls.map(c=>c.key),keys);
  assert.match(html,/실측 전력이 아닙니다/);
  assert.ok(!html.includes('습도 실측 추이'));
  const power=chartCalls.at(-1);
  assert.equal(power.unit,'W');assert.equal(power.options.estimated,true);
  assert.equal(power.rows[0].power_w,2.7);assert.equal(power.rows[1].power_w,null);
}
`,context);
// Exercise the actual chart renderer, including missing RPM and recording gaps.
const liveSource=fs.readFileSync('dist/live-measurements.js','utf8');
vm.runInContext(liveSource.slice(liveSource.indexOf('function measuredChart('),liveSource.indexOf('function actualTrendCards(')),context);
vm.runInContext(`
const powerRows=[{timestamp:1,power_w:2.7},{timestamp:2,power_w:1.5},{timestamp:3,power_w:null},{timestamp:4,power_w:0},{timestamp:10,power_w:1}];
const plot=measuredChart(powerRows,'power_w','팬 전력 소모','W',{width:380,maxGap:2.5,estimated:true});
assert.match(plot,/viewBox="0 0 380 220"/);
assert.match(plot,/팬 전력 소모 추정 추이/);
assert.equal((plot.match(/<polyline/g)||[]).length,3);
assert.ok(!plot.includes('NaN'));
assert.match(measuredChart([],'power_w','전력','W',{emptyMessage:'RPM 기록 없음'}),/RPM 기록 없음/);
const thresholdPlot=measuredChart([{timestamp:1,temperature:32},{timestamp:2,temperature:34}],'temperature','온도','°C',{threshold:26});
assert.match(thresholdPlot,/stroke-dasharray="6 4"/);
assert.match(thresholdPlot,/임계값 26 °C/);
const thresholdY=Number(thresholdPlot.match(/class="chart-threshold"[^>]*y1="([^"]+)"/)[1]);
assert.ok(thresholdY>=25&&thresholdY<=180,'Threshold stays visible even when all samples exceed it');
assert.ok(!plot.includes('chart-threshold'),'Power has no sensor threshold');
assert.equal(recoveryThreshold({criteria:{thresholds:{temperature:28,pm25:55}}},'temperature'),28);
assert.equal(recoveryThreshold({criteria:{pm_limit:50}},'pm25'),50);
assert.equal(recoveryThreshold({criteria:{thresholds:{temperature:26}}},'power_w'),null);
assert.equal(recoveryThreshold({criteria:{}},'temperature'),null);
const measured=()=>null,temperatureData=null;
assert.equal(trendThreshold([{temperature_c:26}],'temperature_c'),26);
assert.equal(trendThreshold([{pm25_ug_m3:50}],'pm25_ug_m3'),null);
assert.equal(trendThreshold([{pm25_ug_m3:65},{pm25_ug_m3:20}],'pm25_ug_m3'),50);
assert.equal(trendThreshold([{humidity_pct:90}],'humidity_pct'),null);
`,context);
(async()=>{
  await vm.runInContext('loadRecovery()',context);
  vm.runInContext("assert.equal(recoveryState.list,null);assert.match(recoveryState.error,/연결 실패/);",context);
  let finishOld;
  context.fetch=url=>url.endsWith('/old')?new Promise(resolve=>finishOld=resolve):Promise.resolve({ok:true,json:async()=>({event:{event_id:'new'},samples:[],photos:[]})});
  const old=vm.runInContext("loadRecoveryDetail('old')",context);
  await vm.runInContext("loadRecoveryDetail('new')",context);
  finishOld({ok:true,json:async()=>({event:{event_id:'old'}})});await old;
  vm.runInContext("assert.equal(recoveryState.detail.event.event_id,'new');",context);
  // Background checks must not replace the DOM until server data changes.
  let renders=0;
  context.renderRecovery=()=>{renders++;};
  const list={events:[{event_id:'new',status:'completed'}],total:1,page:1,pages:1,date:'2026-10-22'};
  const detail={event:{event_id:'new',status:'completed'},samples:[],photos:[]};
  context.fetch=async url=>({ok:true,json:async()=>JSON.parse(JSON.stringify(url.includes('?')?list:detail))});
  await vm.runInContext('loadRecovery()',context);
  renders=0;
  await vm.runInContext('loadRecovery(true)',context);
  assert.equal(renders,0,'unchanged active events must not redraw');
  detail.samples.push({measured_at:'2026-10-22T00:00:00Z',pm25:55});
  await vm.runInContext('loadRecovery(true)',context);
  assert.equal(renders,1,'new samples redraw even if event metadata is unchanged');
  renders=0;
  await vm.runInContext('loadRecovery(true)',context);
  assert.equal(renders,0);
  list.events[0].recovery_seconds=25;detail.event.recovery_seconds=25;
  await vm.runInContext('loadRecovery(true)',context);
  assert.ok(renders>0,'recovery status changes must redraw');
  renders=0;
  detail.photos.push({photo_id:'photo-1'});
  await vm.runInContext('loadRecovery(true)',context);
  assert.equal(renders,1,'late photos on completed events must redraw');
  renders=0;
  list.events.unshift({event_id:'other',status:'active'});list.total=2;
  await vm.runInContext('loadRecovery(true)',context);
  assert.equal(renders,1,'new event redraws list');
  vm.runInContext("assert.equal(recoveryState.selected,'new');",context);
  renders=0;
  context.fetch=async()=>{throw new Error('offline');};
  await vm.runInContext('loadRecovery(true)',context);
  assert.equal(renders,1,'connection failure must be shown');
  renders=0;
  await vm.runInContext('loadRecovery(true)',context);
  assert.equal(renders,0,'repeated identical failures must not redraw');
  console.log('PASS: pagination groups, incident charts, RPM power, CSV links, request races and change-only background refresh');
})().catch(e=>{console.error(e);process.exitCode=1;});
