"""dashboard.template.html + ../model.json + sample_energy_log.csv -> dashboard.html. 모델을 다시 만든 뒤 실행하면 화면의 '예상' 값이 갱신된다."""
import json, os
here = os.path.dirname(os.path.abspath(__file__))
m = json.load(open(os.path.join(here, '..', 'model.json'), encoding='utf-8'))
slim = {'levels_a': m['levels_a'], 'levels_b': m['levels_b'],
        'combos': {k: {'power_w': round(c['power_w'], 4), 'to50': {L: round(v, 2) for L, v in c['to50_mean'].items()}} for k, c in m['combos'].items()},
        'tail': {L: round(v['median'], 2) for L, v in m['tail']['to20'].items()}}
t = open(os.path.join(here, 'dashboard.template.html'), encoding='utf-8').read()
smp = open(os.path.join(here, 'sample_energy_log.csv'), encoding='utf-8-sig').read().strip().replace('\\', '\\\\').replace('`', '\\`')
open(os.path.join(here, 'dashboard.html'), 'w', encoding='utf-8').write(t.replace('/*MODEL*/', json.dumps(slim)).replace('/*SAMPLE*/', smp))
print('dashboard.html 갱신')

# 가상 데이터 시험 로그를 5배속으로 반복 재생하는 단독 HTML (서버 없이 열면 바로 움직인다)
vp = os.path.join(here, '..', '가상시험', '가상시험_에너지로그.csv')
if os.path.exists(vp):
    v = open(vp, encoding='utf-8-sig').read().strip().replace('\\', '\\\\').replace('`', '\\`')
    d = t.replace('/*MODEL*/', json.dumps(slim)).replace('/*SAMPLE*/', v)
    d = d.replace("if (new URLSearchParams(location.search).has('demo') || location.protocol === 'file:') startDemo(); else startLive();", 'startDemo();')
    d = d.replace('데모 데이터 (실제 로그 아님)', '가상 데이터 시험 · 5배속 반복 재생 (실제 측정 아님)')
    open(os.path.join(here, 'dashboard_가상시험.html'), 'w', encoding='utf-8').write(d)
    print('dashboard_가상시험.html 갱신')
