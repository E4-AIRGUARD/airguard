"""Portable helpers; no OpenFOAM or third-party imports."""
import csv
import gzip
import hashlib
import json
import math
import re
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
MODES = ('BOTH',)
LEVELS = ('I100_E100', 'I100_E60', 'I100_E35', 'I60_E100', 'I60_E60', 'I60_E35', 'I35_E100', 'I35_E60', 'I35_E35')
PHYSICS = ('BASE', 'SMOKE', 'HEAT')
PROBES = [(-.1,0,.08),(0,.07,.16),(.1,0,.24),(0,-.14,.16),(0,.14,.16)]

def require(ok, msg):
    if not ok: raise ValueError(msg)

def load(path): return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def dump(path, value):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    temp.replace(path)

def csv_write(path, rows):
    require(bool(rows),'No CSV rows')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    keys=list(dict.fromkeys(k for r in rows for k in r))
    tmp=path.with_suffix('.tmp')
    with tmp.open('w',newline='',encoding='utf-8-sig') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader();w.writerows(rows)
    tmp.replace(path)

def digest(obj): return hashlib.sha256(json.dumps(obj,sort_keys=True).encode()).hexdigest()

def active_pairs(c):
    """Only these combinations are generated/run; archived references stay external."""
    pairs=c.get('active_pairs',list(LEVELS))
    require(isinstance(pairs,list) and pairs and len(pairs)==len(set(pairs)), 'active_pairs must be a nonempty unique list')
    require(all(pair in LEVELS for pair in pairs), 'Unknown active pair')
    return tuple(pair for pair in LEVELS if pair in pairs)

def validate(c, need_velocity=True):
    require(set(c['levels'])==set(LEVELS),'Expected all nine Ixxx_Exxx combinations')
    active_pairs(c)
    require(re.fullmatch(r'V\d{2,}',c['revision']), 'revision must be V01, V02, ...')
    for key in ('duration_s','field_interval_s','rho0_kg_m3','mu_Pa_s','smoke_Sc','smoke_Sct','recovery_hold_s','low_speed_threshold_mps'):
        require(isinstance(c[key],(int,float)) and math.isfinite(c[key]) and c[key]>0, 'Invalid '+key)
    require(c['duration_s']>=c['field_interval_s'], 'Duration must include at least one saved step')
    require(abs(c['duration_s']/c['field_interval_s']-round(c['duration_s']/c['field_interval_s']))<1e-8,'Duration must be a multiple of field interval')
    require(-100<c['ambient_c']<100 and c['ambient_c']<c['heat_initial_c']<100,'Invalid temperatures')
    require(c['flow_model'] in ('laminar','sst'),'flow_model: laminar or sst')
    require(type(c['mesh_refinement']) is int and 1<=c['mesh_refinement']<=3,'mesh_refinement: 1, 2 or 3')
    require(c['initial_flow'] in ('baseline_final','rest'),'initial_flow: baseline_final or rest')
    require(len(c['heat_gravity_mps2'])==3 and all(math.isfinite(x) for x in c['heat_gravity_mps2']),'Invalid gravity')
    for k in LEVELS:
        u=c['levels'][k]['velocity_mps']
        if u is None:
            require(not need_velocity, f'{k}.velocity_mps is not set; no automatic PWM conversion')
        else: require(isinstance(u,(int,float)) and math.isfinite(u) and u>0, 'Invalid velocity '+k)
    for key, item in c['levels'].items():
        require(key == f"I{item['intake_pwm_percent']}_E{item['exhaust_pwm_percent']}", 'PWM/key mismatch')
        if item['velocity_mps'] is not None:
            require(item.get('basis') in ('measured','assumed'), 'Specify measured or assumed basis')
            require(bool(item.get('source_note','').strip()), 'Provide flow source_note')
    require(type(c['visualization']['fps']) is int and c['visualization']['fps']>0,'Invalid fps')
    capacity=c.get('effective_heat_capacity_J_kgK',718)
    require(isinstance(capacity,(int,float)) and math.isfinite(capacity) and capacity>0,'Invalid effective heat capacity')
    for key,value in c.get('quality_limits',{}).items():
        require(isinstance(value,(int,float)) and math.isfinite(value) and value>0,'Invalid quality limit '+key)


