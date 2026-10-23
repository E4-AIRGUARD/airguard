'use strict';
// Trigger a read after a newly observed sensor alert. Never write to the DB or fans.
(() => {
  const initialDelay=5000,retryDelay=1000,maxAttempts=4;
  const active={pm25:false,temperature:false},pending=new Map();
  let timer=null,running=false;
  function schedule(){
    if(timer!==null)clearTimeout(timer);
    timer=null;
    if(running||!pending.size)return;
    const due=Math.min(...[...pending.values()].map(job=>job.due));
    timer=setTimeout(refresh,Math.max(0,due-Date.now()));
  }
  async function refresh(){
    timer=null;
    const now=Date.now(),jobs=[...pending.values()].filter(job=>job.due<=now);
    if(!jobs.length){schedule();return;}
    // Avoid invalidating an in-flight selection or a filter request.
    if(recoveryState.loading||recoveryState.detailLoading||recoveryState.polling){
      for(const job of jobs)job.due=now+500;
      schedule();return;
    }
    running=true;
    try{
      const data=await loadRecovery(true);
      for(const job of jobs){
        if(pending.get(job.category)!==job)continue;
        job.attempts++;
        const saved=data?.events?.some(event=>event.category===job.category&&
          Date.parse(event.started_at)>=job.startedAt-5000);
        if(saved||job.attempts>=maxAttempts)pending.delete(job.category);
        else job.due=Date.now()+retryDelay;
      }
    }finally{
      running=false;
      schedule();
    }
  }
  window.addEventListener('airguard:telemetry',event=>{
    const data=event.detail,sensor=data?.available&&!data.stale?data.sensor:null;
    if(!sensor)return; // A connection gap is not recovery or a new incident.
    for(const [category,key,health] of [['pm25','pm25_ug_m3','pms_ok'],['temperature','temperature_c','bme_ok']]){
      if(!sensor[health]||!Number.isFinite(sensor[key]))continue;
      const abnormal=sensorValueAbnormal(key,data);
      if(abnormal&&!active[category]){
        pending.set(category,{category,startedAt:Number.isFinite(sensor.timestamp)?sensor.timestamp*1000:Date.now(),
          due:Date.now()+initialDelay,attempts:0});
      }
      active[category]=abnormal;
    }
    schedule();
  });
})();
