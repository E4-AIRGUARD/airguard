'use strict';
// Reuse the smoke server's camera frames; never open a second camera on Jetson.
(() => {
  let status = null, receivedAt = 0, stop = () => {};
  const frameInterval = 250, staleMs = 6000;
  function mountCamera() {
    stop();
    const camera = document.querySelector('#camera-view');
    if (!camera) return;
    const card = camera.closest('.camera-card');
    const badge = card.querySelector('.card-head .badge');
    const footer = card.querySelector('.camera-footer');
    const api = smokeAddress().replace(/\/+$/, '');
    const frame = camera.querySelector('img');
    frame.removeAttribute('src'); frame.hidden = true;
    frame.alt = 'Jetson 카메라 실시간 영상';
    camera.classList.add('live-camera');
    camera.querySelector('.camera-top').lastElementChild.textContent = 'JETSON CAMERA';
    camera.querySelector('.camera-bottom b').textContent = 'Jetson 카메라 · 실시간';
    camera.querySelector('.camera-bottom small').textContent = '연기 감지에 사용하는 카메라 영상';
    camera.querySelector('#camera-expand').setAttribute('aria-label', '실시간 CCTV 전체 화면');
    footer.firstElementChild.textContent = '실시간 카메라 · 최대 초당 4회 갱신';
    const message = document.createElement('div');
    message.className = 'camera-live-message'; message.setAttribute('role', 'status');
    camera.appendChild(message);
    let disposed=false, timer=null, active=null, shownURL=null, lastFrameAt=0;
    const releaseFrame = () => {frame.hidden=true;frame.removeAttribute('src');if(shownURL)URL.revokeObjectURL(shownURL);shownURL=null;};
    const display = (text, live=false) => {
      badge.textContent=text;badge.className='badge '+(live?'green':'red');
      message.textContent=live?'':text;message.hidden=live;
      footer.lastElementChild.textContent=live?'수신 '+new Date().toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul',hour12:false}):text;
      if(!live)releaseFrame();
    };
    const healthy = () => status?.cameraOk && Number.isFinite(status.ageSec) && status.ageSec>=0 &&
      status.ageSec*1000+(performance.now()-receivedAt)<staleMs;
    stop=()=>{disposed=true;clearTimeout(timer);clearInterval(watchdog);active?.abort();releaseFrame();};
    const watchdog=setInterval(()=>{
      if(!healthy() || (lastFrameAt && performance.now()-lastFrameAt>staleMs)) display('카메라 수신 지연 · 재연결 중');
    },1000);
    if(!api){display('카메라 서버 미설정 · 장비 데이터 연동에서 연결하세요');clearInterval(watchdog);return;}
    display('실시간 카메라 연결 중…');
    async function nextFrame() {
      if(disposed)return;
      if(!healthy()){
        display(status?'카메라 수신 지연 · 재연결 중':'카메라 서버 연결 대기');
        timer=setTimeout(nextFrame,500);return;
      }
      active=new AbortController();
      const timeout=setTimeout(()=>active?.abort(),3000);
      let candidateURL=null;
      try{
        const response=await fetch(`${api}/frame.jpg?t=${Date.now()}`,{cache:'no-store',signal:active.signal});
        if(!response.ok)throw new Error('Frame unavailable');
        const blob=await response.blob();
        if(!blob.type.startsWith('image/'))throw new Error('Invalid image');
        candidateURL=URL.createObjectURL(blob);
        const decoded=new Image();decoded.src=candidateURL;await decoded.decode();
        if(disposed||!healthy())return;
        const previous=shownURL;shownURL=candidateURL;candidateURL=null;
        frame.src=shownURL;frame.hidden=false;lastFrameAt=performance.now();
        display('● 실시간',true);
        if(previous)URL.revokeObjectURL(previous);
      }catch(error){if(!disposed)display('영상 연결 끊김 · 재연결 중');}
      finally{
        clearTimeout(timeout);if(candidateURL)URL.revokeObjectURL(candidateURL);active=null;
        if(!disposed)timer=setTimeout(nextFrame,frameInterval);
      }
    }
    nextFrame();
  }
  window.addEventListener('airguard:smoke-status',event=>{
    status=event.detail;receivedAt=performance.now();
  });
  const baseRender=render;
  render=function(){baseRender();mountCamera();};
  window.addEventListener('pagehide',()=>stop());
  mountCamera();
})();
