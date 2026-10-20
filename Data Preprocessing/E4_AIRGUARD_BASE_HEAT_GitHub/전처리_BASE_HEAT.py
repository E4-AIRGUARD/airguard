# -*- coding: utf-8 -*-
"""E4-AIRGUARD: Python 표준 라이브러리만 사용. 실행 후 원본 폴더 선택 가능.
python E4_AIRGUARD_전처리.py --input "261014 온도 실험 데이터 heat_30s"
출력은 이 코드 옆 데이터 품질·통합 평가. 기존 결과가 있으면 번호를 붙인 새 폴더를 만듭니다. 센서 CSV만 처리합니다. 원본은 수정하지 않습니다.
"""
## 1. 설정: baseline은 무풍, fan_matrix는 팬 가동, heating_5s는 가열 후 측정
from pathlib import Path
from datetime import datetime
from collections import deque, Counter
import argparse
import csv
import hashlib
import html
import json
import math
import statistics as st
import subprocess
import tempfile
import os
import re

KINDS = {'baseline':'BASE_OFF', 'fan_matrix':'FAN_MATRIX', 'heating_5s':'HEAT_5S', 'heating_30s':'HEAT_30S'}
FOLDER_KINDS = {**KINDS, 'heat_30s':'HEAT_30S'}
LABELS = {'BASE_OFF':'무풍_정상기준', 'FAN_MATRIX':'팬조합_정상운전', 'HEAT_5S':'5초가열_회복', 'HEAT_30S':'30초가열_실험'}
ALIASES = {
 'Temperature[degC]':('Temp','temp_c'), 'Humidity[%RH]':('Humidity','rh_pct'),
 'PM2.5[ug/m3]':('PM2.5','pm2_5'), 'Intake_PWM[%]':('Intake_PWM','duty_in_pct'),
 'Exhaust_PWM[%]':('Exhaust_PWM','duty_out_pct'), 'Intake_RPM[rpm]':('Intake_RPM','rpm_in'),
 'Exhaust_RPM[rpm]':('Exhaust_RPM','rpm_out'), 'Heat_Load[0/1]':('Heat_Load',)}
SIGNALS = list(ALIASES)
SENSOR = SIGNALS[:3]+SIGNALS[5:7]
RPM_RAW = {k:k.replace('_RPM[','_RPM_Raw[') for k in SIGNALS[5:7]}
FAN_MAIN = '일정 PWM 구간'
FAN_TAIL = '정지·재가동 구간'
FAN_UNKNOWN = 'PWM 확인 필요'
DERIVED = []
PLOT_SIGNALS = SENSOR+DERIVED
RATE_COLUMNS={key:key.replace(']','/min]')+'_MeanRate' for key in SENSOR}
COOLING_COLUMNS=['Cooling_Duration_s','Temperature[degC]_Cooling_Delta','Temperature[degC/min]_Cooling_MeanRate']
MIN_SPAN = {'Temperature[degC]':10, 'Humidity[%RH]':30, 'PM2.5[ug/m3]':50,
            'Intake_RPM[rpm]':8000,'Exhaust_RPM[rpm]':8000,
            'Delta_T[degC]':2,'dT_dt[degC/min]':2}
COLS = ['Measurement_Date','Time[s]']+SIGNALS[:-1]

## 2. 읽기·검증: 결측은 빈칸, 실제 0은 보존
def number(x):
    try:
        v=float(x)
        return v if math.isfinite(v) else None
    except (TypeError,ValueError):return None

def median(values):
    vals=[v for v in values if v is not None]
    return st.median(vals) if vals else None

def interval_change(rows,key):
    """변화량은 마지막-처음. 변화율은 실제 경과 시간으로 나눈 분당 평균입니다."""
    if not rows or rows[0][key] is None or rows[-1][key] is None:return None,None
    delta=rows[-1][key]-rows[0][key]
    times=[r['Time_From_Recording_Start[s]'] for r in rows]
    duration=times[-1]-times[0]
    # 한 표본뿐이거나 시각이 역행하면 변화율을 임의의 0으로 채우지 않습니다.
    valid_time=duration>0 and all(b>a for a,b in zip(times,times[1:]))
    return round(delta,6),round(delta/duration*60,6) if valid_time else None

def cooling_metrics(rows,step):
    """한 번의 가열 종료(1→0) 직후부터 최종 관측까지의 온도 변화만 계산합니다."""
    empty=dict.fromkeys(COOLING_COLUMNS)
    on=[n for n,r in enumerate(rows) if r['Heat_Load[0/1]']==1]
    if not on or on[-1]+1>=len(rows):return empty
    if on!=list(range(on[0],on[-1]+1)):return empty  # 여러 가열 주기는 하나로 합산하지 않습니다.
    n=on[-1]+1;previous=rows[n-1];first=rows[n]
    dt=first['Time_From_Recording_Start[s]']-previous['Time_From_Recording_Start[s]']
    if first['Source_Row']!=previous['Source_Row']+1 or not 0<dt<=1.5*step:return empty
    tail=rows[n:];delta,rate=interval_change(tail,'Temperature[degC]')
    duration=round(tail[-1]['Time_From_Recording_Start[s]']-tail[0]['Time_From_Recording_Start[s]'],6)
    return dict(zip(COOLING_COLUMNS,(duration,delta,rate)))

def read_csv(path):
    for encoding in ('utf-8-sig','cp949'):
        try:
            with path.open(encoding=encoding,newline='') as f:return list(csv.DictReader(f))
        except UnicodeDecodeError:pass
    raise ValueError('CSV 인코딩 확인 필요')

def write_csv(path,rows,columns=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    if columns is None:columns=list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=columns);w.writeheader();w.writerows(rows)

def valid(k,v):
    if v is None:return False
    if k.startswith('Temperature'):return -20<=v<=80
    if k.startswith('Humidity') or 'PWM' in k:return 0<=v<=100
    if k.startswith('Heat_Load'):return v in (0,1)
    return v>=0

def required_signals(kind):
    # 가열 상태가 없는 실험에는 Heat_Load 결측을 만들거나 오류로 기록하지 않습니다.
    return SIGNALS if kind=='HEAT_30S' else SIGNALS[:-1]

def load_record(path,kind,audit,original=None):
    rows=read_csv(path) if original is None else original
    if not rows:raise ValueError('빈 CSV')
    if 'Experiment_ID' in rows[0] or 'Temperature[degC]' in rows[0]:raise ValueError('전처리 결과 CSV는 RAW 입력으로 사용할 수 없습니다.')
    mapping={k:next((a for a in (k,*aliases) if a in rows[0]),None) for k,aliases in ALIASES.items()}
    missing=[k for k in required_signals(kind) if mapping[k] is None]
    if missing:raise ValueError('필수 칼럼 누락: '+', '.join(missing))
    has_time=any(k in rows[0] for k in ('Time','pc_timestamp','Timestamp'))
    if not has_time and 'elapsed_ms' not in rows[0]:raise ValueError('시간 열 누락')
    result=[];seen=set()
    for rownum,row in enumerate(rows,2):
        key=tuple(row.items())
        if key in seen:
            audit.append(dict(Source_Row=rownum,Column='ROW',Reason='exact_duplicate'));continue
        seen.add(key)
        stamp=row.get('Time',row.get('pc_timestamp',row.get('Timestamp','')))
        try:
            dt=datetime.fromisoformat(stamp) if has_time else None
            clock=dt.timestamp() if dt else float(row['elapsed_ms'])/1000
            if not math.isfinite(clock):raise ValueError()
        except (ValueError,TypeError):
            audit.append(dict(Source_Row=rownum,Column='ROW',Reason='invalid_time'));continue
        flags=[];values={}
        for k,col in mapping.items():
            if k not in required_signals(kind):
                values[k]=None;continue
            v=number(row.get(col)) if col else None
            bad=not valid(k,v)
            if k in SIGNALS[:2] and 'bme_err' in row and number(row['bme_err'])!=0:bad=True
            if k==SIGNALS[2] and 'pms_stale' in row and number(row['pms_stale'])!=0:bad=True
            if bad:
                flags.append('missing_or_invalid:'+k)
                audit.append(dict(Source_Row=rownum,Column=k,Reason=('missing_value' if v is None else 'invalid_range_or_sensor_flag'),Before=row.get(col,''),After=None));v=None
            values[k]=v
        result.append(dict(clock=clock,Timestamp=stamp,Source_Row=rownum,values=values,flags=flags))
    if not result:raise ValueError('유효 시간 없음')
    return result


def read_meta(path):
    meta=path.parent/'status.json'
    if not meta.exists():return {}
    try:return json.loads(meta.read_text(encoding='utf-8-sig'))
    except (ValueError,OSError) as exc:return {'status':'metadata_read_error','error':str(exc)}


def cooling_match(main,cooling):
    """이름만 같은 다른 실험을 합치지 않도록 시각·명령·연속 측정 기록을 확인합니다."""
    try:
        a=read_csv(main);b=read_csv(cooling);ma=read_meta(main);mb=read_meta(cooling)
        if not a or not b:return '빈 파일'
        if set(a[0])!=set(b[0]):return 'CSV 칼럼 구성 불일치'
        stamp=next((k for k in ('Time','pc_timestamp','Timestamp') if k in a[0]),None)
        if not stamp:return '절대 측정 시각 없음'
        ta=[datetime.fromisoformat(r[stamp]).timestamp() for r in a]
        tb=[datetime.fromisoformat(r[stamp]).timestamp() for r in b]
        if min(tb)<=max(ta):return '측정 시각 중복 또는 냉각 기록이 먼저 시작됨'
        if mb.get('continuous_from_measurement') is not True:return '연속 냉각 메타데이터 없음'
        # 두 기록이 같은 측정 종료 이벤트에서 이어졌는지 확인합니다. 시간을 보정하지 않습니다.
        end=datetime.fromisoformat(ma['measurement_ended']).timestamp()
        start=datetime.fromisoformat(mb['started']).timestamp()
        if abs(start-end)>5:return '측정 종료와 냉각 시작 이벤트가 5초 넘게 떨어짐'
        commands=[]
        for rows in (a,b):
            keys=[next(k for k in (name,*ALIASES[name]) if k in rows[0]) for name in SIGNALS[3:5]]
            commands.append({tuple(number(r[k]) for k in keys) for r in rows})
        if len(commands[0])!=1 or commands[0]!=commands[1] or None in next(iter(commands[0])):
            return '흡기·배기 PWM 조건 불일치 또는 변경'
        heat=next(k for k in ('Heat_Load[0/1]','Heat_Load') if k in b[0])
        if any(number(r[heat])!=0 for r in b):return '냉각 구간 가열 상태가 0이 아님'
        return ''
    except (ValueError,KeyError,StopIteration,OSError) as exc:return '연결 확인 불가: '+str(exc)


