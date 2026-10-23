'use strict';
// Keep the built-in sample dataset separate from all live integrations.
(async () => {
  const loadScript = src => new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = src;
    script.onload = resolve;
    script.onerror = () => reject(new Error('화면 스크립트 로드 실패: ' + src));
    document.body.appendChild(script);
  });
  if (new URLSearchParams(location.search).get('demo') === '1') {
    await loadScript('smoke-alert.js?v=20261022-recovery-final2');
    await loadScript('demo-temperature.js?v=20261022-header-clean1');
    route();
    return;
  }

  // These scripts extend the same global render functions in dependency order.
  try {
    for (const src of [
      'live-measurements.js?v=20261022-recovery-final2',
      'temperature-integration.js?v=20261022-recovery-final2',
      'live-camera.js?v=20261021-live4',
      'smoke-alert.js?v=20261022-recovery-final2',
      'dashboard-lifecycle.js?v=20261021-lease1',
      'recovery-history.js?v=20261022-recovery-final2',
      'recovery-trigger.js?v=20261022-event-retry1s',
      'global-alerts.js?v=20261022-dashboard-link1',
      'recent-alerts.js?v=20261022-recovery-final2'
    ]) {
      await loadScript(src);
    }
    route();
    startDeviceLink();
  } catch (error) {
    document.querySelector('#main').textContent = '화면을 불러오지 못했습니다. 새로고침해 주세요.';
    console.error(error);
  }
})();
