"""Compare paired full-runtime replays with raw-rise assistance off/on."""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from project_paths import TRAINING_CSV


def first_value(series):
    return float(series.iloc[0]) if len(series) else np.nan


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--off',type=Path,required=True)
    parser.add_argument('--on',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    data=pd.read_csv(TRAINING_CSV)
    data['timestamp']=pd.to_datetime(data.Time,utc=True,format='mixed').map(lambda t:t.timestamp())
    traces={name:pd.read_csv(folder/'prediction_and_pwm_trace.csv') for name,folder in [('off',args.off),('on',args.on)]}
    a,b=traces.values()
    assert a.run.equals(b.run) and np.array_equal(a.timestamp,b.timestamp)
    assert np.array_equal(a.actual_temperature_c,b.actual_temperature_c)
    assert np.allclose(a.observed_pwm_prediction_c,b.observed_pwm_prediction_c,equal_nan=True)
    metadata={name:json.loads((folder/'summary.json').read_text()) for name,folder in [('off',args.off),('on',args.on)]}
    assert metadata['off']['model_sha256']==metadata['on']['model_sha256']
    for key in ('control_policy','min_hold_seconds','switch_confirmations','stabilize_lstm'):
        assert metadata['off']['settings'][key]==metadata['on']['settings'][key]
    assert not metadata['off']['settings']['raw_rise_assist'] and metadata['on']['settings']['raw_rise_assist']
    events=[];normal=[];panels=[]
    for number,(run,part) in enumerate(a.groupby('run',sort=False),1):
        raw=data[data.run==run].sort_values('timestamp').reset_index(drop=True)
        heater=raw.Heat_Load.fillna(0)>0
        starts=np.flatnonzero(heater.to_numpy() & ~heater.shift(fill_value=False).to_numpy())
        for mode,trace in traces.items():
            g=trace[trace.run==run].copy()
            up=(g.command_intake_pwm.diff()>0)|(g.command_exhaust_pwm.diff()>0)
            g['fan_up']=up
            if not len(starts):
                normal.append(dict(run=run,mode=mode,pwm_changes=int(g.pwm_changed.sum()),
                                   fan_up_count=int(up.sum()),duration_s=float(g.timestamp.iloc[-1]-g.timestamp.iloc[0])))
                continue
            for event_index,start_index in enumerate(starts):
                start=float(raw.timestamp.iloc[start_index])
                end=float(raw.timestamp.iloc[starts[event_index+1]]) if event_index+1<len(starts) else float(raw.timestamp.iloc[-1])+1
                window=raw[(raw.timestamp>=start)&(raw.timestamp<end)]
                crossing=first_value(window.loc[window.Temp>=26,'timestamp'])
                before=raw[(raw.timestamp<start)&(raw.timestamp>=start-5)].Temp
                reference=float(before.median()) if len(before) else np.nan
                onset=np.nan
                for j in range(start_index,len(raw)-1):
                    if raw.timestamp.iloc[j]>=end:break
                    if raw.Temp.iloc[j]>=reference+.15 and raw.Temp.iloc[j+1]>=reference+.15:
                        onset=float(raw.timestamp.iloc[j]);break
                action=first_value(g.loc[(g.timestamp>=start)&(g.timestamp<end)&g.fan_up,'timestamp'])
                prior=g[g.timestamp<start]
                already_high=bool(len(prior) and (prior.command_intake_pwm.iloc[-1]>35 or prior.command_exhaust_pwm.iloc[-1]>35))
                assist=first_value(g.loc[(g.timestamp>=start)&(g.timestamp<end)&g.prediction_source.str.contains('Raw rise assist',na=False),'timestamp'])
                events.append(dict(run=run,mode=mode,event_index=event_index,source_group=g.source_group.iloc[0],
                    heat_start=start,rise_onset=onset,cross26_at=crossing,first_fan_up=action,
                    first_fan_up_after_heat_s=action-start,lead_before_cross26_s=crossing-action,
                    delay_after_rise_s=action-onset,prewarning=bool(np.isfinite(crossing) and np.isfinite(action) and action<crossing),
                    preexisting_high_command=already_high,first_assist_after_heat_s=assist-start,
                    pwm_changes=int(g.pwm_changed.sum())))
        if len(starts):panels.append((raw,traces['off'][traces['off'].run==run],traces['on'][traces['on'].run==run],float(raw.timestamp.iloc[starts[0]])))
    events=pd.DataFrame(events);normal=pd.DataFrame(normal)
    events.to_csv(args.output/'heating_event_comparison.csv',index=False)
    normal.to_csv(args.output/'normal_operation_comparison.csv',index=False)
    summaries=[]
    for mode,trace in traces.items():
        e=events[events['mode']==mode];n=normal[normal['mode']==mode]
        crosses=e[e.cross26_at.notna()]
        finite_lead=crosses.lead_before_cross26_s.dropna()
        summaries.append(dict(mode=mode,total_pwm_changes=int(trace.pwm_changed.sum()),
            normal_pwm_changes=int(n.pwm_changes.sum()),normal_fan_up_count=int(n.fan_up_count.sum()),
            normal_observed_minutes=float(n.duration_s.sum()/60),
            crossing_events=len(crosses),prewarning_success=int(crosses.prewarning.sum()),
            preexisting_high_count=int(crosses.preexisting_high_command.sum()),
            median_lead_s=float(finite_lead.median()),
            noncrossing_heated_events_with_up=int((e.cross26_at.isna()&e.first_fan_up.notna()).sum()),
            model_mae_c=metadata[mode]['mae_c'],model_fp=metadata[mode]['false_positive'],model_fn=metadata[mode]['false_negative']))
    pd.DataFrame(summaries).to_csv(args.output/'summary.csv',index=False)
    fig=plt.figure(figsize=(17,10));grid=fig.add_gridspec(4,3,height_ratios=[2,1]*2,hspace=.55,wspace=.28)
    for i,(raw,off,on,start) in enumerate(panels):
        row,col=divmod(i,3);ax=fig.add_subplot(grid[2*row,col]);p=fig.add_subplot(grid[2*row+1,col],sharex=ax)
        ax.plot(raw.timestamp-start,raw.Temp,color='#18324a',label='Recorded temperature')
        ax.axhline(26,color='#dc5656',linestyle='--',linewidth=1,label='26 C limit')
        ax.axvline(0,color='#777',linestyle=':',linewidth=1)
        for mode,color,label in [('off','#888','Assist OFF first fan up'),('on','#8555b8','Assist ON first fan up')]:
            event=events[(events.run==off.run.iloc[0])&(events['mode']==mode)].iloc[0]
            if np.isfinite(event.first_fan_up):ax.axvline(event.first_fan_up-start,color=color,linestyle='--',label=label,linewidth=1.2)
        ax.set_title(f'{off.source_group.iloc[0]} | recorded PWM median {raw.Intake_PWM.median():.0f}/{raw.Exhaust_PWM.median():.0f}',fontsize=10)
        ax.set_ylabel('Temperature (C)');ax.tick_params(labelbottom=False);ax.grid(alpha=.2)
        for trace,color,style,column,label in [(off,'#999','--','command_exhaust_pwm','OFF exhaust'),(off,'#bbb',':','command_intake_pwm','OFF intake'),(on,'#8555b8','-','command_exhaust_pwm','ON exhaust'),(on,'#00a08b','-','command_intake_pwm','ON intake')]:
            p.step(trace.timestamp-start,trace[column],where='post',color=color,linestyle=style,label=label,linewidth=1.3)
        p.set_xlim(-5,20);p.set_ylim(0,110);p.set_ylabel('PWM (%)');p.set_xlabel('Seconds after heat start');p.grid(alpha=.2)
        if i==0:ax.legend(fontsize=6);p.legend(fontsize=6,ncol=2)
    fig.suptitle('Raw-rise assistance under the new v5 control policy\nSame LSTM weights and sensor recordings; shadow commands do not change temperature',fontsize=13)
    fig.savefig(args.output/'onset_response_comparison.png',dpi=170,bbox_inches='tight')
    fig.savefig(args.output/'onset_response_comparison.pdf',bbox_inches='tight');plt.close(fig)
    definitions=dict(first_up='First increase of either commanded fan after recorded heat start; startup command excluded.',
                     lead='First recorded temperature >=26 C minus first new fan-up time. Positive means early; equality is not early.',
                     onset='First of two consecutive raw temperatures >= median(previous 5 seconds before heat) + 0.15 C.',
                     settings=metadata['off']['settings'],model_sha256=metadata['off']['model_sha256'],
                     limitation='Previously inspected development test; recorded-input full-runtime replay, not closed-loop cooling/energy validation.')
    (args.output/'definitions.json').write_text(json.dumps(definitions,ensure_ascii=False,indent=2),encoding='utf-8')
    print(pd.DataFrame(summaries).to_string(index=False),flush=True)
    print(events[['source_group','mode','first_fan_up_after_heat_s','lead_before_cross26_s','prewarning']].to_string(index=False),flush=True)


if __name__=='__main__':main()
