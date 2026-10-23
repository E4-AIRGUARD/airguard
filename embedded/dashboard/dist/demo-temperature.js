'use strict';
// Design-only scenario. No sensor requests, telemetry or device commands.
(() => {
  if (new URLSearchParams(location.search).get('demo') !== '1') return;
  let active = new URLSearchParams(location.search).get('scenario') === 'temperature';
  const baseRender = render;
  function toggle() {
    active = !active;
    const url = new URL(location.href);
    if (active) url.searchParams.set('scenario', 'temperature');
    else url.searchParams.delete('scenario');
    history.replaceState(null, '', url);
    render();
  }
  render = () => {
    baseRender();
    const footer = document.querySelector('.camera-footer');
    const toggleButton = document.createElement('button');
    toggleButton.type = 'button';
    toggleButton.className = 'button temperature-demo-toggle';
    toggleButton.textContent = active ? '온도 정상 복귀' : '온도 이상 테스트';
    toggleButton.setAttribute('aria-pressed', String(active));
    toggleButton.addEventListener('click', toggle);
    if(footer)footer.appendChild(toggleButton);
    if (!active) return;

    const notice = document.createElement('section');
    notice.className = 'temperature-demo-alert';
    notice.setAttribute('role', 'alert');
    notice.innerHTML = `${icon('temp')}<div><strong>온도 이상 · 데모</strong><p>현재 32.4 °C · 이상 기준 26 °C 초과 · 냉각 운전 상황</p><small>화면 확인용 가상 측정값입니다. 실제 장비를 제어하지 않습니다.</small></div>`;
    const reset = document.createElement('button');
    reset.className = 'button';
    reset.type = 'button';
    reset.textContent = '정상 상황으로';
    reset.addEventListener('click', toggle);
    notice.appendChild(reset);
    document.querySelector('.page-heading').after(notice);

    if (state.page !== 'dashboard') return;
    document.querySelector('.dashboard-layout > .metrics').innerHTML =
      metric('미세먼지 PM2.5','dust','18','µg/m³','정상 범위','예시 기준 50 µg/m³ 이하','blue') +
      metric('온도','temp','32.4','°C','온도 이상','이상 기준 26 °C · 기준보다 6.4 °C 높음','red','temperature-demo-critical') +
      metric('습도','drop','57.7','%RH','측정 기록','상대습도 · 예시 측정값') +
      metric('컨테이너 상태','box','온도 이상','','냉각 운전','온도 기준 초과 · 연기 없는 상황 가정','red','container-metric temperature-demo-critical');
    const fans = document.querySelector('.dashboard-fans > .fans');
    fans.innerHTML = fan('흡기 팬','IN','3,420') + fan('배기 팬','OUT','6,980');
    // Illustrative cooling combination, not a controller prediction or command.
    const exhaust = fans.lastElementChild;
    exhaust.querySelectorAll('.track i').forEach(bar => bar.style.width = '100%');
    exhaust.querySelectorAll('.pwm b').forEach(value => value.textContent = '100%');
    fans.querySelectorAll('.small-label').forEach(label => label.textContent = '냉각 운전 가정 · 예시 회전수');
    document.querySelector('.activity').innerHTML = `<div class="activity-row"><time>예시</time><span class="activity-icon">${icon('temp')}</span><div><b>온도 기준 초과</b><p>32.4 °C · 이상 기준 26 °C 초과</p></div></div><div class="activity-row"><time>예시</time><span class="activity-icon">${icon('fan')}</span><div><b>냉각 운전 상황</b><p>흡기 60% / 배기 100% · 화면 확인용 팬 조합</p></div></div>`;
  };
})();
