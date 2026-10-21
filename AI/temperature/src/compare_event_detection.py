"""동일 test run에서 모델별 상승 감지·사전 경보·공통 팬 상향을 비교합니다."""
from __future__ import annotations

import json
import argparse
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from main_runner import LstmMpcController, PhysicsMpcController
from sensor_buffer import SensorBuffer, SensorSample
from raw_rise_assist import RawRiseAssist


def rising_edges(values):
    values=np.asarray(values,dtype=bool)
    return np.flatnonzero(values & ~np.r_[False,values[:-1]])


def confirmed(values,n):
    output=np.zeros(len(values),dtype=bool); count=0
    for i,value in enumerate(values):
        count=count+1 if value else 0
        output[i]=count>=n
    return output


def common_policy(temp,pred,times,assist=None):
    """예측 2회 경보, 26°C 실제 초과 즉시 상향, 25.5°C 3회 해제입니다."""
    state=False; on=off=0; last_change=-float('inf'); output=[]
    if assist is None: assist=np.zeros(len(temp),dtype=bool)
    for t,p,now,assisted in zip(temp,pred,times,assist):
        on=on+1 if np.isfinite(p) and p>=26 else 0
        off=off+1 if np.isfinite(p) and p<25.5 and t<25.5 else 0
        if not state and (t>=26 or on>=2 or assisted):
            state=True; last_change=now
        elif state and not assisted and off>=3 and now-last_change>=8:
            state=False; last_change=now
        output.append(state)
    return np.array(output)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    from project_paths import TRAINING_CSV, SPLIT_CSV, MODELS_DIR, RESULTS_DIR
    parser.add_argument('--output',type=Path,default=RESULTS_DIR/'event_evaluation')
    parser.add_argument('--include-v4',action='store_true')
    parser.add_argument('--model-file',type=Path,default=MODELS_DIR/'lstm_temperature_multirate_v4.pt')
    args=parser.parse_args()
    output=args.output
    output.mkdir(parents=True,exist_ok=False)
    manifest=pd.read_csv(SPLIT_CSV)
    manifest=manifest[manifest.split=='test'].sort_values(['source_group','run'])
    data=pd.read_csv(TRAINING_CSV)
    data['timestamp']=pd.to_datetime(data.Time,utc=True,format='mixed').map(lambda x:x.timestamp())
    models={
        'Physics 3s':PhysicsMpcController(MODELS_DIR/'physics_mpc_model.pkl',26,3,35),
        'Physics 5s':PhysicsMpcController(MODELS_DIR/'physics_mpc_model.pkl',26,5,35),
        'Physics 10s':PhysicsMpcController(MODELS_DIR/'physics_mpc_model.pkl',26,10,35),
        'LSTM original 10s':LstmMpcController(MODELS_DIR/'lstm_temperature_model.pt',26,35),
        'LSTM v2 5s':LstmMpcController(MODELS_DIR/'lstm_temperature_model_v2.pt',26,35),
        'LSTM v3 5s':LstmMpcController(MODELS_DIR/'lstm_temperature_stable_v3.pt',26,35),
        'Linear trend 5s':None,
        'Threshold':None,
    }
    if args.include_v4:
        new_model=LstmMpcController(args.model_file,26,35)
        models['LSTM v4 5s']=new_model
        models['LSTM v4 + raw assist']=new_model
    events=[]; normals=[]; traces=[]; timeline_panels=[]
    for case,row in enumerate(manifest.itertuples()):
        run=data[data.run==row.run].sort_values('timestamp').reset_index(drop=True)
        times=run.timestamp.to_numpy(float); temp=run.Temp.to_numpy(float)
        heater=run.Heat_Load.fillna(0).to_numpy(float)>0
        starts=rising_edges(heater)
        normal=row.source_group=='normal_fan'
        buffer=SensorBuffer(window_size=8); histories={}; multi_histories={}
        assist=RawRiseAssist();assist_flags=[]
        for index,sample in enumerate(run.itertuples()):
            if index and times[index]-times[index-1]>2.5:
                buffer=SensorBuffer(window_size=8)
            buffer.append(SensorSample(timestamp=sample.timestamp,temperature_c=sample.Temp,
                                       intake_pwm=sample.Intake_PWM,exhaust_pwm=sample.Exhaust_PWM))
            assist_flags.append(assist.update(buffer.snapshot()).active)
            if buffer.is_ready():
                histories[index]=buffer.control_feature_matrix(sample.Intake_PWM,sample.Exhaust_PWM)
                multi_histories[index]=buffer.control_feature_matrix(sample.Intake_PWM,sample.Exhaust_PWM,include_raw_rates=True)
        model_forecasts={}
        for name,model in models.items():
            horizon=getattr(model,'horizon_seconds',5)
            prediction=np.full(len(run),np.nan); current=np.full(len(run),np.nan)
            for index,history in histories.items():
                if getattr(model,'context_feature_count',4)==7:history=multi_histories[index]
                current[index]=history[-1,0]
                if name=='Threshold': prediction[index]=temp[index]
                elif name=='Linear trend 5s':
                    t=np.arange(8,dtype=float)-3.5
                    prediction[index]=history[-1,0]+5*np.dot(t,history[:,0])/np.dot(t,t)
                elif isinstance(model,PhysicsMpcController):
                    prediction[index]=model.forecast(float(history[-1,0]),float(history[-1,1]),
                                                     float(history[-1,2]),float(history[-1,3]),horizon)
                else: prediction[index]=model.forecast_current(history)
            alarms=confirmed(np.isfinite(prediction)&(prediction>=26),2)
            assisted=np.array(assist_flags) if name=='LSTM v4 + raw assist' else None
            if assisted is not None:alarms|=assisted
            pwm_high=common_policy(temp,prediction,times,assist=assisted)
            if name=='Threshold':
                alarms=np.isfinite(prediction)&(prediction>=26)
                # 공통 해제/유지는 같되 실제 초과에 대한 상향은 바로 수행합니다.
            rise_flags=confirmed(np.isfinite(prediction)&((prediction-current)/horizon>=.03),2)
            if assisted is not None:rise_flags|=assisted
            if name=='Threshold': rise_flags[:]=False
            alarm_edges=rising_edges(alarms); pwm_edges=rising_edges(pwm_high); rise_edges=rising_edges(rise_flags)
            model_forecasts[name]=prediction
            traces.extend(dict(run=row.run,model=name,timestamp=times[i],elapsed_s=times[i]-times[0],
                               actual_temp_c=temp[i],forecast_c=prediction[i],horizon_s=horizon,
                               heater_on=bool(heater[i]),alarm=bool(alarms[i]),rise_detected=bool(rise_flags[i]),
                               replay_pwm=100 if pwm_high[i] else 35) for i in range(len(run)))
            if normal:
                valid=np.arange(len(run))>=7
                normals.append(dict(run=row.run,model=name,observed_s=float(times[-1]-times[7]),
                                    upward_events=int(sum(i>=7 for i in pwm_edges)),
                                    alarm_events=int(sum(i>=7 for i in alarm_edges)),
                                    maximum_actual_c=float(temp.max())))
            for event_index,heat_index in enumerate(starts):
                end=int(starts[event_index+1]) if event_index+1<len(starts) else len(run)
                before=temp[max(0,heat_index-5):heat_index]
                baseline=float(np.median(before)) if len(before) else float(temp[heat_index])
                # 상승 시작은 사건 이후 baseline + 0.15°C가 2개 표본 유지된 첫 시각입니다.
                onset=None
                for j in range(heat_index,end-1):
                    if temp[j]>=baseline+.15 and temp[j+1]>=baseline+.15 and times[j+1]-times[j]<=2.5:
                        onset=j; break
                crossings=np.flatnonzero(temp[heat_index:end]>=26)+heat_index
                cross=int(crossings[0]) if len(crossings) else None
                already_over=bool(temp[heat_index]>=26)
                pre_alarm=bool(heat_index>0 and alarms[heat_index-1])
                pre_pwm=bool(heat_index>0 and pwm_high[heat_index-1])
                pre_rise=bool(heat_index>0 and rise_flags[heat_index-1])
                ae=[int(i) for i in alarm_edges if heat_index<=i<end]
                pe=[int(i) for i in pwm_edges if heat_index<=i<end]
                re=[int(i) for i in rise_edges if heat_index<=i<end]
                detected=re[0] if re else None
                # 가열 전부터 켜진 경보/팬을 새 가열의 감지로 인정하지 않습니다.
                valid_alarm=ae[0] if ae else None
                valid_pwm=pe[0] if pe else None
                detection_delay=None
                if onset is not None and detected is not None and not pre_rise:
                    detection_delay=float(times[detected]-times[onset])
                lead=None
                if cross is not None and valid_pwm is not None and not pre_pwm and not already_over:
                    lead=float(times[cross]-times[valid_pwm])
                success=bool(cross is not None and not already_over and not pre_alarm
                             and valid_alarm is not None and valid_alarm<cross)
                events.append(dict(run=row.run,source_group=row.source_group,model=name,horizon_s=horizon,
                                   event_index=event_index+1,baseline_c=baseline,
                                   heat_start_elapsed_s=float(times[heat_index]-times[0]),
                                   onset_elapsed_s=float(times[onset]-times[0]) if onset is not None else None,
                                   crossing_elapsed_s=float(times[cross]-times[0]) if cross is not None else None,
                                   crossed26=cross is not None,already_over=already_over,
                                   preexisting_alarm=pre_alarm,preexisting_pwm_high=pre_pwm,preexisting_rise_flag=pre_rise,
                                   rise_detection_delay_s=detection_delay,
                                   missed_rise_detection=detected is None,
                                   pre_onset_detection=detection_delay is not None and detection_delay<0,
                                   first_pwm_lead_s=lead,prewarning_success=success,
                                   alarm_after_heat_elapsed_s=float(times[valid_alarm]-times[heat_index]) if valid_alarm is not None else None))
        if not normal:
            timeline_panels.append((row.run,times-times[0],temp,model_forecasts,heater))
    event=pd.DataFrame(events); normal=pd.DataFrame(normals); summary=[]
    for name in models:
        e=event[event.model==name]; n=normal[normal.model==name]
        eligible=e[e.crossed26&~e.already_over]
        clean_rise=e[~e.preexisting_rise_flag&e.rise_detection_delay_s.notna()]
        after=clean_rise[clean_rise.rise_detection_delay_s>=0]
        lead=eligible.first_pwm_lead_s.dropna()
        summary.append(dict(model=name,heating_events=len(e),onset_defined_events=int(e.onset_elapsed_s.notna().sum()),
                            rise_delay_median_s=float(after.rise_detection_delay_s.median()) if len(after) else None,
                            rise_delay_observed_events=len(after),early_rise_events=int(e.pre_onset_detection.sum()),
                            no_rise_edge_events=int(e.missed_rise_detection.sum()),preexisting_rise_events=int(e.preexisting_rise_flag.sum()),
                            pwm_lead_median_s=float(lead.median()) if len(lead) else None,pwm_lead_available_events=len(lead),
                            preexisting_pwm_at_heat=int(eligible.preexisting_pwm_high.sum()),
                            prewarning_success=int(eligible.prewarning_success.sum()),prewarning_denominator=len(eligible),
                            prewarning_success_pct=float(eligible.prewarning_success.mean()*100) if len(eligible) else None,
                            preexisting_alarm_at_heat=int(eligible.preexisting_alarm.sum()),
                            no_heat_upward_events=int(n.upward_events.sum()),
                            no_heat_observed_minutes=float(n.observed_s.sum()/60)))
    summary=pd.DataFrame(summary)
    summary.to_csv(output/'model_summary.csv',index=False)
    event.to_csv(output/'heating_event_details.csv',index=False)
    normal.to_csv(output/'no_heat_details.csv',index=False)
    pd.DataFrame(traces).to_csv(output/'prediction_alarm_pwm_trace.csv',index=False)
    definition=dict(dataset='Existing test: 6 heating runs + 3 normal-fan runs. Not a new untouched final test.',
                    onset='First of two post-heat samples >= median(last 5 pre-heat temperatures)+0.15C.',
                    rise_detection='First new confirmed edge with (forecast-current_smoothed)/horizon >= 0.03 C/s for 2 samples after heat_on.',
                    warning='Forecast >=26C for 2 samples; Threshold uses current >=26C.',
                    pwm='COMMON comparator 35/35 to 100/100: confirmed forecast alarm OR actual >=26C; release after 3 samples below25.5C and hold>=8s. Not production MPC.',
                    prewarning='New alarm edge after heat_on strictly before first actual26C crossing, excluding preexisting alarm. Denominator: events crossing26 after starting below26.',
                    no_heat='Count common-policy PWM rising edges in normal_fan runs after warmup. Indicates nuisance upshifts; does not prove these were thermally unnecessary.',
                    raw_assist='Only LSTM v4 + raw assist: raw helper activates preventive alarm and bypasses forecast confirmation. Runtime minimum PWM is 35/60 or60/100; common replay records any upshift as binary35/100.',
                    limitation='Observed input replay: modeled PWM never changes the recorded temperature or recorded actual PWM. No closed-loop energy or safety comparison can be inferred.')
    (output/'definitions.json').write_text(json.dumps(definition,ensure_ascii=False,indent=2))
    fig,axes=plt.subplots(2,2,figsize=(14,8))
    for axis,column,title in zip(axes.flat,['rise_delay_median_s','pwm_lead_median_s','prewarning_success_pct','no_heat_upward_events'],
                                 ['Rise detection delay (s; valid detections only)','First PWM upshift lead (s; non-preexisting only)',
                                  'New prewarning success (%)','No-heat PWM upshift events']):
        axis.bar(summary.model,summary[column],color='#4478ad')
        axis.set_title(title);axis.tick_params(axis='x',rotation=35,labelsize=8);axis.grid(axis='y',alpha=.2)
        for k,v in enumerate(summary[column]):
            if pd.isna(v): axis.text(k,0,'N/A',ha='center',va='bottom',fontsize=8)
    fig.suptitle('Same test-input replay | Common alarm/PWM rules | Not physical closed-loop trials')
    fig.tight_layout();fig.savefig(output/'event_comparison.png',dpi=160);plt.close(fig)
    print(summary.to_string(index=False))


if __name__=='__main__': main()
