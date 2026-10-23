const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const context=vm.createContext({Date,Intl,Map,AbortSignal,fetch:async()=>({ok:false}),render(){},state:{page:'dashboard'},document:{querySelector:()=>null},window:{addEventListener(){}},
 recoveryState:{list:{events:[]}},recoveryLiveEvents:new Map(),recoveryShownEvent:e=>e,loadRecovery:async()=>{},actualSmoke:null,liveNumber:String});
vm.runInContext(fs.readFileSync('dist/recent-alerts.js','utf8'),context);
context.recoveryLiveEvents.set('a',{event_id:'a',category:'temperature',status:'completed',started_at:'2026-10-22T07:00:00Z',recovered_at:'2026-10-22T07:00:20Z',recovery_seconds:20});
context.recoveryLiveEvents.set('b',{event_id:'b',category:'pm25',status:'completed',started_at:'2026-10-22T07:00:10Z',recovered_at:'2026-10-22T07:00:30Z',recovery_seconds:20});
context.actualSmoke={incidents:[{id:'camera',start:'2026-10-22T07:00:05Z',end:'2026-10-22T07:00:25Z'}]};
const rows=vm.runInContext('recentAlertEntries()',context);
assert.equal(rows.length,6);
assert.deepEqual(Array.from(rows,r=>r.title),['PM2.5 회복','영상 연기 해제','온도 회복','PM2.5 이상','영상 연기 감지','온도 이상']);
context.recoveryLiveEvents.get('b').status='active';
assert.equal(vm.runInContext('recentAlertEntries()',context).length,5,'no recovery until Jetson confirms it');
assert.ok(!fs.readFileSync('dist/temperature-integration.js','utf8').includes('id="actual-control"'));
assert.ok(!fs.readFileSync('dist/live-measurements.js','utf8').includes('실제 팬 PWM과 선택한 상황 기준'));
console.log('PASS: merged temperature/PM/smoke starts and recoveries in time order; removed panels');
