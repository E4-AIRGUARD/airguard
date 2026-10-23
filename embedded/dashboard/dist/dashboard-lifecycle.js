'use strict';
// Each tab owns a separate lease. Only the last tab closing stops the fans.
(()=>{
  let client='',sequence=0,closed=true,timer=null;
  const payload=action=>JSON.stringify({client,action,sequence:++sequence});
  async function heartbeat(){
    if(closed)return;
    const current=client;
    try{
      const response=await fetch('/api/dashboard-lease',{method:'POST',headers:{'Content-Type':'application/json'},body:payload('heartbeat'),signal:AbortSignal.timeout(4000)});
      if(!response.ok)throw new Error('자동 제어 연결 대기');
    }catch{/* Missing heartbeats expire on Jetson; never assume fans are on. */}
    finally{if(!closed&&client===current)timer=setTimeout(heartbeat,3000);}
  }
  function open(){if(!closed)return;client=crypto.randomUUID();sequence=0;closed=false;heartbeat();}
  function close(){
    if(closed)return;closed=true;clearTimeout(timer);
    const body=payload('close');
    if(!navigator.sendBeacon('/api/dashboard-lease',new Blob([body],{type:'application/json'}))){
      fetch('/api/dashboard-lease',{method:'POST',headers:{'Content-Type':'application/json'},body,keepalive:true}).catch(()=>{});
    }
  }
  window.addEventListener('pagehide',close);
  window.addEventListener('pageshow',open); // New ID on back-forward cache restore.
  open();
})();
