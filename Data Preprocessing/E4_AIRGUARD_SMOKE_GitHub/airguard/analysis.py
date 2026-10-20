"""회복 지표 공통 정의. 도달 구간 시작과 유지 확인 시각을 구분합니다."""
from .common import PM, number


def held(rows, threshold, hold, gap, start=0, strict=False):
    first = previous = None
    for i in range(start, len(rows)):
        t, value = number(rows[i].get('Time[s]')), number(rows[i].get(PM))
        if t is None or value is None or value < 0:
            first = previous = None
            continue
        if previous is not None and not 0 < t-previous <= gap:
            first = None
        if (value < threshold if strict else value <= threshold):
            if first is None:
                first = i
            if t-float(rows[first]['Time[s]']) >= hold-1e-8:
                return first, i
        else:
            first = None
        previous = t
    return None, None


def window(rows, config, gap):
    result = dict(anchor=None, anchor_confirm=None, target=None, target_confirm=None,
                  anchor_index=None, target_confirm_index=None, gap_limit=gap,
                  status='PM500 상한 구간 미관측')
    good = [i for i, r in enumerate(rows) if number(r.get('Time[s]')) is not None
            and number(r.get(PM)) is not None and float(r[PM]) >= 0]
    if not good or max(float(rows[i][PM]) for i in good) < config['pm_cap']:
        return result
    peak = max(float(rows[i][PM]) for i in good)
    last_peak = max(i for i in good if float(rows[i][PM]) == peak)
    first, confirmed = held(rows, config['pm_cap'], config['hold_seconds'], gap, last_peak, strict=True)
    if first is None:
        result['status'] = 'PM500 미만 유지 미확인'
        return result
    result.update(anchor=rows[first]['Time[s]'], anchor_confirm=rows[confirmed]['Time[s]'], anchor_index=first)
    target, target_confirm = held(rows, config['pm_target'], config['hold_seconds'], gap, first)
    result['status'] = 'PM50 이하 유지 미확인'
    if target is not None:
        result.update(target=rows[target]['Time[s]'], target_confirm=rows[target_confirm]['Time[s]'],
                      target_confirm_index=target_confirm, status='PM50 이하 유지 확인')
    return result


def evaluate(run, config):
    rows = run['fan']
    first, confirm = held(rows, config['pm_target'], config['hold_seconds'], run['gap'])
    w = window(rows, config, run['gap'])
    run['window'] = w
    run['metrics'] = {
        'Fan_to_PM50_Arrival_s': rows[first]['Time[s]'] if first is not None else None,
        'Fan_to_PM50_Confirm_s': rows[confirm]['Time[s]'] if confirm is not None else None,
        'Anchor_From_Fan_Start_s': w['anchor'], 'Anchor_Confirm_s': w['anchor_confirm'],
        'Actual_Anchor_PM[ug/m3]': rows[w['anchor_index']][PM] if w['anchor_index'] is not None else None,
        'PM500_to_PM50_s': round(w['target']-w['anchor'], 6) if w['target'] is not None else None,
        'Target_From_Fan_Start_s': w['target'], 'Target_Confirm_s': w['target_confirm'],
        'Recovery_Status': w['status'] if rows else run['fan_reason'],
    }
    stop = w['target_confirm_index'] if w['target_confirm_index'] is not None else len(rows)-1
    run['aligned'] = [dict(r, **{'Time_From_Anchor[s]': round(r['Time[s]']-w['anchor'], 6)})
                      for r in rows[w['anchor_index']:stop+1]] if w['anchor_index'] is not None else []
    return run
