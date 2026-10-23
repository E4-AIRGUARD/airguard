'use strict';
/* 에어가드 대시보드에 "연기 감지 알림"을 붙이는 스크립트.
   Jetson의 smoke_alert.py (기본 포트 8092)에서 1초마다 상태를 가져와서
   - 화면 위에 빨간 알림 배너(감지 시각, 점수, 감지 순간 사진)
   - 메인 화면 "최근 알림·운전 기록"에 연기 감지/해제 기록
   를 보여 줍니다. app.js 는 고치지 않고, index.html 에서 app.js 다음에 이 파일 한 줄만 추가하면 됩니다.

   알림 서버 주소를 정하는 법 (먼저 찾은 것을 씀)
     1) 주소 뒤에 ?smoke=http://젯슨주소:8092  (한 번 열면 이 브라우저에 저장돼요. 지우려면 ?smoke=off)
     2) 이 브라우저에 저장된 값
     3) 이 파일보다 먼저 정의된 window.SMOKE_API
   주소가 없으면 아무 일도 하지 않아요(기존 대시보드 그대로). */
(() => {
  const demo = new URLSearchParams(location.search).get('demo') === '1';
  window.addEventListener('airguard:smoke-status', event => {
    document.body.classList.toggle('smoke-detected', !!event.detail?.smoke);
  });
  const STORE = 'airguard.smokeApi';
  const store = {
    get: k => { try { return localStorage.getItem(k); } catch { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* 저장 불가여도 동작은 함 */ } },
    del: k => { try { localStorage.removeItem(k); } catch { /* */ } }
  };
  const param = new URLSearchParams(location.search).get('smoke');
  if (!demo && param === 'off') store.del(STORE);
  else if (!demo && param) store.set(STORE, param);
  const api = demo ? '' : ((param && param !== 'off' ? param : store.get(STORE)) || window.SMOKE_API || '').replace(/\/+$/, '');
  if (!api && !demo) { console.info('[smoke-alert] 알림 서버 주소가 없어 꺼져 있습니다. 주소 뒤에 ?smoke=http://젯슨주소:8092 를 붙여 여세요.'); return; }

  const POLL_MS = 1000, CLEARED_SHOW_MS = 8000;
  const html = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmtTime = iso => iso ? new Intl.DateTimeFormat('ko-KR', { timeZone: 'Asia/Seoul', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(iso)) : '—';
  const fmtDur = sec => sec == null ? '' : sec >= 60 ? `${Math.floor(sec / 60)}분 ${sec % 60}초` : `${sec}초`;

  const style = document.createElement('style');
  style.textContent = `
  .sa-banner{position:fixed;left:50%;top:12px;transform:translateX(-50%);z-index:9999;width:min(680px,calc(100vw - 24px));display:none;gap:14px;align-items:center;padding:12px 14px;border-radius:12px;color:#fff;box-shadow:0 10px 30px rgba(10,20,40,.35);font:14px/1.45 system-ui,'Noto Sans KR',sans-serif}
  .sa-banner.show{display:flex}
  .sa-banner.alert{background:#d92d4a;animation:sa-pulse 1.2s ease-in-out infinite}
  .sa-banner.clear{background:#1f9d7a}
  .sa-banner img{width:112px;height:63px;object-fit:cover;border-radius:8px;border:2px solid rgba(255,255,255,.7);flex:none;background:#0003}
  .sa-banner .sa-text{flex:1;min-width:0}
  .sa-banner b{display:block;font-size:16px}
  .sa-banner small{opacity:.92}
  .sa-banner button{background:rgba(255,255,255,.2);color:#fff;border:1px solid rgba(255,255,255,.6);border-radius:8px;padding:6px 10px;cursor:pointer;font:inherit}
  .sa-banner .sa-col{display:flex;flex-direction:column;gap:6px}
  @keyframes sa-pulse{50%{box-shadow:0 0 0 6px rgba(217,45,74,.35),0 10px 30px rgba(10,20,40,.35)}}
  @media (max-width:520px){.sa-banner img{display:none}}`;
  document.head.appendChild(style);

  const banner = document.createElement('div');
  banner.className = 'sa-banner'; banner.setAttribute('role', 'alert'); banner.setAttribute('aria-live', 'assertive');
  if(demo)document.body.append(banner);

  let soundOn = !demo && store.get('airguard.smokeSound') === '1', audio = null, beepTimer = null;
  const beep = () => {
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      const o = audio.createOscillator(), g = audio.createGain();
      o.frequency.value = 880; g.gain.value = 0.15; o.connect(g); g.connect(audio.destination);
      o.start(); o.stop(audio.currentTime + 0.25);
    } catch { /* 소리를 못 내는 환경 */ }
  };
  const syncBeep = on => {
    clearInterval(beepTimer); beepTimer = null;
    if (on && soundOn) { beep(); beepTimer = setInterval(beep, 2000); }
  };

  let prev = null, hideTimer = null, dismissedId = null, last = null;

  function showAlert(d) {
    if(!demo)return;
    const cur = d.current || d.incidents[0] || {};
    if (dismissedId === cur.id) return;
    banner.className = 'sa-banner alert show';
    banner.innerHTML = `${cur.id ? `<img alt="${demo ? '배치 확인용 예시 사진' : '연기가 처음 감지된 순간의 사진'}" src="${demo ? 'assets/chamber.jpg' : `${html(api)}/event/${html(cur.id)}.jpg`}">` : ''}
      <div class="sa-text"><b>⚠ 연기 감지${demo ? ' · 데모' : ''}</b><small>${html(fmtTime(cur.start))} 시작 · 현재 점수 ${d.score.toFixed(2)} · 최고 ${Number(cur.peak || d.score).toFixed(2)}<br>${demo ? '화면 확인용 가상 상황 · 실제 감지 아님' : `${html(d.deviceId)} 카메라`}</small></div>
      <div class="sa-col"><button data-sa="ack">확인</button><button data-sa="sound">${soundOn ? '소리 끄기' : '소리 켜기'}</button></div>`;
  }
  function showCleared(inc) {
    if(!demo)return;
    clearTimeout(hideTimer);
    banner.className = 'sa-banner clear show';
    banner.innerHTML = `<div class="sa-text"><b>✓ 연기 해제${demo ? ' · 데모' : ''}</b><small>${html(fmtTime(inc.start))} ~ ${html(fmtTime(inc.end))} · ${html(fmtDur(inc.durationSec))}</small></div><div class="sa-col"><button data-sa="ack">닫기</button></div>`;
    hideTimer = setTimeout(() => banner.classList.remove('show'), CLEARED_SHOW_MS);
  }
  banner.addEventListener('click', e => {
    const b = e.target.closest('[data-sa]'); if (!b) return;
    if (b.dataset.sa === 'ack') {
      dismissedId = last && last.current ? last.current.id : dismissedId;
      banner.classList.remove('show');
      syncBeep(false);
    } else {
      soundOn = !soundOn; if (!demo) store.set('airguard.smokeSound', soundOn ? '1' : '0');
      b.textContent = soundOn ? '소리 끄기' : '소리 켜기';
      syncBeep(!!(last && last.smoke) && !banner.classList.contains('clear'));
    }
  });


  function rowsHtml(d) {
    return (d.incidents || []).slice(0, 3).flatMap(inc => {
      const out = [];
      if (inc.end) out.push([inc.end, 'check', '연기 해제', `${fmtDur(inc.durationSec)} 지속 · 최고 점수 ${Number(inc.peak).toFixed(2)}`]);
      out.push([inc.start, 'bell', '연기 감지', `카메라 점수 최고 ${Number(inc.peak).toFixed(2)} · ${d.deviceId}`]);
      return out;
    }).sort((a, b) => b[0].localeCompare(a[0])).slice(0, 4).map(r =>
      `<div class="activity-row"><time>${html(fmtTime(r[0]).slice(0, 5))}</time><span class="activity-icon">${typeof icon === 'function' ? icon(r[1]) : ''}</span><div><b>${html(r[2])}</b><p>${html(r[3])}</p></div></div>`).join('');
  }
  function injectRows() {
    if (demo) { mountDemoToggle(); return; }
    return; // The unified recent-alerts module owns the live activity panel.
    if (!last || typeof state === 'undefined' || state.page !== 'dashboard') return;
    const box = document.querySelector('.activity');
    if (box) {
      let wrap=box.querySelector('[data-sa-rows]');
      if(!wrap){wrap=document.createElement('div');wrap.dataset.saRows='1';box.prepend(wrap);}
      wrap.innerHTML=rowsHtml(last);
      const empty=box.querySelector('[data-live-empty]');if(empty)empty.hidden=!!last.incidents?.length;
    }
  }
  if (typeof render === 'function') {              // 화면을 다시 그릴 때마다 연기 기록 줄을 다시 붙임
    const baseRender = render;
    render = function () { baseRender(); injectRows(); };
  }

  async function poll() {
    const ctl = new AbortController(), timer = setTimeout(() => ctl.abort(), 2500);
    try {
      const r = await fetch(`${api}/api/smoke`, { cache: 'no-store', signal: ctl.signal });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      last = d;
      window.dispatchEvent(new CustomEvent('airguard:smoke-status', {detail:d}));
      injectRows();
      if (d.smoke) {
        if (prev !== true) {                       // 새로 켜짐 (또는 페이지를 열었을 때 이미 켜져 있음)
          dismissedId = null;
          clearTimeout(hideTimer);
          if (typeof render === 'function' && state.page === 'dashboard') render();
        }
        showAlert(d); syncBeep(!banner.classList.contains('clear') && dismissedId !== (d.current && d.current.id));
      } else if (prev === true) {
        syncBeep(false);
        const inc = d.incidents[0];
        if (inc) showCleared(inc);
        if (typeof render === 'function' && state.page === 'dashboard') render();
      }
      prev = d.smoke;
    } catch (e) {
      window.dispatchEvent(new CustomEvent('airguard:smoke-status', {detail:null}));
    } finally {
      clearTimeout(timer);
    }
  }
  function mountDemoToggle() {
    const footer = document.querySelector('.camera-footer');
    if (!footer || footer.querySelector('[data-smoke-demo]')) return;
    const button = document.createElement('button');
    button.className = 'button smoke-demo-toggle';
    button.dataset.smokeDemo = '1';
    button.type = 'button';
    button.textContent = last?.smoke ? '연기 해제 테스트' : '연기 발생 테스트';
    button.setAttribute('aria-pressed', String(!!last?.smoke));
    button.addEventListener('click', () => {
      const active = !last?.smoke;
      const now = new Date().toISOString();
      const incident = active
        ? {id:'DEMO-SMOKE', start:now, peak:.94, end:null}
        : {...last.current, end:now, durationSec:Math.round((Date.now()-Date.parse(last.current.start))/1000)};
      last = {smoke:active, score:active ? .94 : .05, deviceId:'DEMO', cameraOk:true, ageSec:0,
        current:active?incident:null, incidents:[incident]};
      dismissedId = null;
      clearTimeout(hideTimer);
      window.dispatchEvent(new CustomEvent('airguard:smoke-status', {detail:last}));
      if (active) showAlert(last); else showCleared(incident);
      syncBeep(active);
      button.textContent = active ? '연기 해제 테스트' : '연기 발생 테스트';
      button.setAttribute('aria-pressed', String(active));
    });
    footer.appendChild(button);
  }
  if (demo) { mountDemoToggle(); return; }
  poll(); setInterval(poll, POLL_MS);
  window.addEventListener('hashchange', () => setTimeout(injectRows, 0));
})();
