const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('dist/recovery-trigger.js','utf8');
function harness(){
  let now=100000,id=0,response={events:[]},calls=[];
  const timers=new Map(),listeners={};
  const state={page:3,date:'2026-10-21',category:'all',selected:'selected-event',loading:false,detailLoading:false,polling:false};
  const context=vm.createContext({Date:class extends Date{static now(){return now;}},
    setTimeout:(fn,ms)=>{timers.set(++id,{fn,at:now+ms});return id;},clearTimeout:key=>timers.delete(key),
    window:{addEventListener:(name,fn)=>listeners[name]=fn},recoveryState:state,
    sensorValueAbnormal:(key,data)=>key==='pm25_ug_m3'?data.sensor[key]>50:data.sensor[key]>=26,
    loadRecovery:async background=>{assert.equal(background,true);calls.push(now);return response;}});
  vm.runInContext(source,context);
  return {calls,state,timers,setResponse:value=>response=value,
    emit(pm,temp=22,extra={}){listeners['airguard:telemetry']({detail:{available:true,stale:false,
      sensor:{timestamp:now/1000,pms_ok:true,bme_ok:true,pm25_ug_m3:pm,temperature_c:temp},...extra}});},
    async advance(ms){const end=now+ms;let steps=0;
      while(true){const next=[...timers.entries()].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];
        if(!next)break;assert.ok(++steps<100);now=next[1].at;timers.delete(next[0]);await next[1].fn();}
      now=end;}
  };
}
(async()=>{
  const h=harness();h.emit(20);await h.advance(1000);assert.equal(h.calls.length,0);
  h.emit(60);await h.advance(2000);h.emit(61);await h.advance(2999);assert.equal(h.calls.length,0);
  await h.advance(1);assert.deepEqual(h.calls,[106000],'Repeated sensor frames do not postpone the five-second query');
  await h.advance(999);assert.equal(h.calls.length,1);
  await h.advance(1);assert.deepEqual(h.calls,[106000,107000],'Missing upload retries after one second');
  h.setResponse({events:[{category:'pm25',started_at:new Date(101000).toISOString()}]});
  await h.advance(1000);assert.deepEqual(h.calls,[106000,107000,108000]);
  await h.advance(20000);assert.equal(h.calls.length,3,'A saved incident stops retries');
  assert.equal(h.state.selected,'selected-event');assert.equal(h.state.page,3);assert.equal(h.state.date,'2026-10-21');
  const simultaneous=harness();simultaneous.emit(60,30);await simultaneous.advance(5000);
  assert.equal(simultaneous.calls.length,1,'Simultaneous categories share a database read');
  await simultaneous.advance(20000);assert.deepEqual(simultaneous.calls,[105000,106000,107000,108000],'Retries are bounded at one-second intervals');
  const busy=harness();busy.emit(60);busy.state.loading=true;await busy.advance(5000);assert.equal(busy.calls.length,0);
  busy.state.loading=false;await busy.advance(500);assert.equal(busy.calls.length,1,'Busy requests are deferred, not canceled');
  const gap=harness();gap.emit(60);gap.emit(60,22,{stale:true});gap.emit(60);await gap.advance(5000);
  assert.equal(gap.calls.length,1);gap.setResponse({events:[{category:'pm25',started_at:new Date(100000).toISOString()}]});
  await gap.advance(5000);gap.emit(20);gap.emit(60);await gap.advance(5000);assert.equal(gap.calls.length,3,'A later incident triggers again');
  const invalid=harness();invalid.emit(60,30,{available:false});await invalid.advance(30000);assert.equal(invalid.calls.length,0);
  console.log('PASS: five-second incident query, deduplication, delayed upload retries, simultaneous alerts, busy requests, filters, stale data and subsequent incidents.');
})().catch(error=>{console.error(error);process.exitCode=1;});