def assess_quality(rows, meta):
    """Project screening limits, not proof of physical accuracy or mesh convergence."""
    c=meta['config'];limits=c.get('quality_limits',{})
    tail=[r for r in rows if r['time_s']>=max(0,meta['end']-30)]
    failures=[]
    margin=limits.get('temperature_margin_C',.2)
    high=c['heat_initial_c'] if meta['physics']=='HEAT' else c['ambient_c']
    if min(r['T_min_C'] for r in rows)<c['ambient_c']-margin or max(r['T_max_C'] for r in rows)>high+margin:
        failures.append('temperature_outside_expected_range')
    if meta['physics']=='SMOKE':
        eps=limits.get('scalar_margin',1e-4)
        if min(r['C_min'] for r in rows)<-eps or max(r['C_max'] for r in rows)>1+eps:
            failures.append('tracer_outside_0_1')
    residuals=[abs(r['mass_balance_residual_kg_s'])/max(abs(r['mdot_inlet_signed_kg_s']),abs(r['mdot_outlet_signed_kg_s']),1e-12)
               for r in tail if r.get('mass_balance_residual_kg_s') is not None]
    mass=max(residuals,default=None)
    if mass is None or mass>limits.get('tail_mass_residual_fraction',.02):failures.append('tail_mass_balance_review')
    mean=sum(r['U_mean_mps'] for r in tail)/len(tail)
    variation=(max(r['U_mean_mps'] for r in tail)-min(r['U_mean_mps'] for r in tail))/max(mean,1e-12)
    imbalance=max((r['boundary_imbalance_fraction'] for r in tail if r.get('boundary_imbalance_fraction') is not None),default=None)
    base_fail=[]
    if meta['physics']=='BASE' and not meta['pilot']:
        if meta['end']<30:base_fail.append('baseline_less_than_30s')
        if variation>limits.get('baseline_tail_U_range_fraction',.05):base_fail.append('baseline_tail_not_stable')
        if imbalance is None or imbalance>limits.get('baseline_tail_flux_imbalance_fraction',.01):base_fail.append('baseline_flux_imbalance')
    return dict(numerical_screen='FAIL' if failures else 'PASS',numerical_failures=failures,
                baseline_ready=meta['physics']=='BASE' and not meta['pilot'] and not failures and not base_fail,
                baseline_failures=base_fail,tail_mass_residual_fraction=mass,
                tail_U_range_fraction=variation,tail_max_boundary_imbalance_fraction=imbalance)


def require_screen(summary, meta, baseline=False):
    require(summary.get('config_hash')==meta['config_hash'],'CSV config mismatch; run post again')
    require(summary.get('numerical_screen')=='PASS','Numerical screen failed or missing; inspect CSV summary before continuing')
    if baseline:require(summary.get('baseline_ready') is True,'Baseline not ready for recovery initialization; inspect tail metrics')


def scheduled_times(available, meta):
    """Exclude off-grid pause checkpoints without deleting or interpolating them."""
    interval=meta['config']['field_interval_s']
    ordered=sorted(float(t) for t in available)
    result=[]
    for i in range(round(meta['end']/interval)+1):
        target=i*interval
        matches=[t for t in ordered if abs(t-target)<1e-6]
        require(matches,f'Missing scheduled field {target:g}s')
        result.append(min(matches,key=lambda t:abs(t-target)))
    return result

def field_exists(folder, name): return (folder/name).is_file() or (folder/(name+'.gz')).is_file()

def field_text(folder, name):
    p=folder/name
    return p.read_text() if p.exists() else gzip.open(str(p)+'.gz','rt').read()

def fields_required(meta):
    return ['U','T','p','p_rgh']+(['k','omega','nut','alphat'] if meta['config']['flow_model']=='sst' else [])+(['C'] if meta['physics']=='SMOKE' else [])

