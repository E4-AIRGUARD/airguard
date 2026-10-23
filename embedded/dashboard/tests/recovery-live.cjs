const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const listeners={},timers=new Map();let nextTimer=0,queries=0,finish;
const c=vm.createContext({Date,Intl,URLSearchParams,AbortSignal,
  setInterval(){},setTimeout(fn,ms){const id=++nextTimer;timers.set(id,{fn,ms});return id;},clearTimeout(id){timers.delete(id);},
  document:{addEventListener(){},getElementById(){return null;}},window:{addEventListener(name,fn){listeners[name]=fn;}},
  esc:v=>String(v??''),badge:v=>v,icon:()=>'',liveNumber:String,measuredChart:()=>'',actualHeading:()=>'',
  render(){},state:{page:'events'},eventsPage(){},fetch:async()=>{queries++;return {ok:true,json:async()=>({event:{event_id:'a',status:'active'},samples:[],photos:[]})};}});
vm.runInContext(fs.readFileSync('dist/recovery-history.js','utf8'),c);
const run=code=>vm.runInContext(code,c);
const emit=rows=>listeners['airguard:telemetry']({detail:{available:true,stale:false,integratedControl:{recoveryEvents:rows}}});
const event={event_id:'a',category:'pm25',device_id:'jetson',status:'active',started_at:'2026-10-22T06:00:00Z'};
async function tick(){assert.equal(timers.size,1);const [id,timer]=[...timers][0];assert.equal(timer.ms,1000);timers.delete(id);await timer.fn();await new Promise(setImmediate);}
(async()=>{
  run("recoveryState.date='2026-10-22';recoveryState.list={events:[],page:1,pages:1}");
  emit([event]);assert.equal(run('recoveryVisibleEvents()[0].status'),'active');
  await run("loadRecoveryDetail('a')");assert.match(run('recoveryDetailMarkup()'),/불러오는 중/);
  emit([{...event,status:'completed',recovery_seconds:6,recovered_at:'2026-10-22T06:00:06Z'}]);
  assert.match(run("recoveryBadge({event_id:'a',status:'active'})"),/회복 완료/);
  assert.equal(run('recoveryVisibleEvents()[0].recovery_seconds'),6);
  await tick();assert.equal(queries,2);assert.match(run('recoveryDetailMarkup()'),/불러오는 중/);
  c.fetch=async()=>{queries++;throw new Error('offline');};
  await tick();assert.match(run('recoveryDetailMarkup()'),/연결을 다시 확인/);assert.equal(timers.size,1);
  c.fetch=()=>{queries++;return new Promise(resolve=>finish=resolve);};
  await tick();assert.equal(timers.size,0);
  await run("loadRecoveryDetail('a',true)");assert.equal(queries,4,'no duplicate request during slow response');
  c.fetch=async()=>({ok:true,json:async()=>({event:{event_id:'b',status:'completed'},samples:[],photos:[]})});
  await run("loadRecoveryDetail('b')");
  finish({ok:true,json:async()=>({event:{event_id:'a',status:'active'},samples:[],photos:[]})});
  await new Promise(setImmediate);assert.equal(run('recoveryState.detail.event.event_id'),'b');assert.equal(timers.size,0);
  c.fetch=async()=>({ok:false});await run("loadRecoveryDetail('a')");assert.match(run('recoveryDetailMarkup()'),/불러오는 중/);assert.equal(timers.size,1,'missing DB row retries');
  c.fetch=async()=>({ok:true,json:async()=>({event:{...event,status:'completed'},samples:[],photos:[]})});
  await tick();assert.equal(timers.size,0);assert.ok(!run('recoveryDetailMarkup()').includes('불러오는 중'));
  emit([event]);assert.equal(run("recoveryLiveEvents.get('a').status"),'completed','old telemetry cannot regress recovery');
  c.fetch=async()=>({ok:true,json:async()=>({event:{event_id:'c',status:'active'},samples:[],photos:[]})});
  await run("loadRecoveryDetail('c')");run("state.page='dashboard';recoveryScheduleRetry()");assert.equal(timers.size,0);
  run("state.page='events';recoveryScheduleRetry()");assert.equal(timers.size,1);
  for(const path of ['dist/app.js','dist/index.html','dist/styles.css'])assert.ok(!/showToast|id="toast"|#toast/.test(fs.readFileSync(path,'utf8')));
  console.log('PASS: actual recovery overlay, pending DB design, 1-second retries, failures, missing rows, completion, selection races, no overlaps, navigation and no toasts');
})().catch(error=>{console.error(error);process.exitCode=1;});
