"""Replay test sensor records through the real runtime without opening USB."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from unittest.mock import patch

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch

from main_runner import AirGuardRuntime, build_parser
from project_paths import TRAINING_CSV, SPLIT_CSV, MODELS_DIR, RESULTS_DIR
from sensor_buffer import SensorSample


class ReplayClock:
    def __init__(self, timestamp):
        self.now = timestamp
        self.origin = timestamp

    def wall(self):
        return self.now

    def monotonic(self):
        return self.now-self.origin


class ReplayRuntime(AirGuardRuntime):
    def __init__(self, args):
        self.trace = []
        super().__init__(args)

    def _apply(self, sample, result, reason):
        previous = (self.state.last_intake_percent, self.state.last_exhaust_percent)
        super()._apply(sample, result, reason)
        model_prediction = np.nan
        extra={}
        if self.buffer.is_ready():
            history = self.buffer.control_feature_matrix(
                sample.intake_pwm, sample.exhaust_pwm, include_raw_rates=True)
            if self.controller.context_feature_count==8:
                history=np.column_stack((history,history[:,0]))
            model_prediction = self.controller.forecast(history, sample.intake_pwm, sample.exhaust_pwm)
            if self.controller.context_feature_count==8:
                detail=self.controller.trajectory(history,sample.intake_pwm,sample.exhaust_pwm)
                extra=dict(predicted_1s_c=float(detail['temperatures'][0]),predicted_3s_c=float(detail['temperatures'][1]),
                           predicted_rate_c_per_s=detail['rate'],max_positive_rate_c_per_s=max(0.,detail['max_rate']),
                           risk_score=detail['risk_score'])
        self.trace.append(dict(
            timestamp=sample.timestamp, actual_temperature_c=sample.temperature_c,
            observed_pwm_prediction_c=model_prediction,
            selected_pwm_prediction_c=result.predicted_temp_c,
            predicted_target_timestamp=sample.timestamp+self.controller.horizon_seconds,
            recorded_intake_pwm=sample.intake_pwm, recorded_exhaust_pwm=sample.exhaust_pwm,
            command_intake_pwm=result.intake_pwm, command_exhaust_pwm=result.exhaust_pwm,
            pwm_changed=previous[0]>=0 and previous!=(result.intake_pwm,result.exhaust_pwm),
            startup_command=previous[0]<0, recovery_seconds=result.recovery_seconds,
            policy_mode=result.policy_mode, policy_limit_c=result.policy_limit_c,
            prediction_source=result.model_name, reason=reason,**extra))


class RecordedSamples:
    """Advance one control iteration per recorded sample, using recorded time."""
    def __init__(self, runtime, records, clock):
        self.runtime, self.records, self.clock = runtime, iter(records), clock
        self.stopped = False

    def wait(self, timeout):
        if self.stopped:
            return True
        try:
            record=next(self.records)
        except StopIteration:
            return True
        self.clock.now=record.timestamp
        self.runtime.receive_sample(SensorSample(
            timestamp=record.timestamp, temperature_c=record.Temp,
            intake_pwm=record.Intake_PWM, exhaust_pwm=record.Exhaust_PWM))
        return False

    def set(self):
        self.stopped=True


def attach_targets(trace, raw):
    times=raw.timestamp.to_numpy(float)
    targets=[]; held=[]
    for row in trace.itertuples():
        insertion=int(np.searchsorted(times,row.predicted_target_timestamp))
        choices=[i for i in (insertion-1,insertion) if 0<=i<len(raw) and times[i]>row.timestamp]
        index=min(choices,key=lambda i:abs(times[i]-row.predicted_target_timestamp)) if choices else None
        if index is None or abs(times[index]-row.predicted_target_timestamp)>.6:
            targets.append(np.nan);held.append(False);continue
        segment=raw[(raw.timestamp>=row.timestamp)&(raw.timestamp<=times[index])]
        valid=not segment.timestamp.diff().dropna().gt(2.5).any()
        targets.append(float(raw.iloc[index].Temp) if valid else np.nan)
        held.append(valid and segment.Intake_PWM.eq(row.recorded_intake_pwm).all()
                    and segment.Exhaust_PWM.eq(row.recorded_exhaust_pwm).all())
    trace['actual_at_target_c']=targets
    trace['recorded_pwm_held_to_target']=held
    return trace


def draw_panel(axes, part, trace, title):
    origin=float(part.timestamp.iloc[0])
    x=part.timestamp-origin
    axes[0].plot(x,part.Temp,color='#172b45',label='Actual temperature',linewidth=1.5)
    valid=trace.observed_pwm_prediction_c.notna() & trace.recorded_pwm_held_to_target & trace.actual_at_target_c.notna()
    axes[0].plot(trace.loc[valid,'predicted_target_timestamp']-origin,
                 trace.loc[valid,'observed_pwm_prediction_c'],color='#1679d3',label=f'{trace.model_label.iloc[0]} +5 s (recorded PWM)',linewidth=1.3)
    selected=trace.prediction_source.str.contains('LSTM',case=False,na=False)
    axes[0].plot(trace.loc[selected,'predicted_target_timestamp']-origin,
                 trace.loc[selected,'selected_pwm_prediction_c'],color='#e29828',linestyle=':',
                 label='Selected PWM forecast (unverified)',linewidth=1,alpha=.8)
    axes[0].axhline(26,color='#dd4c51',linestyle='--',linewidth=1,label='26 C limit')
    heat=part.Heat_Load.fillna(0).to_numpy(float)>0
    axes[0].fill_between(x,0,1,where=heat,transform=axes[0].get_xaxis_transform(),color='#ffc55c',alpha=.25,label='Recorded heating')
    axes[0].set_title(title,fontsize=10);axes[0].set_ylabel('Temperature (C)')
    for key,color,label in [('command_intake_pwm','#0e9f8a','Replay command intake'),('command_exhaust_pwm','#8a53b9','Replay command exhaust')]:
        axes[1].step(trace.timestamp-origin,trace[key],where='post',color=color,label=label,linewidth=1.3)
    axes[1].step(x,part.Intake_PWM,where='post',color='#888',linestyle=':',label='Recorded intake',linewidth=1)
    axes[1].step(x,part.Exhaust_PWM,where='post',color='#bbb',linestyle='--',label='Recorded exhaust',linewidth=1)
    changed=trace[trace.pwm_changed]
    axes[1].scatter(changed.timestamp-origin,changed.command_exhaust_pwm,s=14,color='#d9514e',zorder=5,label='Command change')
    axes[1].set_ylim(-5,110);axes[1].set_ylabel('PWM (%)');axes[1].set_xlabel('Elapsed recorded time (s)')
    for ax in axes:ax.grid(alpha=.2)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=RESULTS_DIR/'v4_control_replay')
    parser.add_argument('--min-hold-seconds',type=float,default=15.)
    parser.add_argument('--switch-confirmations',type=int,default=5)
    parser.add_argument('--stabilize-lstm',action='store_true')
    parser.add_argument('--raw-rise-assist',action='store_true')
    parser.add_argument('--model-file',type=Path,default=MODELS_DIR/'lstm_temperature_multirate_v4.pt')
    parser.add_argument('--control-policy',choices=('v4','v5','v6'),default='v4')
    parser.add_argument('--label',default='LSTM v4')
    parser.add_argument('--split',choices=('validation','test'),default='test')
    parser.add_argument('--rate-weight',type=float,default=None)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    torch.set_num_threads(1)
    data=pd.read_csv(TRAINING_CSV)
    data['timestamp']=pd.to_datetime(data.Time,utc=True,format='mixed').map(lambda t:t.timestamp())
    manifest=pd.read_csv(SPLIT_CSV)
    test=manifest[manifest.split==args.split].sort_values(['source_group','run'])
    frames=[];metrics=[];panels=[]
    for number,entry in enumerate(test.itertuples(),1):
        part=data[data.run==entry.run].sort_values('timestamp').reset_index(drop=True)
        clock=ReplayClock(float(part.timestamp.iloc[0]))
        options=build_parser().parse_args([
            '--controller-model','lstm','--lstm-model-file',str(args.model_file.resolve()),
            '--control-policy',args.control_policy,
            '--database',':memory:','--dummy','--dry-run',
            '--min-hold-seconds',str(args.min_hold_seconds),
            '--switch-confirmations',str(args.switch_confirmations)])
        options.stabilize_lstm=args.stabilize_lstm;options.raw_rise_assist=args.raw_rise_assist
        options.rate_weight=args.rate_weight
        output=io.StringIO()
        with redirect_stdout(output),patch('time.time',clock.wall),patch('time.monotonic',clock.monotonic):
            runtime=ReplayRuntime(options)
            runtime.stop_event=RecordedSamples(runtime,part.itertuples(),clock)
            try:runtime.run()
            finally:runtime.close()
        trace=attach_targets(pd.DataFrame(runtime.trace),part)
        trace.insert(0,'run',entry.run);trace['source_group']=entry.source_group
        trace['model_label']=args.label
        trace['elapsed_s']=trace.timestamp-float(part.timestamp.iloc[0])
        frames.append(trace)
        valid=trace.observed_pwm_prediction_c.notna() & trace.actual_at_target_c.notna() & trace.recorded_pwm_held_to_target
        actual=trace.loc[valid,'actual_at_target_c'];pred=trace.loc[valid,'observed_pwm_prediction_c']
        error=pred-actual
        metrics.append(dict(run=entry.run,source_group=entry.source_group,rows=len(part),
                            scored_predictions=int(valid.sum()),mae_c=float(error.abs().mean()),
                            rmse_c=float(np.sqrt((error**2).mean())),
                            false_positive=int(((pred>=26)&(actual<26)).sum()),
                            false_negative=int(((pred<26)&(actual>=26)).sum()),
                            pwm_changes=int(trace.pwm_changed.sum())))
        detail=(f'recorded median PWM {part.Intake_PWM.median():.0f}/{part.Exhaust_PWM.median():.0f}'
                if '::' in entry.run else entry.run)
        title=f'{entry.source_group} | {detail}'
        panels.append((part,trace,title))
        fig,axs=plt.subplots(2,1,figsize=(12,6),sharex=True,gridspec_kw={'height_ratios':[2,1]})
        draw_panel(axs,part,trace,title)
        axs[0].legend(fontsize=8,loc='best');axs[1].legend(fontsize=8,loc='best',ncol=3)
        fig.suptitle('Recorded-input replay, not closed-loop cooling validation',fontsize=10)
        fig.tight_layout();fig.savefig(args.output/f'run_{number:02d}.png',dpi=160);plt.close(fig)
        (args.output/f'run_{number:02d}_console.txt').write_text(output.getvalue(),encoding='utf-8')
        print(number,entry.source_group,'changes',metrics[-1]['pwm_changes'],flush=True)
    trace=pd.concat(frames,ignore_index=True)
    trace.to_csv(args.output/'prediction_and_pwm_trace.csv',index=False)
    trace[trace.pwm_changed|trace.startup_command].to_csv(args.output/'pwm_change_log.csv',index=False)
    pd.DataFrame(metrics).to_csv(args.output/'per_run_metrics.csv',index=False)
    fig=plt.figure(figsize=(19,15));grid=fig.add_gridspec(6,3,height_ratios=[2,1]*3,hspace=.55,wspace=.25)
    for i,(part,current,title) in enumerate(panels):
        row,col=divmod(i,3)
        a=fig.add_subplot(grid[row*2,col]);b=fig.add_subplot(grid[row*2+1,col],sharex=a)
        draw_panel((a,b),part,current,f'{args.split.capitalize()} {i+1}: {title}')
        a.tick_params(labelbottom=False)
        if i==0:a.legend(fontsize=6);b.legend(fontsize=6,ncol=2)
    fig.suptitle(f'{args.label}: +5 s forecasts aligned to target time and shadow PWM commands\nRecorded-input replay only; commands do not change recorded temperature',fontsize=14)
    fig.savefig(args.output/'test_prediction_and_pwm.png',dpi=170,bbox_inches='tight')
    fig.savefig(args.output/'test_prediction_and_pwm.pdf',bbox_inches='tight');plt.close(fig)
    valid=trace.observed_pwm_prediction_c.notna() & trace.actual_at_target_c.notna() & trace.recorded_pwm_held_to_target
    actual=trace.loc[valid,'actual_at_target_c'];pred=trace.loc[valid,'observed_pwm_prediction_c'];error=pred-actual
    summary=dict(split=args.split,evaluated_runs=len(test),samples=len(trace),scored_predictions=int(valid.sum()),
                 mae_c=float(error.abs().mean()),rmse_c=float(np.sqrt((error**2).mean())),
                 false_positive=int(((pred>=26)&(actual<26)).sum()),false_negative=int(((pred<26)&(actual>=26)).sum()),
                 pwm_changes=int(trace.pwm_changed.sum()),settings=vars(args).copy(),
                 model_sha256=runtime.controller.model_sha256,
                 warning='Recorded inputs including actual PWM are unchanged. Replay commands are not applied to the plant. No closed-loop energy or cooling claim.')
    summary['settings']['output']=str(args.output)
    summary['settings']['model_file']=str(args.model_file)
    (args.output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