def input_groups(folder,kind):
    """본 측정+후속 냉각을 한 실험으로 묶고 미연결 냉각은 검토용으로 보존합니다."""
    paths=sorted(folder.rglob('*.csv'))
    if kind!='HEAT_30S':return [([p],'') for p in paths]
    cooling_root=folder/'cooling'
    cooling=[p for p in paths if cooling_root in p.parents]
    groups=[];used=set();notes={}
    for main in (p for p in paths if p not in cooling):
        candidate=cooling_root/main.parent.relative_to(folder)/'cooling.csv'
        if candidate in cooling:
            reason=cooling_match(main,candidate)
            if not reason:
                groups.append(([main,candidate],''));used.add(candidate);continue
            notes[candidate]=reason
        groups.append(([main],''))
    groups.extend(([p],notes.get(p,'같은 실험명의 본 측정 파일 없음')) for p in cooling if p not in used)
    return groups


def folder_kind(name):
    """날짜·한글 설명이 붙은 폴더명에서도 실험 종류를 찾습니다."""
    kinds={kind for word,kind in FOLDER_KINDS.items()
           if re.search(r'(?<![a-z0-9])'+re.escape(word)+r'(?![a-z0-9])',name.lower())}
    if len(kinds)>1:
        raise ValueError('폴더명에 여러 실험 종류가 있습니다. --kind로 지정하세요: '+name)
    return next(iter(kinds),None)


def discover_folders(selected,explicit_kind=None):
    """선택한 실험 폴더 또는 바로 아래의 실험 종류 폴더만 탐색합니다."""
    selected=selected.resolve()
    kind=explicit_kind or folder_kind(selected.name)
    if kind:
        folders=[(selected,kind)];root=selected.parent
    else:
        root=selected
        folders=[(p,kind) for p in sorted(selected.iterdir()) if p.is_dir()
                 if (kind:=folder_kind(p.name))]
    # 출력 폴더를 만들기 전에 입력을 확인하여 빈 결과 폴더 생성을 방지합니다.
    folders=[(p,kind) for p,kind in folders if next(p.rglob('*.csv'),None) is not None]
    if not folders:
        raise ValueError('원본 CSV를 찾지 못했습니다. baseline / fan_matrix / heating_5s / '
                         'heating_30s / heat_30s 폴더를 선택하거나, '
                         '--kind HEAT_30S처럼 실험 종류를 지정하세요. 선택한 경로: '+str(selected))
    return root,folders


## 3. 연속 구간별 후행 중앙값: 필터 구간과 저장 파일을 구분
def fan_sections(raw,kind):
    """30초 가열 종료 후 첫 양쪽 PWM=0 전환부터 끝까지 별도 구간으로 표시합니다.

    실험 종료를 센서 온도로 추측하지 않습니다. 여러 가열 주기 또는 가열 상태 결측이
    있으면 끝 구간을 자동 판정하지 않습니다. 구간 표시는 행 삭제 조건이 아닙니다.
    """
    pairs=[tuple(r['values'][k] for k in SIGNALS[3:5]) for r in raw]
    sections=[FAN_UNKNOWN if None in pair else FAN_MAIN for pair in pairs]
    on=[i for i,r in enumerate(raw) if r['values']['Heat_Load[0/1]']==1]
    heat_known=all(r['values']['Heat_Load[0/1]'] is not None for r in raw)
    if kind=='HEAT_30S' and heat_known and on and on==list(range(on[0],on[-1]+1)):
        for i in range(max(1,on[-1]+1),len(raw)):
            if pairs[i]==(0,0) and None not in pairs[i-1] and any(v>0 for v in pairs[i-1]):
                sections[i:]=[FAN_TAIL]*(len(raw)-i)
                break
    return {r['Source_Row']:section for r,section in zip(raw,sections)}


def clean_record(raw,kind,audit):
    """유효 시각의 행은 보존합니다. 결측은 해당 칼럼만 비우고 보간하지 않습니다."""
    steps=[b['clock']-a['clock'] for a,b in zip(raw,raw[1:]) if b['clock']>a['clock']]
    step=st.median(steps) if steps else 1
    parts=[];part=[];previous=None;origin=raw[0]['clock']
    for r in raw:
        # RPM 결측 때문에 정상 온도·습도까지 삭제하지 않습니다.
        required=required_signals(kind)
        if previous:
            dt=r['clock']-previous['clock']
            changed=any(r['values'][k]!=previous['values'][k] for k in required if 'PWM' in k or 'Heat_Load' in k)
            # 시각 오류·중복 행도 이미 제외됐을 수 있으므로 원본 행의 연속성을 확인합니다.
            skipped=r['Source_Row']!=previous['Source_Row']+1
            if dt<=0 or dt>1.5*step or changed or skipped:
                parts.append(part);part=[]
                audit.append(dict(Source_Row=r['Source_Row'],Column='TIME_OR_COMMAND',Reason='new_continuous_segment'))
        part.append(r);previous=r
    if part:parts.append(part)
    exports=[];sections=fan_sections(raw,kind)
    rpm_buffers={k:deque(maxlen=3) for k in RPM_RAW};previous_rpm=None
    for part_no,part in enumerate(parts,1):
        kept=[];buffers={k:deque(maxlen=3) for k in SIGNALS[:3]}
        for r in part:
            row=dict(Timestamp=r['Timestamp'],**{'Time[s]':round(r['clock']-part[0]['clock'],6),
                'Time_From_Recording_Start[s]':round(r['clock']-origin,6)},Source_Row=r['Source_Row'])
            row['Fan_Section']=sections[r['Source_Row']]
            pair=tuple(r['values'][k] for k in SIGNALS[3:5])
            # RPM 창은 가열 상태만 바뀌어도 유지합니다. PWM·시간·행 연속성이 바뀌면 초기화합니다.
            reset=previous_rpm is None or None in pair
            if previous_rpm is not None:
                dt=r['clock']-previous_rpm['clock']
                old_pair=tuple(previous_rpm['values'][k] for k in SIGNALS[3:5])
                reset=reset or pair!=old_pair or not 0<dt<=1.5*step or r['Source_Row']!=previous_rpm['Source_Row']+1
            if reset:
                for buf in rpm_buffers.values():buf.clear()
            for k,v in r['values'].items():
                before=v
                if k in buffers:
                    if v is None:buffers[k].clear()
                    else:
                        buffers[k].append(v)
                        if len(buffers[k])==3:v=st.median(buffers[k])
                if k in RPM_RAW:
                    row[RPM_RAW[k]]=before
                    pwm=r['values']['Intake_PWM[%]' if k.startswith('Intake') else 'Exhaust_PWM[%]']
                    # 정지 명령·끝 구간·결측은 원값 보존. 정상 명령의 연속 유효 표본만 평활화합니다.
                    if v is None or None in pair or pwm==0 or row['Fan_Section']==FAN_TAIL:
                        rpm_buffers[k].clear()
                    else:
                        rpm_buffers[k].append(v)
                        if len(rpm_buffers[k])==3:v=st.median(rpm_buffers[k])
                if v!=before:
                    reason='rpm_trailing_median_3' if k in RPM_RAW else 'trailing_median_3'
                    audit.append(dict(Source_Row=r['Source_Row'],Column=k,Reason=reason,Before=before,After=v))
                row[k]=v
            kept.append(row);previous_rpm=r
        exports.append((part_no,kept))
    return exports,max(0,len(parts)-1),step


## 4. 그래프: 인터넷·추가 패키지 없이 SVG와 HTML로 열기
def short_label(run):
    # 범례에는 팬 조건과 반복 번호만 표시하고, 원본 ID는 상세정보로 분리합니다.
    fmt=lambda v:'미상' if v is None else f'{v:g}'
    label=f'흡기{fmt(run["key"][0])} / 배기{fmt(run["key"][1])} · {run["repeat"]}회'
    return label

def plot_points(run,key,include_tail=True):
    # 한 파일로 저장해도 실제 시간 공백·역행·제외 행을 선으로 연결하지 않습니다.
    points=[];previous=None
    for row in run['rows']:
        time=row['Time_From_Recording_Start[s]']
        if previous:
            delta=time-previous['Time_From_Recording_Start[s]']
            if delta<=0 or delta>1.5*run['step'] or row['Source_Row']!=previous['Source_Row']+1:
                points.append((time,None))
        value=None if not include_tail and row.get('Fan_Section')==FAN_TAIL else row[key]
        points.append((time,value));previous=row
    return points