def saved_times(case, meta):
    result=[]
    for p in Path(case).iterdir():
        try: t=float(p.name)
        except ValueError: continue
        if p.is_dir() and math.isfinite(t) and t>=0 and all(field_exists(p,n) for n in fields_required(meta)):
            result.append((t,p))
    result.sort()
    # Validate the newest restart candidate; all frames get numeric checks during post.
    # Avoid repeatedly decompressing the whole 300s dataset for a status query.
    while result:
        p=result[-1][1];valid=True
        try:
            for n in fields_required(meta):
                text=field_text(p,n)
                clean=re.sub(r'/\*.*?\*/|//[^\n]*','',text,flags=re.S).strip()
                if not ('FoamFile' in clean and 'internalField' in clean and 'boundaryField' in clean and clean.endswith('}') and clean.count('{')==clean.count('}')):
                    valid=False;break
        except (OSError,EOFError,UnicodeError):valid=False
        if valid:break
        result.pop()
    return result

def parse_log_time(line):
    """Accept Foundation time lines with or without a seconds suffix."""
    match=re.fullmatch(r'\s*Time\s*=\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(?:s)?\s*',line)
    if not match:return None
    value=float(match.group(1))
    return value if math.isfinite(value) else None


def verify(case):
    case=Path(case);m=load(case/'case.json');end=m['end'];iv=m['config']['field_interval_s']
    times=saved_times(case,m); ts=[t for t,p in times]
    require(ts and abs(ts[-1]-end)<1e-6,'Final complete field missing')
    for i in range(round(end/iv)+1):require(any(abs(t-i*iv)<1e-6 for t in ts), f'Missing scheduled field {i*iv:g}s')
    mesh=sorted(case.glob('log.checkMesh.*'))
    require(mesh and 'Mesh OK.' in mesh[-1].read_text(errors='replace'), 'checkMesh not passed')
    segments=sorted(case.glob('log.foamRun.*'))
    require(segments,'No solver log')
    s=segments[-1].read_text(errors='replace')
    require('FOAM FATAL' not in s and re.search(r'^End\s*$',s,re.M),'Latest solver log not normally finished')
    observed=[t for line in s.splitlines() if (t:=parse_log_time(line)) is not None]
    require(observed and observed[-1]>=end-1e-6,'Latest solver log did not reach target')
    return {'status':'SOLVER_COMPLETE','last_time_s':ts[-1],'saved_fields':len(ts),'numerical_quality':'pending_csv_review'}

def recovery(times, values, threshold, hold, interval):
    start=None;prev=None
    for t,v in zip(times,values):
        if v is None or not math.isfinite(v) or v>threshold:
            start=None
        else:
            if start is None or (prev is not None and t-prev>interval*1.5):start=t
            if t-start>=hold-1e-8:return start
        prev=t
    return None

def restart_time_name(times):
    """Preserve the exact directory name, including off-grid checkpoint precision."""
    require(bool(times), 'No complete restart field')
    return times[-1][1].name


def merge_function(case, name, audit=None):
    """Later restart segment wins. Never bridge missing scheduled samples.

    A killed solver may leave one incomplete trailing row. Ignore it only when
    its time is covered by a later restart segment. Interior damage still fails.
    The caller separately requires a flux sample at every exported saved time.
    """
    case=Path(case)
    out={}
    paths=list((case/'postProcessing'/name).glob('*/*.dat'))
    def key(p):
        try:return float(p.parent.name),p.name
        except ValueError:return -1,p.name
    paths=sorted(paths,key=key)
    for pi,p in enumerate(paths):
        lines=[(i,line) for i,line in enumerate(p.read_text(errors='strict').splitlines(),1)
               if line.strip() and not line.lstrip().startswith('#')]
        later_starts=[key(x)[0] for x in paths[pi+1:] if key(x)[0]>key(p)[0]]
        for ri,(lineno,line) in enumerate(lines):
            tokens=line.replace('(',' ').replace(')',' ').split()
            try:
                vals=[float(x) for x in tokens]
                require(len(vals)==2 and all(math.isfinite(v) for v in vals),'Invalid values')
            except ValueError as exc:
                try:row_time=float(tokens[0])
                except (ValueError,IndexError):row_time=float('nan')
                covered=any(start<=row_time+1e-7 for start in later_starts)
                if ri==len(lines)-1 and math.isfinite(row_time) and covered:
                    if audit is not None:
                        audit.append(dict(function=name,source=str(p.relative_to(case)),line=lineno,
                                          time_s=row_time,reason='trailing_row_superseded_by_restart'))
                    continue
                raise ValueError(f'Invalid function data {p}:{lineno}') from exc
            out[round(vals[0],7)]=(vals[1],str(p.relative_to(case)))
    return out