def chart(series,title,key,marked_rows=(),shaded_ranges=(),x_range=None):
    vals=[y for _,points,_,_ in series for _,y in points if y is not None]
    if not vals:return '<p>유효 측정값 없음</p>'
    span=max(MIN_SPAN[key],max(vals)-min(vals))*1.1
    mid=(max(vals)+min(vals))/2;lo=mid-span/2;hi=mid+span/2
    if key not in SIGNALS[:2]+DERIVED:lo=0;hi=max(MIN_SPAN[key],max(vals)*1.1)
    if key in DERIVED:
        hi=max(MIN_SPAN[key]/2,max(abs(v) for v in vals)*1.1);lo=-hi
    xmin,xmax=x_range or (0,max(1,max(x for _,pts,_,_ in series for x,_ in pts)))
    if xmax<=xmin:xmax=xmin+1
    sx=lambda x:75+(x-xmin)/(xmax-xmin)*795
    esc=html.escape
    out=[f'<h3>{esc(title)}</h3><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 310" role="img">', '<rect width="900" height="310" fill="white"/>']
    for start,end in shaded_ranges:
        left,right=max(xmin,start),min(xmax,end)
        if right>left:out.append(f'<rect x="{sx(left):.2f}" y="25" width="{sx(right)-sx(left):.2f}" height="220" fill="#64748b" opacity="0.16"/>')
    # 처리 위치는 주황 띠로 표시합니다. 결측값도 x축 위치를 확인할 수 있습니다.
    for x in marked_rows:
        out.append(f'<rect x="{sx(x)-2:.2f}" y="25" width="4" height="220" fill="#ff9800" opacity="0.25"/>')
    for i in range(6):
        y=25+i*44;value=hi-(hi-lo)*i/5
        out.append(f'<path d="M75 {y} H870" stroke="#ddd"/><text x="5" y="{y+4}" font-size="13">{value:.2f}</text>')
        x=75+i*159;out.append(f'<text x="{x}" y="270" font-size="13">{xmin+(xmax-xmin)*i/5:.0f}</text>')
    for label,pts,color,dash in series:
        chunk=[]
        def emit():
            if chunk:out.append(f'<polyline points="{" ".join(chunk)}" fill="none" stroke="{color}" stroke-width="2" stroke-dasharray="{dash}"/>')
        for x,y in pts:
            if y is None:emit();chunk=[]
            else:chunk.append(f'{sx(x):.2f},{25+(hi-y)/(hi-lo)*220:.2f}')
        emit()
        # 선 위에 약 12개의 표식을 찍어 색이 비슷해도 조건을 구분합니다.
        shape='square' if '■' in label else 'triangle' if '▲' in label else 'circle'
        for x,y in pts[::max(1,len(pts)//12)]:
            if y is None:continue
            px=sx(x);py=25+(hi-y)/(hi-lo)*220
            if shape=='square':element=f'<rect x="{px-4}" y="{py-4}" width="8" height="8"'
            elif shape=='triangle':element=f'<polygon points="{px},{py-5} {px-5},{py+4} {px+5},{py+4}"'
            else:element=f'<circle cx="{px}" cy="{py}" r="4"'
            out.append(element+f' fill="white" stroke="{color}" stroke-width="2"/>')
    out.append('<text x="300" y="300" font-size="14">Elapsed time from recording start [s]</text></svg>')
    if shaded_ranges:out.append('<p>회색 영역: 정지·재가동 구간. RPM은 평활화하지 않은 원래 값입니다.</p>')
    out.append('<p>'+ '　'.join(f'<span style="color:{c}">{esc(n)} ({"실선" if not d else "점선"})</span>' for n,_,c,d in series)+'</p>')
    return ''.join(out)


# 전후 비교 그래프는 센서 신호의 변경 위치를 보여줍니다.
def processing_report(path,raw,parts,audit,out,rid,original=None,origins=None):
    """원본 행 번호 기준으로 전처리 전후 신호와 처리 위치를 연결합니다."""
    original=read_csv(path) if original is None else original
    after={r['Source_Row']:r for _,rows in parts for r in rows}
    joined=origins is not None and len({source for source,_ in origins})>1
    axis='Combined row number' if joined else 'Original CSV row number'
    body='<p>가로축='+('본 측정과 냉각을 이어 붙인 행 순번' if joined else '원본 CSV 행 번호(헤더 포함)')+'. 회색 원형=전처리 전, 파란 네모=전처리 후. 주황 띠=처리 위치. 제외 행은 전처리 후 선이 없습니다.</p>'
    if joined:body+='<p>입력 순서: '+html.escape(' → '.join(dict.fromkeys(source for source,_ in origins)))+'</p>'
    for key in SENSOR:
        col=next((a for a in ALIASES[key] if a in original[0]),None)
        before=[(n,number(row.get(col))) for n,row in enumerate(original,2)]
        cleaned=[(n,after.get(n,{}).get(key)) for n in range(2,len(original)+2)]
        changed=[a['Source_Row'] for a in audit if a['Column'] in (key,'ROW','TIME_OR_COMMAND')]
        body+=chart([('○ 전처리 전',before,'#777',''),('■ 전처리 후',cleaned,'#1565c0','8 3')],key,key,changed).replace('Elapsed time from recording start [s]',axis)
    body+='<h2>처리 상세</h2><p>중앙값 평활 변경은 이상치 확정 판정이 아닙니다. 결측과 범위 오류도 구분해서 기록합니다.</p><table><tr><th>원본 파일</th><th>원본 행</th><th>칼럼</th><th>사유</th><th>전</th><th>후</th></tr>'
    for entry in audit:
        source,rownum=origins[entry['Source_Row']-2] if origins else (path.name,entry['Source_Row'])
        display={**entry,'Source':source,'Source_Row':rownum}
        body+='<tr>'+''.join('<td>'+html.escape(str(display.get(k,'')))+'</td>' for k in ('Source','Source_Row','Column','Reason','Before','After'))+'</tr>'
    write_html(out/'03_처리기록/05_실험별_전처리전후_그래프'/(rid+'.html'),path.name,body+'</table>')

def write_html(path,title,body):
    path.write_text('<!doctype html><meta charset="utf-8"><title>'+html.escape(title)+'</title><style>body{font:16px sans-serif;max-width:1150px;margin:30px auto;line-height:1.6}svg{width:100%}table{border-collapse:collapse}td,th{padding:8px;border:1px solid #ddd}</style><h1>'+html.escape(title)+'</h1>'+body,encoding='utf-8')

## 발표 예시: 실제 비가열 기록에서 온도·습도의 평활화 차이가 잘 보이는 구간을 선택합니다.
def smoothing_example(out,runs):
    folder=out/'03_처리기록/07_발표용_평활화예시';folder.mkdir(parents=True,exist_ok=True)
    description='# 온도·습도 평활화 예시\n\n비가열 기록(BASE_OFF/FAN_MATRIX)에서 30초 이후의 60초 창을 30초씩 이동하며 비교합니다. 표본 간 평균 절대변화가 가장 많이 줄어든 구간을 신호별로 선택했습니다. 발표용으로 차이가 뚜렷한 사례를 고른 것이며 전체 데이터의 평균 개선율이 아닙니다.\n\n가열에 따른 실제 급상승을 노이즈 제거 효과로 제시하지 않습니다. 온도·습도 각각 전체 기록과 선택 구간을 함께 보여주며, **확대 그래프는 세로축 범위도 다릅니다.** 원본과 처리값은 각 패널에서 같은 축을 사용합니다.\n\n'
    for key,name,filename in [('Temperature[degC]','온도','01_온도_평활화_전후비교'),('Humidity[%RH]','습도','02_습도_평활화_전후비교')]:
        best=None;best_score=0
        for run in sorted(runs,key=lambda r:r['id']):
            if run['kind'] not in ('BASE_OFF','FAN_MATRIX'):continue
            raw=read_csv(run['source']);col=next((k for k in ALIASES[key] if k in raw[0]),None)
            points=[dict(t=r['Time_From_Recording_Start[s]'],before=number(raw[r['Source_Row']-2].get(col)),after=r[key]) for r in run['rows']]
            continuous=all(0<b['t']-a['t']<=1.5*run['step'] for a,b in zip(points,points[1:]))
            if not continuous or any(p['before'] is None or p['after'] is None for p in points):continue
            for start in range(30,max(31,int(points[-1]['t'])-57),30):
                zoom=[p for p in points if start<=p['t']<=start+60]
                if len(zoom)<30 or zoom[-1]['t']-zoom[0]['t']<55:continue
                tv=lambda field:sum(abs(b[field]-a[field]) for a,b in zip(zoom,zoom[1:]))
                score=(tv('before')-tv('after'))/(len(zoom)-1)
                if score>best_score:best_score=score;best=(run,points,zoom)
        if best is None:description+=f'## {name}\n\n비교할 연속 구간이 없거나 변동량 감소가 확인되지 않아 예시를 생성하지 않았습니다.\n\n';continue
        description+=render_smoothing_example(folder,*best,key,name,filename)
    (folder/'사례_설명.md').write_text(description,encoding='utf-8')

def render_smoothing_example(folder,run,points,zoom,key,name,filename):
    unit='°C' if key.startswith('Temperature') else '%RH'
    def metrics(pts):
        tv=lambda name:sum(abs(b[name]-a[name]) for a,b in zip(pts,pts[1:]))
        a,b=tv('before'),tv('after')
        return dict(before=a,after=b,reduction=(a-b)/a*100 if a else None,
                    mean_before=st.mean(p['before'] for p in pts),mean_after=st.mean(p['after'] for p in pts))
    whole,detail=metrics(points),metrics(zoom)
    date=run['rows'][0]['Timestamp'][:10]
    subtitle=f"흡기 {run['key'][0]:g}% / 배기 {run['key'][1]:g}% · {date} · 후행 3표본 중앙값"
    desc=f'## {name}\n\n'+subtitle+'\n\n'
    desc+='| 비교 범위 | 표본 수 | 원본 변동량 | 처리 후 변동량 | 변동량 감소 | 원본 평균 | 처리 후 평균 |\n|---|---:|---:|---:|---:|---:|---:|\n'
    for scope_label,pts,m in [('전체',points,whole),('확대',zoom,detail)]:
        reduction=f"{m['reduction']:.1f}%" if m['reduction'] is not None else '계산 불가'
        desc+=f"| {scope_label} {pts[0]['t']:.3f}~{pts[-1]['t']:.3f}초 | {len(pts)} | {m['before']:.3f} | {m['after']:.3f} | {reduction} | {m['mean_before']:.4f} | {m['mean_after']:.4f} |\n"
    desc+=f'\n변동량은 연속한 두 표본의 차이에 절댓값을 취해 모두 더한 값입니다. 단위는 {unit}이며, 표본 수·간격·범위가 같은 원본/처리값을 비교했습니다. 표의 감소율은 측정 오차 또는 잡음의 감소율이 아닙니다.\n\n'
    desc+=f"선택한 확대 구간 원본 범위: {min(p['before'] for p in zoom):.3f}~{max(p['before'] for p in zoom):.3f} {unit}. 확대해서 보인다고 큰 환경 변화가 발생했다는 뜻은 아닙니다.\n\n"
    desc+='**발표 문장:** “실제 팬 운전 기록에서 3표본 중앙값을 적용하자 단기적인 오르내림이 줄어, 전체 추세를 읽기 쉬워졌습니다. 다만 실제 변화도 완화하거나 지연할 수 있어 원본과 함께 검증했습니다.”\n\n'
    desc+=f"원본: `{run['source']}`\n\n전처리 CSV: `{run['file']}`\n\n원본과 전달 CSV의 같은 시각 값을 그대로 사용했습니다. 재보간·추가 평활화·값 보정은 하지 않았습니다.\n"
    png=folder/(filename+'.png')
    # Windows 기본 .NET 차트로 PPT용 PNG를 만듭니다. 별도 Python 패키지는 필요 없습니다.
    # 다른 OS 또는 차트 기능이 없는 PC에서는 기존 SVG 그래프를 HTML로 제공합니다.
    renderer=r'''
param([string]$DataPath,[string]$PngPath)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms.DataVisualization
Add-Type -AssemblyName System.Drawing
$d=Get-Content -LiteralPath $DataPath -Encoding UTF8 -Raw | ConvertFrom-Json
$chart=New-Object System.Windows.Forms.DataVisualization.Charting.Chart
$chart.Width=2200;$chart.Height=1240;$chart.BackColor=[Drawing.Color]::White
function Title($text,$x,$y,$w,$h,$size,$bold) {
    $t=$chart.Titles.Add($text)
    $style=if($bold){[Drawing.FontStyle]::Bold}else{[Drawing.FontStyle]::Regular}
    $t.Font=[Drawing.Font]::new('Malgun Gothic',$size,$style)
    $t.ForeColor=[Drawing.ColorTranslator]::FromHtml('#18334b')
    $t.Position=[System.Windows.Forms.DataVisualization.Charting.ElementPosition]::new($x,$y,$w,$h)
}
Title $d.title 3 1 94 7 30 $true
Title $d.subtitle 3 9 94 5 18 $false
Title '전체 기록' 3 18 43 5 21 $true
Title $d.zoom_title 53 18 43 5 21 $true
$legend=$chart.Legends.Add('L');$legend.Font=[Drawing.Font]::new('Malgun Gothic',16)
$legend.Position=[System.Windows.Forms.DataVisualization.Charting.ElementPosition]::new(65,14,32,5)
$legend.LegendStyle='Row';$legend.BorderColor=[Drawing.Color]::Transparent
for($i=0;$i -lt 2;$i++) {
    $area=$chart.ChartAreas.Add("Area$i")
    $area.Position=[System.Windows.Forms.DataVisualization.Charting.ElementPosition]::new((2+50*$i),24,46,56)
    $area.AxisX.Minimum=if($i -eq 0){[Math]::Floor($d.points[0].t)}else{[Math]::Floor($d.zoom[0].t/10)*10}
    $area.AxisX.Maximum=if($i -eq 0){[Math]::Ceiling($d.points[-1].t/60)*60}else{[Math]::Ceiling($d.zoom[-1].t/10)*10}
    $area.AxisX.Interval=if($i -eq 0){60}else{10}
    $area.AxisY.Minimum=$d.ymin[$i];$area.AxisY.Maximum=$d.ymax[$i];$area.AxisY.Interval=$d.yinterval[$i]
    $area.AxisX.Title='측정 시작 후 경과 시간 [s]';$area.AxisY.Title=$d.ylabel
    foreach($axis in @($area.AxisX,$area.AxisY)) {
        $axis.TitleFont=[Drawing.Font]::new('Malgun Gothic',16)
        $axis.LabelStyle.Font=[Drawing.Font]::new('Malgun Gothic',14)
        $axis.MajorGrid.LineColor=[Drawing.ColorTranslator]::FromHtml('#e6edf2')
        $axis.LineColor=[Drawing.ColorTranslator]::FromHtml('#9baab5')
    }
    $area.AxisY.LabelStyle.Format=if($i -eq 1 -and $d.is_temperature){'0.00'}else{'0.0'}
    if($i -eq 0){
        $strip=New-Object System.Windows.Forms.DataVisualization.Charting.StripLine
        $strip.IntervalOffset=[double]$d.zoom[0].t;$strip.StripWidth=[double]$d.zoom[-1].t-[double]$d.zoom[0].t
        $strip.BackColor=[Drawing.Color]::FromArgb(18,0,125,152);$area.AxisX.StripLines.Add($strip)
    }
    $pts=if($i -eq 0){$d.points}else{$d.zoom}
    foreach($kind in @('before','after')) {
        $s=$chart.Series.Add("$kind$i");$s.ChartArea="Area$i";$s.ChartType='Line';$s.Legend='L'
        $s.IsVisibleInLegend=($i -eq 0);$s.LegendText=if($kind -eq 'before'){'원본 측정값'}else{'평활화 후'}
        $s.Color=[Drawing.ColorTranslator]::FromHtml($(if($kind -eq 'before'){'#9b9ba4'}else{'#007d98'}))
        $s.BorderWidth=if($kind -eq 'before'){2}else{4}
        if($i -eq 1){$s.MarkerStyle=if($kind -eq 'before'){'Circle'}else{'Diamond'};$s.MarkerSize=6}
        foreach($p in $pts){[void]$s.Points.AddXY([double]$p.t,[double]$p.$kind)}
    }
}
Title $d.metric_whole 3 81 94 5 18 $true
Title $d.metric_zoom 3 87 94 5 18 $false
Title '변동량 = 연속 표본의 절대변화 합. 변동량 감소가 센서 정확도 향상을 뜻하지는 않습니다.' 3 94 94 4 14 $false
$chart.SaveImage($PngPath,[System.Windows.Forms.DataVisualization.Charting.ChartImageFormat]::Png)
$chart.Dispose()
'''
    try:
        if os.name!='nt':raise OSError('Windows 차트 기능 없음')
        is_temperature=key.startswith('Temperature');ymin=[];ymax=[];interval=[]
        for i,pts in enumerate((points,zoom)):
            values=[p[k] for p in pts for k in ('before','after')];midpoint=(max(values)+min(values))/2
            minimum=(10 if is_temperature else 5) if i==0 else (0.2 if is_temperature else 1.2)
            tick=(2 if is_temperature else 1) if i==0 else (0.05 if is_temperature else 0.2)
            span=max(minimum,(max(values)-min(values))*1.3)
            ymin.append(round(math.floor((midpoint-span/2)/tick)*tick,6))
            ymax.append(round(math.ceil((midpoint+span/2)/tick)*tick,6));interval.append(tick)
        payload=dict(points=points,zoom=zoom,subtitle=subtitle,title=f'{name} | 원본과 중앙값 평활화 비교',
            zoom_title=f"{zoom[0]['t']:.0f}~{zoom[-1]['t']:.0f}초 확대 (세로축 다름)",
            ylabel=f'{name} [{unit}]',is_temperature=is_temperature,ymin=ymin,ymax=ymax,yinterval=interval,
            metric_whole=f"전체 기록: 변동량 {whole['before']:.3f} → {whole['after']:.3f} {unit} · {whole['reduction']:.1f}% 감소",
            metric_zoom=f"확대 구간: 변동량 {detail['before']:.3f} → {detail['after']:.3f} {unit} · "+(f"{detail['reduction']:.1f}% 감소" if detail['reduction'] is not None else '원본 변동량 0'))
        with tempfile.TemporaryDirectory(prefix='airguard_plot_') as tmp:
            script=Path(tmp)/'plot.ps1';data=Path(tmp)/'points.json'
            script.write_text(renderer,encoding='utf-8-sig');data.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8-sig')
            powershell=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
            subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-File',str(script),str(data),str(png.resolve())],
                           check=True,capture_output=True,timeout=60,creationflags=subprocess.CREATE_NO_WINDOW)
        desc+=f'\n![{name} 원본과 평활화 비교]({filename}.png)\n\n'
    except (OSError,subprocess.SubprocessError) as exc:
        body='<p>'+html.escape(subtitle)+'</p>'
        for title,pts in [('전체 기록',points),('일부 확대',zoom)]:
            body+=chart([('○ 원본',[(p['t'],p['before']) for p in pts],'#999',''),('■ 평활화 후',[(p['t'],p['after']) for p in pts],'#007d98','')],title,key)
        write_html(folder/(filename+'.html'),'평활화 예시',body)
        desc+='\nPNG 생성 기능을 사용할 수 없어 HTML 그래프로 저장했습니다.\n'
        print('평활화 예시: PNG 대신 HTML 저장 ('+type(exc).__name__+')')
    return desc


## 5. 전체 실행: 센서 CSV / 분석 요약 / 처리 기록
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--input',type=Path);p.add_argument('--output',type=Path);p.add_argument('--replace',action='store_true',help='이 코드가 만든 기존 결과만 교체')
    p.add_argument('--kind',choices=sorted(set(KINDS.values())),help='폴더명으로 구분할 수 없을 때 선택한 전체 폴더의 실험 종류 지정')
    a=p.parse_args();root=a.input
    # --input이 없는 일반 실행에서는 매번 원본 폴더를 직접 선택합니다.
    if root is None:
        import tkinter as tk
        from tkinter import filedialog
        ui=tk.Tk();ui.withdraw();chosen=filedialog.askdirectory(title='원본 폴더 선택 (baseline / fan_matrix / heating_5s / heat_30s)');ui.destroy()
        if not chosen:return
        root=Path(chosen)
    if not root.is_dir():raise ValueError('선택한 원본 폴더가 없습니다.')
    # '261014 온도 실험 데이터 heat_30s'처럼 날짜가 붙은 이름도 인식합니다.
    root,folders=discover_folders(root,a.kind)
    out=a.output or Path(__file__).resolve().parent/'E4-AIRGUARD 데이터 품질·통합 평가'
    out=out.resolve()
    if out==root or root in out.parents:raise ValueError('출력 폴더를 원본 폴더 밖으로 지정하세요.')
    if out in root.parents:raise ValueError('원본의 상위 폴더를 출력으로 사용할 수 없습니다.')
    # 원본·기존 결과는 삭제하지 않습니다. 재실행은 번호를 붙인 형제 폴더에 저장합니다.
    if a.replace:raise ValueError('v2는 기존 결과를 삭제하지 않습니다. --replace 없이 실행하세요.')
    base=out;version=2
    while out.exists() and any(out.iterdir()):
        out=base.with_name(base.name+f'_{version:02d}');version+=1
    out.mkdir(parents=True,exist_ok=True)

    for name in ['01_AI전달_CSV','02_데이터분석_요약/개별실험_그래프','03_처리기록/05_실험별_전처리전후_그래프']: (out/name).mkdir(parents=True,exist_ok=True)
    summary=[];manifest=[];audit_all=[];runs=[];hashes={};review_summary=[];heat_pairs=[];record_ids=set()
    for folder,kind in folders:
        print('입력:',folder,'| 종류:',kind,flush=True)
        for sources,review_note in input_groups(folder,kind):
            if kind=='HEAT_30S':
                heat_pairs.append((len(sources)>1,bool(review_note)))
            path=sources[0]
            digest=hashlib.sha256(path.read_bytes()).hexdigest();relative=path.relative_to(root).as_posix()
            # 본 측정이 같아도 추가 냉각이 다르면 별도 입력입니다. 연결한 전체 내용을 비교합니다.
            source_hashes=tuple(hashlib.sha256(source.read_bytes()).hexdigest() for source in sources)
            if source_hashes in hashes:
                for source,source_hash in zip(sources,source_hashes):
                    manifest.append(dict(Source=source.relative_to(root).as_posix(),SHA256=source_hash,
                        Status='DUPLICATE',Same_As=hashes[source_hashes]))
                continue
            hashes[source_hashes]=relative;audit=[]
            try:
                # 내부 행 순번은 이어 붙이고, 처리 기록에는 각 원본 파일의 행 번호를 남깁니다.
                original=[];origins=[];input_counts=[]
                for source in sources:
                    rows=read_csv(source);input_counts.append(len(rows));original.extend(rows)
                    origins.extend((source.relative_to(root).as_posix(),n) for n in range(2,len(rows)+2))
                raw=load_record(path,kind,audit,original);parts,boundaries,step=clean_record(raw,kind,audit)
            except Exception as e:
                for source in sources:
                    manifest.append(dict(Source=source.relative_to(root).as_posix(),SHA256=hashlib.sha256(source.read_bytes()).hexdigest(),Status='ERROR',Message=str(e)))
                continue
            date=raw[0]['Timestamp'].replace('-','').replace(':','').replace('T','_').split('.')[0] or 'unknown_time'
            file_tag=digest[:8];rid=f'{kind}_{date}_{file_tag}'
            if rid in record_ids:
                file_tag=hashlib.sha256('|'.join(source_hashes).encode()).hexdigest()[:8]
                rid=f'{kind}_{date}_{file_tag}'
            record_ids.add(rid)
            # 처음 보는 사람도 알 수 있도록 전후 그래프 이름에 실험 조건을 적습니다.
            tag=lambda v:'미상' if v is None else f'{v:g}'
            when=datetime.fromisoformat(raw[0]['Timestamp']).strftime('%Y-%m-%d_%H시%M분%S초') if raw[0]['Timestamp'] else '시각미기록'
            report_name=f"{LABELS[kind]}__흡기{tag(raw[0]['values'][SIGNALS[3]])}_배기{tag(raw[0]['values'][SIGNALS[4]])}__{when}__{file_tag}"
            # 수집 상태는 RAW에서 읽어 연결 검증·경고에만 사용하고 결과에 복사하지 않습니다.
            meta=read_meta(path);cooling_meta=read_meta(sources[1]) if len(sources)>1 else {}
            processing_report(path,raw,parts,audit,out,report_name,original,origins)
            # 30초 가열은 실험 전체를 CSV 하나로 저장합니다.
            # 필터는 각 연속 구간에 이미 따로 적용했으므로 가열 경계를 넘지 않습니다.
            output_parts=[(None,[row for _,segment in parts for row in segment])] if kind=='HEAT_30S' and parts else parts
            for part_no,rows in output_parts:
                i=rows[0][SIGNALS[3]];e=rows[0][SIGNALS[4]]
                tag=lambda v:'UNKNOWN' if v is None else f'{v:g}'
                suffix='' if part_no is None else f'_part{part_no:02d}'
                label='미연결냉각_검토용' if review_note else LABELS[kind]
                eid=rid+suffix;filename=f'{label}__흡기{tag(i)}_배기{tag(e)}__{date}__{file_tag}{suffix}.csv'
                for r in rows:r.update(Experiment_ID=eid,Recording_ID=rid,Experiment_Type=kind)
                # 30초 가열 실험에만 원본 가열 상태를 추가합니다.
                export_signals=required_signals(kind)
                extra_columns=list(RPM_RAW.values())+['Fan_Section']
                export_columns=['Measurement_Date','Time[s]']+export_signals+extra_columns
                # 날짜는 각 행의 원본 시각에서 가져옵니다. 날짜 기록이 없으면 추정하지 않습니다.
                output_folder='03_처리기록/미연결냉각_검토용' if review_note else '01_AI전달_CSV'
                write_csv(out/output_folder/filename,
                          [{'Measurement_Date':datetime.fromisoformat(r['Timestamp']).date().isoformat() if r['Timestamp'] else '',
                            'Time[s]':r['Time_From_Recording_Start[s]'],**{k:r[k] for k in export_signals+extra_columns}} for r in rows],export_columns)
                warnings=[]
                if meta.get('status') not in (None,'complete'):warnings.append('실험상태_'+str(meta.get('status')))
                if meta.get('error'):warnings.append(str(meta['error']))
                if cooling_meta.get('status') not in (None,'complete'):warnings.append('냉각상태_'+str(cooling_meta['status']))
                if cooling_meta.get('error'):warnings.append('냉각_'+str(cooling_meta['error']))
                if review_note:warnings.append(review_note)
                if len(sources)>1:warnings.append('본측정과_후속냉각_통합')
                if kind=='HEAT_30S':warnings.append('30초가열_Heat_Load로가열구간확인')
                if kind=='BASE_OFF' and (i,e)!=(0,0):warnings.append('무풍폴더_PWM불일치')
                if kind=='HEAT_5S':warnings.append('가열종료시각_미확인_기록시작기준')
                if all(r[SIGNALS[2]]==0 for r in rows):warnings.append('PM전부0_실제0또는상태확인필요')
                if 'bme_err' not in read_csv(path)[0]:warnings.append('센서오류플래그_미제공')
                if boundaries:warnings.append('시간또는명령변경_평활화구간분리')
                stats=dict(Experiment_ID=eid,Recording_ID=rid,Type=kind,Intake_PWM=i,Exhaust_PWM=e,
                    Measurement_Start=rows[0]['Timestamp'],Measurement_End=rows[-1]['Timestamp'],
                    Rows=len(rows),Duration_s=round(rows[-1]['Time_From_Recording_Start[s]']-rows[0]['Time_From_Recording_Start[s]'],6),
                    Median_Interval_s=step,Source=relative,CSV=filename,
                    Before_After_Graph='03_처리기록/05_실험별_전처리전후_그래프/'+report_name+'.html',
                    Acquisition_Status=meta.get('status','not_recorded'),Warnings=';'.join(warnings))
                if kind=='HEAT_30S':
                    stats.update(Main_Input_Rows=0 if review_note else input_counts[0],
                        Cooling_Input_Rows=sum(input_counts[1:]) if not review_note else input_counts[0],
                        Cooling_Source=sources[1].relative_to(root).as_posix() if len(sources)>1 else (relative if review_note else ''),
                        Cooling_Start_s=next((round(r['clock']-raw[0]['clock'],6) for r in raw if r['Source_Row']>input_counts[0]+1),'') if len(sources)>1 else '',
                        Cooling_Status=cooling_meta.get('status','not_linked'),Output_Folder=output_folder)
                for k in SENSOR:
                    values=[r[k] for r in rows];stats[k+'_First']=values[0];stats[k+'_Last']=values[-1];stats[k+'_Median']=median(values);stats[k+'_Missing_Count']=sum(v is None for v in values)
                    stats[k+'_Delta'],stats[RATE_COLUMNS[k]]=interval_change(rows,k)
                stats.update(cooling_metrics(rows,step) if kind=='HEAT_30S' else dict.fromkeys(COOLING_COLUMNS))
                if kind=='HEAT_30S' and stats['Cooling_Duration_s'] is None:
                    stats['Warnings']+=';연속된_단일가열_종료확인불가_냉각통계미계산'
                if review_note:review_summary.append(stats)
                else:
                    summary.append(stats);runs.append(dict(kind=kind,id=eid,rows=rows,key=(i,e),file=filename,recording=rid,source=path,step=step))
            for entry in audit:
                source,rownum=origins[entry['Source_Row']-2]
                audit_all.append({**entry,'Source':source,'Source_Row':rownum})
            # 입력 파일별 행 수를 대조할 수 있도록, 보존한 행을 원본 파일별로 셉니다.
            retained=Counter(origins[r['Source_Row']-2][0] for _,rows in parts for r in rows)
            for source,count_in,source_hash in zip(sources,input_counts,source_hashes):
                source_name=source.relative_to(root).as_posix();count_out=retained[source_name]
                manifest.append(dict(Source=source_name,SHA256=source_hash,
                    Status=('REVIEW_ONLY' if review_note else 'OK') if parts else 'NO_RETAINED_ROWS',
                    Input_Rows=count_in,Retained_Rows=count_out,Excluded_Rows=count_in-count_out,
                    Recording_ID=rid,Output_Files=len(output_parts),Smoothing_Segments=len(parts),Message=review_note))
    if not runs:
        write_csv(out/'03_처리기록/02_파일별_처리결과와_행수.csv',manifest)
        raise ValueError('처리 가능한 CSV가 없습니다. 처리 중 오류 또는 입력 구조를 확인하세요.')
    ## 동일 I/E 조합만 같은 색. 약풍 파랑, 중풍 녹색, 강풍 빨강 계열.
    import colorsys
    keys=sorted(set(r['key'] for r in runs),key=lambda pair:tuple(-1 if v is None else v for v in pair));palette={}
    grouped={}
    for key in keys:
        peak=max(v or 0 for v in key);group=0 if peak==0 else 1 if peak<=35 else 2 if peak<=60 else 3
        grouped.setdefault(group,[]).append(key)
    for g,items in grouped.items():
        for n,key in enumerate(items):
            rgb=colorsys.hls_to_rgb({0:0,1:.61,2:.36,3:.01}[g],.25+.35*(n+1)/(len(items)+1),0 if g==0 else .75)
            palette[key]='#'+''.join(f'{round(x*255):02x}' for x in rgb)
    repetition_ids={};count=Counter();dash=['','8 4','2 3','8 3 2 3']
    for r in runs:
        pair=(r['kind'],r['key'])
        record_pair=(*pair,r['recording'])
        if record_pair not in repetition_ids:
            count[pair]+=1;repetition_ids[record_pair]=count[pair]
        r['repeat']=repetition_ids[record_pair];r['dash']=dash[(r['repeat']-1)%4]
        tail=[x for x in r['rows'] if x['Fan_Section']==FAN_TAIL]
        ranges=[(tail[0]['Time_From_Recording_Start[s]'],tail[-1]['Time_From_Recording_Start[s]'])] if tail else []
        body='<p>전체 기록을 표시합니다. RPM의 회색 선은 원래 값, 색 선은 평활화 후 값입니다.</p>'
        for k in PLOT_SIGNALS:
            if not any(x[k] is not None for x in r['rows']):continue
            pts=plot_points(r,k)
            series=[(short_label(r),pts,palette[r['key']],r['dash'])]
            if k in RPM_RAW:series.insert(0,('RPM 원값',plot_points(r,RPM_RAW[k]),'#a1a1aa',''))
            body+=chart(series,k,k,shaded_ranges=ranges)
        if tail:
            body+='<h2>끝부분 정지·재가동 확대</h2><p>첫 양쪽 PWM=0 전환 전 5초부터 표시합니다. 이 구간을 삭제하거나 부드럽게 연결하지 않습니다.</p>'
            start=max(0,ranges[0][0]-5);end=ranges[0][1]
            for k in RPM_RAW:
                points=[p for p in plot_points(r,k) if start<=p[0]<=end]
                body+=chart([(short_label(r),points,palette[r['key']],'')],k,k,shaded_ranges=ranges,x_range=(start,end))
        write_html(out/'02_데이터분석_요약/개별실험_그래프'/(r['id']+'.html'),r['file'],body)
    # 비교 그림은 신호별 페이지로 분리하고, 한 그림에는 최대 3개 조건만 표시합니다.
    # 흡기 PWM을 고정한 상태에서 배기 PWM 효과를 비교합니다.
    for kind in LABELS:
        selected=[r for r in runs if r['kind']==kind]
        if not selected:continue
        for k in PLOT_SIGNALS:
            if not any(x[k] is not None for r in selected for x in r['rows']):continue
            body='<p>색=정확한 I/E 조합, ○/■/▲=배기 단계, 선 스타일=같은 조건의 반복. 끝부분 정지·재가동 구간은 비교선에서 제외하고, 전체 CSV와 개별실험 그래프에 보존합니다. 서로 다른 날의 차이를 팬 효과로 단정하지 마세요.</p>'
            for intake in sorted(set(r['key'][0] for r in selected),key=lambda v: (v is None, v if v is not None else 0)):
                subset=sorted([r for r in selected if r['key'][0]==intake],key=lambda r:(r['rows'][0]['Heat_Load[0/1]'] or 0, r['rows'][0]['Time_From_Recording_Start[s]']//20, r['key'][1] if r['key'][1] is not None else -1,r['repeat']))
                exhausts=sorted(set(r['key'][1] for r in subset),key=lambda v: (v is None, v if v is not None else 0))
                # 반복이 많아도 한 그림의 최대 선 수를 제한합니다.
                for start in range(0,len(subset),3):
                    series=[]
                    for r in subset[start:start+3]:
                        mark=['○','■','▲'][exhausts.index(r['key'][1])%3]
                        label=f'{mark} {short_label(r)}'
                        series.append((label,plot_points(r,k,include_tail=False),palette[r['key']],r['dash']))
                    body+=chart(series,f'흡기 {intake}% 고정 / '+k,k)
                    body+='<details><summary>원본 실험 정보</summary><ul>'+''.join(
                        '<li>'+html.escape(short_label(r))+' → <a href="개별실험_그래프/'+html.escape(r['id'])+'.html">전체 기록·끝부분 확대</a></li>'
                        for r in subset[start:start+3])+'</ul></details>'
            write_html(out/'02_데이터분석_요약'/(LABELS[kind]+'_'+k.split('[')[0]+'.html'),LABELS[kind]+' '+k,body)
    # 요약에는 비교할 값만 남기고, 원본 경로·ID·실험 상태는 별도 처리 기록에 보존합니다.
    front=['Type','Intake_PWM','Exhaust_PWM','Measurement_Start','Measurement_End','Duration_s','Rows']
    sensor_stats=[column for key in SENSOR for column in
                  [key+'_First',key+'_Last',key+'_Median',key+'_Delta',RATE_COLUMNS[key]]]
    visible=front+sensor_stats+(COOLING_COLUMNS if any(r['Type']=='HEAT_30S' for r in summary) else [])
    write_csv(out/'02_데이터분석_요약/실험별_요약.csv',
              [{k:r.get(k) for k in visible} for r in summary],visible)
    # 전체 행을 저장한 채 구간별 위치와 행 수를 별도 요약합니다.
    section_summary=[]
    for run in runs:
        sections=[]
        for row in run['rows']:
            if not sections or sections[-1][-1]['Fan_Section']!=row['Fan_Section']:sections.append([])
            sections[-1].append(row)
        for rows in sections:
            section_summary.append(dict(CSV=run['file'],Intake_PWM=run['key'][0],Exhaust_PWM=run['key'][1],
                Fan_Section=rows[0]['Fan_Section'],Start_s=rows[0]['Time_From_Recording_Start[s]'],
                End_s=rows[-1]['Time_From_Recording_Start[s]'],Rows=len(rows)))
    write_csv(out/'02_데이터분석_요약/팬운전구간_요약.csv',section_summary)
    # 요약과 같은 순서·실험 조건으로 연결할 수 있습니다. 학습 분할에는 Recording_ID를 사용합니다.
    trace=front+[k for k in dict.fromkeys(k for r in summary for k in r) if k not in front+sensor_stats+COOLING_COLUMNS]
    write_csv(out/'03_처리기록/01_실험목록과_원본파일연결.csv',[{k:r.get(k) for k in trace} for r in summary],trace)
    if review_summary:write_csv(out/'03_처리기록/미연결냉각_검토용/검토목록.csv',review_summary)
    write_csv(out/'03_처리기록/02_파일별_처리결과와_행수.csv',manifest)
    write_csv(out/'03_처리기록/03_측정값_변경과제외_상세.csv',audit_all,['Source','Source_Row','Column','Reason','Before','After'])
    (out/'03_처리기록/04_전처리_적용설정.json').write_text(json.dumps(dict(input=str(root),median_window=3,
        smoothed_signals=SENSOR,required_export_signals=SIGNALS[:-1],missing_policy='keep_row_and_blank_invalid_cell',
        startup_removal=False,rpm_smoothing='trailing_median_3_with_constant_PWM',
        rpm_raw_columns=RPM_RAW,rpm_tail_policy='preserve_raw',
        tail_rule='HEAT_30S: first both-PWM-zero transition after one complete heating block, through end',
        interpolation=False,statistical_outlier_removal=False,
        temperature_valid_range=[-20,80]),ensure_ascii=False,indent=2),encoding='utf-8')
    smoothing_example(out,runs)
    (out/'03_처리기록/00_처리기록_읽어주세요.md').write_text('''# 처리 기록 안내

이 폴더는 전처리 결과를 검증하고 원본을 찾기 위한 자료입니다. 학습용 센서값은 01_AI전달_CSV에 있습니다.

| 확인할 내용 | 파일·폴더 |
|---|---|
| 어떤 실험이며 원본과 결과 CSV가 무엇인가 | 01_실험목록과_원본파일연결.csv |
| 각 원본에서 몇 행을 읽고 남겼는가 | 02_파일별_처리결과와_행수.csv |
| 어느 칼럼의 값이 어떻게 바뀌었거나 제외됐는가 | 03_측정값_변경과제외_상세.csv |
| 어떤 기준으로 처리했는가 | 04_전처리_적용설정.json 및 결과 폴더의 README_전처리과정.md |
| 개별 실험의 원본과 처리 후 그래프 | 05_실험별_전처리전후_그래프/ |
| 발표에 넣을 평활화 예시와 수치 설명 | 07_발표용_평활화예시/ |

그래프 이름에는 실험 종류, 흡기·배기 PWM, 측정 시작 시각이 표시됩니다. 마지막 짧은 식별자는 이름 중복을 방지합니다.
원본 연결표의 Before_After_Graph는 결과 폴더 기준 상대경로입니다. 같은 원본에서 나온 파일은 Recording_ID로 확인할 수 있습니다.
변경·제외 상세의 한 행은 센서값 하나의 변경 또는 처리 이벤트입니다. 기록 건수를 삭제된 데이터 행 수로 해석하지 마세요.
수집 상태·이벤트·검증 기록은 RAW에만 보관합니다. 실험 상태와 주요 경고는 01_실험목록과_원본파일연결.csv에서 확인할 수 있습니다.
''',encoding='utf-8')
    linked=sum(linked for linked,_ in heat_pairs)
    unlinked=sum(review for _,review in heat_pairs)
    main_count=sum(not review for _,review in heat_pairs)
    connection=f'30초 가열: 본 측정 {main_count}개 / 냉각 연결 {linked}개 / 미연결 냉각 {unlinked}개'
    guide="""# E4-AIRGUARD 센서 데이터 전처리 안내

이 자료는 팬 운전 및 가열 조건에 따른 온도·습도·PM2.5·회전수 변화를 분석하기 위한 센서 데이터입니다. 원본 시각과 센서값을 검사하고, 온도·습도·PM2.5와 일정 PWM 구간의 RPM에 후행 3표본 중앙값을 적용합니다. 센서 결측이 있는 행도 보존하며 해당 값만 빈칸으로 둡니다. 원본 CSV는 수정하지 않고 각 변경 내용은 처리 기록에 남깁니다.

## 1. 데이터 구성

| 원본 폴더 | 실험 내용 |
|---|---|
| `baseline` | 팬을 끈 상태의 환경 측정 |
| `fan_matrix` | 흡기·배기 팬의 PWM 조합별 측정 |
| `heating_5s` | 5초 가열 후 환경 변화 측정 |
| `heating_30s` 또는 `heat_30s` | 측정 중 30초 가열을 포함한 환경 변화 측정 |

실험 종류는 원본 폴더를 기준으로 구분합니다. `261014 온도 실험 데이터 heat_30s`처럼 날짜·설명이 붙은 이름도 인식합니다. 이름으로 구분할 수 없으면 `--kind HEAT_30S`처럼 지정할 수 있습니다. 팬 조건과 실험 종류가 다른 데이터는 별도 CSV로 저장합니다.

### 30초 가열 실험과 추가 냉각 연결

30초 가열 실험은 `실험폴더/조건명/조건명.csv`에서 읽습니다. 가열 전·가열 중·회복이 한 CSV에 들어 있으면 실제 기록 끝까지 처리하며 300초로 자르지 않습니다. 가열 구간은 원본 `Heat_Load`로 구분합니다.
추가 냉각을 별도로 기록한 기존 구조에서는 `실험폴더/cooling/조건명/cooling.csv`를 찾아 같은 실험명의 두 파일을 한 CSV로 저장합니다. 실험 폴더명·CSV 칼럼·흡기/배기 PWM이 일치하고, 냉각 메타데이터에 연속 측정이 표시돼 있어야 합니다. 본 측정 종료와 냉각 시작 이벤트 시각은 5초 이내여야 하고, 냉각 측정 시각은 본 측정 시각 이후여야 합니다. 냉각 구간의 `Heat_Load`가 0인지도 확인합니다. 별도 냉각 파일이 없어도 본 CSV는 정상 처리합니다.

전체 시간은 본 측정의 첫 유효 시각을 기준으로 계산합니다. 냉각 파일에 임의로 300초를 더하지 않으며 원본 시각의 공백을 유지합니다. 파일만 바뀌고 측정이 연속되면 필터도 이어서 적용하고, 시간 공백이나 명령 변경이 있으면 필터를 초기화합니다.
매 실행마다 현재 RAW 폴더에 존재하는 CSV만 읽습니다. 삭제된 파일이나 이전 출력은 입력으로 가져오지 않습니다. 연결 조건이 맞지 않는 냉각 파일이 실제로 있을 때만 `03_처리기록/미연결냉각_검토용`을 생성합니다. 정상 01~09 쌍은 각각 하나의 실험으로 통합합니다.

### 전달용 CSV 칼럼

| 칼럼 | 의미와 단위 |
|---|---|
| `Measurement_Date` | 각 행의 원본 측정 날짜, YYYY-MM-DD 형식 |
| `Time[s]` | 원본 실험의 첫 유효 측정 시각을 0초로 둔 경과 시간 |
| `Temperature[degC]` | 온도, °C |
| `Humidity[%RH]` | 상대습도, %RH |
| `PM2.5[ug/m3]` | PM2.5 입자 질량농도 센서값, µg/m³ |
| `Intake_PWM[%]` | 흡기팬 PWM 명령값, % |
| `Exhaust_PWM[%]` | 배기팬 PWM 명령값, % |
| `Intake_RPM[rpm]` | 일정 PWM 구간에서 평활화한 흡기팬 회전수, rpm. 정지·재가동 끝 구간은 원값 |
| `Exhaust_RPM[rpm]` | 일정 PWM 구간에서 평활화한 배기팬 회전수, rpm. 정지·재가동 끝 구간은 원값 |
| `Intake_RPM_Raw[rpm]`, `Exhaust_RPM_Raw[rpm]` | 평활화 전 유효 RPM. 원본의 빈칸·수치 오류는 빈칸 |
| `Fan_Section` | 일정 PWM 구간 / 정지·재가동 구간 / PWM 확인 필요 |
| `Heat_Load[0/1]` | `heating_30s`에만 포함. 1은 가열 구간, 0은 가열 전·후 구간 |

기본 CSV는 12개 칼럼이며, `heating_30s`는 가열 상태를 포함해 13개입니다. `Heat_Load`는 가열 여부를 나타내며 열출력(W)이 아닙니다. PM2.5는 가스 농도가 아닙니다. RPM 분석에서 실측값 자체가 필요하면 `_Raw` 칼럼을 사용합니다.

`Measurement_Date`는 전처리를 실행한 날짜가 아니라 원본 시각의 날짜입니다. 자정을 넘겨 측정하면 해당 행부터 날짜가 바뀝니다. 원본에 경과 시간만 있고 날짜가 없으면 빈칸으로 남깁니다.

`Time[s] = 현재 원본 시각 − 해당 원본 실험의 첫 유효 시각`으로 계산합니다. 예를 들어 22:57:52.609에 시작하여 22:57:53.608에 측정한 값은 0.999초입니다. 실제 시간 간격을 유지하며 행 번호로 시간을 만들지 않습니다.
`heating_30s`는 가열 전·가열 중·가열 후 및 연결된 추가 냉각을 실험당 CSV 하나에 순서대로 저장합니다. 가열 단계는 `Heat_Load[0/1]`로 구분하고, `Time[s]`는 가열 상태가 바뀌거나 냉각 파일로 넘어가도 계속 이어집니다. 파일명에 part 번호를 붙이지 않습니다.
다른 실험에서 연속 구간이 나뉘는 경우에는 part 파일로 저장하되 원본 기준 경과 시간을 유지합니다. 센서 결측 때문에 행을 삭제하거나 시간을 당기지 않습니다. 원본 시계가 역행하면 경과 시간도 역행할 수 있습니다.
센서 비교 그래프도 같은 경과 시간 기준입니다. 파일과 원본 실험의 연결 정보는 `03_처리기록/01_실험목록과_원본파일연결.csv`에서 확인할 수 있습니다.

## 2. 처리 순서

**CSV 읽기 → 시각·중복 검사 → 결측·범위 오류 검사 → 연속 구간 분리 → 중앙값 필터 → CSV·그래프·처리 기록 저장**

### 2.1 시각과 중복 검사

파일의 SHA256 해시가 같으면 내용이 동일한 중복 파일로 판단하여 한 번만 처리합니다. 본 측정과 냉각을 연결하는 경우에는 두 파일 모두 같은 경우에만 중복 입력으로 판단합니다. 한 실험 안에서 시각을 포함한 모든 칼럼 값이 동일한 행도 한 번만 사용합니다. 센서값이 같아도 측정 시각이 다른 행은 서로 다른 관측으로 유지합니다.

측정 시각을 해석할 수 없는 행은 제외합니다. 해석 가능한 시각은 원본 기록 순서대로 처리하며, 시간 역행이나 동일 시각의 서로 다른 관측은 구간을 나누어 보존합니다.

### 2.2 결측치와 이상치 판별

빈 값, 숫자로 변환할 수 없는 값, NaN과 무한대는 유효하지 않은 값으로 처리합니다. 숫자로 읽힌 값에는 다음 범위 검사를 적용합니다. 범위의 양 끝값은 포함합니다.

| 항목 | 코드에서 허용하는 범위 |
|---|---|
| 온도 | −20~80°C |
| 상대습도 | 0~100%RH |
| 흡기·배기 PWM | 0~100% |
| PM2.5 | 0 이상 |
| 흡기·배기 RPM | 0 이상 |
| 가열 상태 | 0 또는 1 |

이 방법은 **설정한 범위와 센서 상태에 근거한 규칙 기반 오류 판별**입니다. 온도 범위는 프로젝트의 점검 설정이며 센서 고장을 확정하는 기준은 아닙니다. PM2.5와 RPM에는 현재 상한 검사가 없으므로, 양수라는 이유만으로 센서의 측정 범위나 정확도가 검증된 것은 아닙니다.

원본에 `bme_err` 또는 `pms_stale`가 있으면 해당 상태도 검사합니다. 값이 0으로 확인되지 않는 경우 각각 온습도 또는 PM 값을 무효로 처리합니다. 상태 칼럼이 없으면 수치·범위 검사만 수행합니다.

센서·PWM·RPM·가열 상태에 결측 또는 범위 오류가 있으면 **해당 칼럼만 빈칸으로 두고 행과 나머지 정상 값은 보존**합니다. 빈칸을 0으로 바꾸거나 보간하지 않습니다. 평활화 창도 해당 신호의 결측에서 초기화하여 공백 앞뒤를 섞지 않습니다. 실제 측정된 0은 유효 범위에 포함되므로 보존합니다. 시각 해석 불가 및 완전 중복에 대한 앞 절의 검사는 별도로 유지합니다.

평균에서 멀거나 변화량이 크다는 이유만으로 값을 이상치로 삭제하지 않습니다. 가열이나 팬 운전으로 발생한 실제 변화를 보존하기 위해서입니다. 이후 중앙값 필터로 값이 바뀌더라도, 그 값이 측정 오류로 확정됐다는 뜻은 아닙니다.

### 2.3 연속 구간 분리

각 원본 파일에서 양수인 인접 시각 차이들의 중앙값을 대표 측정 간격으로 구합니다. 다음 상황에서는 별도의 연속 구간으로 나눕니다.

- 시간이 역행하거나 동일 시각이 반복되는 경우
- 인접 시각 차이가 대표 측정 간격의 1.5배를 초과하는 경우
- 흡기·배기 PWM 또는 기록된 가열 상태가 바뀌는 경우
- 시각 오류·중복 행을 제외하여 원본 행의 연속성이 끊기는 경우

센서 결측은 행을 삭제하지 않으며 그 신호의 필터만 초기화합니다. RPM은 흡기/배기 PWM 조합·시각·행의 연속성으로 창을 관리하며, 가열 상태만 바뀐 경우에는 창을 이어갑니다.

예를 들어 대표 측정 간격이 1초라면 1.5초를 초과하는 간격에서 필터 구간을 나눕니다. 이렇게 하면 서로 다른 운전 조건이나 끊긴 기록을 하나의 필터 창으로 처리하지 않게 됩니다.
필터 구간과 저장 파일은 별개입니다. `heating_30s`는 구간별 평활화를 마친 뒤 유효한 행을 원래 순서로 합쳐 CSV 하나에 저장합니다. 가열 ON/OFF 때문에 행을 삭제하지 않습니다. 실제 공백은 채우지 않으며, 그래프에서도 공백·시간 역행·제외 행을 가로질러 선을 연결하지 않습니다. 다른 실험은 연속 구간별 part 파일로 저장합니다.

### 2.4 시그널 처리: 후행 3표본 중앙값 필터

온도·습도·PM2.5에는 다음 식을 적용합니다.

```text
처리값[i] = median(원본값[i−2], 원본값[i−1], 원본값[i])
```

현재 값과 직전 두 값을 크기순으로 정렬하여 가운데 값을 선택하는 방식입니다. 한 표본에서만 나타나는 큰 튐의 영향을 줄일 수 있습니다. 예를 들어 온도 세 값이 `24.4, 24.5, 27.0`이면 세 번째 시각의 처리값은 `24.5`가 됩니다. 이 예시는 계산 방식 설명용입니다.

필터는 평활화 전의 관측값으로 계산합니다. 구간의 첫 두 값은 그대로 사용하고, 새 구간에서는 필터를 초기화합니다. PWM·가열 상태는 변경하지 않습니다.

### 2.5 RPM 평활화와 끝부분 구분

- 흡기·배기 PWM 조합이 일정하고 시각·표본이 연속인 구간에서 현재와 직전 두 RPM의 중앙값을 사용합니다. 각 팬의 PWM이 0이거나 PWM을 확인할 수 없으면 그 팬의 RPM은 평활화하지 않습니다.
- PWM 변경, 시간 공백·역행, 누락된 행이 있으면 두 RPM 창을 초기화합니다. RPM 결측은 해당 팬의 창을 초기화합니다. 새 창의 첫 두 유효 값은 원래 값을 사용합니다.
- `HEAT_30S`에서 가열 상태가 모두 기록되고 가열이 한 번만 있는 경우, 가열 종료 후 처음 **양쪽 PWM이 0으로 전환된 행부터 기록 끝까지** `Fan_Section=정지·재가동 구간`으로 표시합니다. 이 규칙은 수집 프로토콜을 위한 구분이며 센서 고장이나 회복 완료 판정이 아닙니다. 다른 실험 종류나 여러 가열 주기에는 이 끝 구간 규칙을 자동 적용하지 않습니다.
- 정지·재가동 구간은 행을 전부 보존하고 RPM을 평활화하지 않습니다. 실제 감속·재가동과 측정된 0이 그대로 남습니다.
- 전달 CSV의 기존 RPM 칼럼에는 처리값, `_Raw` 칼럼에는 평활화 전 값을 저장합니다. 같은 행의 두 값을 비교할 수 있습니다.
- 개별 그래프는 전체 기록과 회색 음영의 끝 구간, RPM 원값과 처리값, 끝부분 확대를 제공합니다. 팬 조건 비교 그래프만 끝 구간을 제외합니다. `팬운전구간_요약.csv`에서 각 구간의 시작·끝·행 수를 확인할 수 있습니다.
- `Fan_Section`은 사후 분석용 구분입니다. 실시간 예측 모델의 입력 특성으로 사용하지 말고 분석 구간 선택에 사용하세요. 원본 기록을 미래까지 살펴 구분하므로 온라인 정보라고 가정하면 안 됩니다.

필터 창은 **3개 표본**이므로 측정 간격에 따라 시간 길이가 달라집니다. 급격한 실제 상승·하강도 완화되거나 지연될 수 있으므로, 반응 시점이나 최대값을 분석할 때는 전후 비교 그래프와 원본을 함께 확인해야 합니다.
단조롭게 상승·하강하는 구간에서는 중앙값이 직전 표본과 같아져 약 한 측정 간격의 지연이 생길 수 있습니다. 이는 일반적인 추세 비교에는 활용할 수 있지만, 최고 온도·순간 변화율·정확한 회복 시각을 보존하는 처리는 아닙니다. 이후 실시간 AI 입력에도 같은 인과적 필터를 적용해야 학습과 추론의 처리 방식이 일치합니다.

## 3. 결과 확인 방법

| 생성물 | 내용과 사용 방법 |
|---|---|
| `01_AI전달_CSV` | 분석·학습에 사용할 전처리 CSV |
| `02_데이터분석_요약` | 조건별 비교·전체 개별 그래프, 실험별 통계, `팬운전구간_요약.csv`의 구간별 시작·끝·행 수 |
| `03_처리기록` | 실험별 원본 연결, 입력 파일 경로·해시, 처리 설정, 변경·제외 내역, 전처리 전후 그래프, 발표용 평활화 예시 |
| `README_전처리과정.md` | 데이터 구성과 처리 기준 설명 |

### 실험별 요약의 칼럼 순서

`실험별_요약.csv`는 **실험 종류·팬 조건·측정 시간·행 수 → 센서별 통계 → 30초 가열 실험의 냉각 통계** 순서로 배치합니다. 추적용 ID·경로·실험 상태·경고는 `03_처리기록/01_실험목록과_원본파일연결.csv`에서 확인합니다.

| 왼쪽 칼럼 | 의미 |
|---|---|
| `Type` | BASE_OFF=무풍, FAN_MATRIX=팬 조합, HEAT_5S=5초 가열 후 회복, HEAT_30S=30초 가열 및 추가 냉각(연결된 경우) |
| `Intake_PWM`, `Exhaust_PWM` | 흡기·배기 PWM, 단위 % |
| `Measurement_Start`, `Measurement_End` | 해당 전달 CSV에 남은 첫 행과 마지막 행의 원본 측정 날짜·시각 |
| `Duration_s` | 마지막 측정 시각 − 첫 측정 시각, 단위 초. 실제 공백을 포함한 경과 시간 |
| `Rows` | 해당 전달 CSV의 데이터 행 수. 헤더 제외 |

행 수와 측정 시간은 다릅니다. 예를 들어 1초 간격으로 0~299초를 기록하면 300행이고 첫·마지막 시각 차이는 299초입니다. `heating_30s`는 연결한 추가 냉각까지 포함합니다. 날짜·시각이 없는 경과시간 전용 원본은 시작·종료 시각을 빈칸으로 남기고 경과 시간만 계산합니다.

다음 칼럼에는 온도·습도·PM2.5·흡기 RPM·배기 RPM의 First/Last/Median/Delta/MeanRate를 둡니다. 전처리된 전달 CSV의 전체 기록을 사용합니다. RPM 통계는 처리 RPM 기준이며 정지·재가동도 포함합니다. 조건별 비교 그래프와 통계의 범위가 다르므로 전체 평균을 고정 PWM 성능으로 해석하지 마세요.

| 통계 | 계산과 해석 |
|---|---|
| First / Last / Median | 첫 값 / 마지막 값 / 전체 표본 중앙값 |
| Delta | 마지막 값 − 첫 값. 음수는 감소, 양수는 증가 |
| MeanRate | Delta ÷ 실제 경과 시간(초) × 60. 칼럼명에 표시된 단위의 분당 평균 변화율 |

예를 들어 온도가 240초 동안 30→26°C로 변하면 Delta는 −4°C, MeanRate는 −1°C/min입니다. 순간 미분값이나 백분율 변화가 아닙니다. 습도 차이는 상대습도 수치의 차이(%RH), 변화율은 %RH/min입니다. 전체 구간의 증감이 상쇄될 수 있으므로 가열·냉각이 섞인 실험에서 이 값만으로 냉각 성능을 판단하지 않습니다. RPM 변화율에는 팬 시동에 따른 증가도 포함됩니다.

첫값 또는 마지막값이 결측이면 Delta와 MeanRate는 빈칸입니다. 한 표본만 있거나 시각이 역행·중복되어 분모를 신뢰할 수 없으면 평균 변화율을 빈칸으로 남깁니다. 실제 시간 공백은 시간 계산에 포함하며 보간하지 않습니다.

### 30초 가열 실험의 냉각 통계

`HEAT_30S`에는 한 번의 연속된 `Heat_Load=1` 구간 직후, 첫 `Heat_Load=0` 관측부터 최종 측정까지의 통계를 추가합니다. 냉각 시작은 추가 cooling 파일의 시작(약 300초)이 아니라 기록상 가열이 종료된 시점(약 60초)입니다. 가열 전환 경계의 행이 빠져 있거나 여러 가열 주기가 있으면 임의로 추정하지 않고 빈칸으로 남깁니다.

- `Cooling_Duration_s`: 기록상 가열 종료 후 첫 관측부터 마지막 관측까지의 시간
- `Temperature[degC]_Cooling_Delta`: 해당 구간의 마지막 온도 − 첫 온도
- `Temperature[degC/min]_Cooling_MeanRate`: 해당 온도 변화량 ÷ Cooling_Duration_s × 60. 음수는 평균적인 온도 감소

다른 실험 종류에는 이 세 값이 빈칸입니다. 냉각 지표도 시작 온도·측정 길이·외기 조건에 영향을 받으므로 팬 성능의 단독 순위나 회복 완료 정답으로 사용하지 않습니다. Heat_Load는 기록된 가열 상태이며 실제 열출력이나 수동 가열 종료 순간의 독립 측정값은 아닙니다.

### 원본 추적 및 품질 확인

`03_처리기록/01_실험목록과_원본파일연결.csv`는 요약과 같은 행 순서 및 실험 종류·팬 조건·시각을 포함하며 CSV 파일명·Recording_ID·원본 경로·실험 상태·경고를 보관합니다. `Missing_Count`는 전처리 후 남은 값의 결측 수이고, 원본에서 제외한 행 수는 `02_파일별_처리결과와_행수.csv`의 `Excluded_Rows`로 확인합니다.

`Main_Input_Rows`와 `Cooling_Input_Rows`는 각 원본의 입력 행 수입니다. `Source`와 `Cooling_Source`는 연결한 원본 경로, `Cooling_Start_s`는 추가 냉각 파일의 시작 경과 시간입니다. 본 측정과 추가 냉각의 상태는 각각 `Acquisition_Status`, `Cooling_Status`로 남깁니다. 실험 종료 시의 실패 표시는 자동으로 지우지 않습니다.
`02_파일별_처리결과와_행수.csv`의 `Input_Rows`, `Retained_Rows`, `Excluded_Rows`는 각각 해당 원본 파일의 입력·보존·제외 행 수입니다. 값이 평활화로 바뀐 행도 보존 행에 포함됩니다. 중복 파일·파일 읽기 실패는 별도의 `Status`로 확인합니다.

`03_측정값_변경과제외_상세.csv`에는 원본 파일, 헤더를 포함한 원본 행 번호, 칼럼, 처리 사유, 변경 전후 값이 기록됩니다. `trailing_median_3`는 온습도·PM 중앙값 변경, `rpm_trailing_median_3`는 RPM 중앙값 변경입니다. `missing_value`와 `invalid_range_or_sensor_flag`는 해당 칼럼만 빈칸으로 둔 사유입니다. 전후 비교 그래프의 주황색 표시는 처리 위치를 나타냅니다.

전달 자료에는 전처리 CSV와 검토 자료가 포함됩니다. 원본 CSV와 사진은 RAW 위치에 보존합니다. 실행할 때 기존 결과가 있으면 번호를 붙인 새 결과 폴더에 저장합니다.

## 4. 분석·학습 시 유의사항

학습·검증·시험은 원본 실험 단위로 나누세요. 한 실험의 시간 창이나 part 파일이 서로 다른 묶음에 들어가지 않도록 `03_처리기록/01_실험목록과_원본파일연결.csv`의 `CSV`와 `Recording_ID` 연결을 확인하세요.

실험 상태가 `failed`로 기록된 경우에는 `01_실험목록과_원본파일연결.csv`의 상태·경고와 RAW의 `status.json`을 확인한 뒤 사용 여부를 결정하세요. 수집 상태·이벤트·검증 파일은 결과 폴더에 복사하지 않습니다.

실험 종류·초기 온습도·주변 환경·가열 단계가 다른 자료를 비교할 때는 이러한 조건 차이를 함께 고려해야 합니다. 전처리 과정은 값의 형식과 기본 품질을 정리하는 단계이며, 실험의 물리적 타당성이나 센서 정확도까지 보장하지는 않습니다.

급격한 관측을 자동으로 오류로 간주하지 않는 원칙은 [NIST의 이상치 검토 안내](https://itl.nist.gov/div898/handbook/eda/section3/eda35h.htm)와 같습니다. 중앙값 필터의 3표본 크기와 온도 점검 범위는 이 프로젝트의 설정이며, 최적값으로 검증된 것은 아닙니다.
"""
    checked=[x for x in manifest if 'Input_Rows' in x]
    guide+='\n## 5. 이번 실행 확인\n\n'+connection+'\n\n'
    guide+=f"검사 완료 입력 {sum(x['Input_Rows'] for x in checked):,}행 / 보존 {sum(x['Retained_Rows'] for x in checked):,}행 / 제외 {sum(x['Excluded_Rows'] for x in checked):,}행. 보존 수는 검토용 파일이 있을 경우 해당 행도 포함합니다.\n\n"
    guide+='| 평활화 신호 | 변경 표본 수 | 변경된 값의 평균 절대 차이 | 최대 절대 차이 |\n|---|---:|---:|---:|\n'
    for signal in SENSOR:
        diffs=[abs(x['After']-x['Before']) for x in audit_all if x['Column']==signal and x['Reason'] in ('trailing_median_3','rpm_trailing_median_3')]
        guide+=f"| {signal} | {len(diffs):,} | {st.mean(diffs) if diffs else 0:.4g} | {max(diffs,default=0):.4g} |\n"
    guide+='\n위 차이는 평활화가 관측값을 얼마나 바꿨는지 보여주며 센서 오차나 이상치 개수를 의미하지 않습니다. 값의 단위는 표의 칼럼명과 같습니다.\n'
    (out/'README_전처리과정.md').write_text(guide,encoding='utf-8')
    links=''.join(f'<li><a href="{html.escape(p.name)}">{html.escape(p.stem)}</a></li>' for p in sorted((out/'02_데이터분석_요약').glob('*.html')))
    body=f'<p>원본 {len(manifest)}개 / 출력 CSV {len(runs)}개. 01·02·03 폴더와 README_전처리과정.md를 함께 전달하세요.</p><p>{connection}</p><p><a href="실험별_요약.csv">실험별 요약</a> · <a href="../03_처리기록/01_실험목록과_원본파일연결.csv">원본 연결·실험 상태</a></p><ul>{links}</ul><h2>확인 필요</h2>'
    body+='<p><a href="팬운전구간_요약.csv">팬 운전 구간별 시작·끝·행 수</a>. 비교 그래프는 정지·재가동 끝 구간을 제외하고, 전체 CSV와 개별 그래프는 모든 구간을 보존합니다.</p>'
    if review_summary:body+=f'<p>본 실험과 연결되지 않은 냉각 기록 {len(review_summary)}개는 03_처리기록/미연결냉각_검토용에서 확인하세요. AI 전달 CSV와 비교 그래프에는 포함하지 않았습니다.</p>'
    body+='<p>회복 정답은 미생성. 가열 종료 시각과 허용 범위가 필요합니다. 다른 날짜·초기조건 차이를 팬 효과로 해석하지 마세요.</p>'
    body+='<table><tr><th>실험 종류</th><th>흡기/배기 PWM</th><th>행 수</th><th>확인 사항</th></tr>'
    body+=''.join(f'<tr><td>{x["Type"]}</td><td>{x["Intake_PWM"]}/{x["Exhaust_PWM"]}</td><td>{x["Rows"]}</td><td>{html.escape(x["Warnings"])}</td></tr>' for x in summary)+'</table>'
    write_html(out/'02_데이터분석_요약/00_분석요약_여기부터.html','E4-AIRGUARD 데이터 분석 요약',body)
    print('저장 완료:',out);print('전처리 CSV:',len(runs),'개');print('입력 상태:',dict(Counter(x['Status'] for x in manifest)));print(connection)


if __name__=='__main__':main()
