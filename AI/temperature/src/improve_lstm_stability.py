"""5초 LSTM의 전처리·기준점·정상 상태 편향 개선을 재현하는 평가 실험입니다."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
from torch import nn

from main_runner import LstmMpcController, PhysicsMpcController
from sensor_buffer import SensorBuffer, SensorSample
from stable_temperature_lstm import StableTemperatureLSTM, relative_window
from pwm_stability import PwmStabilityGuard


COMBOS = [(35,35),(35,60),(35,100),(60,60),(60,100),(100,100)]


def examples(frame, future_required=True, include_raw_rates=False):
    """현재 런타임과 같은 8행 버퍼/중앙값/시간 기반 변화율을 적용합니다."""
    output=[]
    for run,part in frame.groupby('run',sort=False):
        part=part.sort_values('timestamp').reset_index(drop=True)
        times=part.timestamp.to_numpy(float)
        buffer=SensorBuffer(window_size=8)
        for index,row in enumerate(part.itertuples()):
            if index and times[index]-times[index-1]>2.5:
                buffer=SensorBuffer(window_size=8)
            buffer.append(SensorSample(timestamp=row.timestamp,temperature_c=row.Temp,
                                       intake_pwm=row.Intake_PWM,exhaust_pwm=row.Exhaust_PWM))
            if not buffer.is_ready(): continue
            history=buffer.control_feature_matrix(row.Intake_PWM,row.Exhaust_PWM,include_raw_rates=include_raw_rates)
            insertion=int(np.searchsorted(times,row.timestamp+5))
            candidates=[j for j in (insertion-1,insertion) if index<j<len(times)]
            if not candidates: continue
            future=min(candidates,key=lambda j:abs(times[j]-row.timestamp-5))
            if future>=len(part) or abs(times[future]-row.timestamp-5)>.6: continue
            segment=part.iloc[index:future+1]
            if segment.timestamp.diff().dropna().gt(2.5).any(): continue
            held=bool(segment.Intake_PWM.eq(row.Intake_PWM).all() and segment.Exhaust_PWM.eq(row.Exhaust_PWM).all())
            if future_required and not held: continue
            output.append(dict(run=run,timestamp=row.timestamp,history=history,
                               temp=float(history[-1,0]),raw_temp=row.Temp,
                               intake=row.Intake_PWM,exhaust=row.Exhaust_PWM,
                               target=float(part.Temp.iloc[future]),held=held))
    return output


def score(items,pred):
    a=np.array([item['target'] for item in items]); pred=np.asarray(pred)
    return dict(samples=len(items),mae_c=float(np.abs(pred-a).mean()),
                rmse_c=float(np.sqrt(np.mean((pred-a)**2))),bias_c=float((pred-a).mean()),
                fp=int(((pred>=26)&(a<26)).sum()),fn=int(((pred<26)&(a>=26)).sum()),
                actual_over26=int((a>=26).sum()),actual_normal=int((a<26).sum()))


def tensors(items):
    x=np.stack([relative_window(i['history'],i['intake'],i['exhaust']) for i in items])
    y=np.array([i['target']-i['temp'] for i in items],dtype=np.float32)
    return x,y


def fit(train,valid,gated,seed,epochs):
    torch.manual_seed(seed)
    x,y=tensors(train); vx,vy=tensors(valid)
    rng=np.random.default_rng(seed)
    # run을 분리한 뒤 학습 입력에만 작은 측정 잡음을 추가합니다.
    augmented=x.copy(); augmented[:,:,0]+=rng.normal(0,.01,augmented[:,:,0].shape)
    augmented[:,:,0]-=augmented[:,-1:,0]
    x=np.concatenate([x,augmented]); y=np.tile(y,2)
    mean=x.mean((0,1)); std=np.maximum(x.std((0,1)),1e-3)
    model=StableTemperatureLSTM(mean,std,gated=gated)
    x=torch.tensor((x-mean)/std); y=torch.tensor(y)
    vx=torch.tensor((vx-mean)/std); vy=torch.tensor(vy)
    counts=pd.Series([i['run'] for i in train]*2).value_counts()
    weights=torch.tensor([1/counts[i['run']] for i in train]*2,dtype=torch.float32)
    weights/=weights.mean()
    optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.001)
    best=None; best_loss=float('inf'); patience=0
    for epoch in range(epochs):
        model.train()
        order=torch.randperm(len(x))
        for start in range(0,len(x),128):
            ids=order[start:start+128]; optimizer.zero_grad()
            error=model(x[ids])-y[ids]
            loss=((nn.functional.smooth_l1_loss(error,torch.zeros_like(error),beta=.2,reduction='none')+.1*error.square())*weights[ids]).mean()
            loss.backward(); nn.utils.clip_grad_norm_(model.parameters(),1.); optimizer.step()
        model.eval()
        with torch.no_grad(): current=float((model(vx)-vy).abs().mean())
        if current<best_loss:
            best_loss=current; best={k:v.clone() for k,v in model.state_dict().items()}; patience=0
        else:
            patience+=1
            if patience>=12: break
    model.load_state_dict(best); model.eval()
    return model,mean,std


def predict(model,items,mean,std):
    x,_=tensors(items)
    with torch.inference_mode(): delta=model(torch.tensor((x-mean)/std)).numpy()
    return np.array([i['temp'] for i in items])+delta


def model_predictions(name,items,improved=None):
    if name=='persistence': return np.array([i['temp'] for i in items])
    if name=='linear_trend': return np.array([i['temp']+5*(i['history'][-1,0]-i['history'][0,0])/7 for i in items])
    if name=='physics5':
        from project_paths import MODELS_DIR
        model=PhysicsMpcController(MODELS_DIR/'physics_mpc_model.pkl',26,5,35)
        return np.array([model.forecast(i['temp'],float(i['history'][-1,1]),i['intake'],i['exhaust'],5) for i in items])
    if name=='lstm_v2':
        from project_paths import MODELS_DIR
        model=LstmMpcController(MODELS_DIR/'lstm_temperature_model_v2.pt',26,35)
        return np.array([model.forecast(i['history'],i['intake'],i['exhaust']) for i in items])
    return predict(*improved[:1],items,*improved[1:])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    from project_paths import RESULTS_DIR, MODELS_DIR
    parser.add_argument('--output',type=Path,default=RESULTS_DIR/'lstm_v3')
    parser.add_argument('--database',type=Path,required=True,help='분석할 실제 시험 SQLite DB')
    parser.add_argument('--epochs',type=int,default=100)
    args=parser.parse_args(); args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'models').mkdir()
    from project_paths import TRAINING_CSV, SPLIT_CSV
    data=pd.read_csv(TRAINING_CSV)
    # datetime 내부 정밀도와 무관하게 초 단위 Unix 시각을 만듭니다.
    data['timestamp']=pd.to_datetime(data.Time,utc=True,format='mixed').map(lambda t:t.timestamp())
    manifest=pd.read_csv(SPLIT_CSV)
    manifest.to_csv(args.output/'run_manifest.csv',index=False)
    all_items=examples(data)
    groups={split:[i for i in all_items if i['run'] in set(manifest.loc[manifest.split==split,'run'])] for split in ['train','validation','test']}
    candidates=[]
    for gated in (False,True):
        for seed in (42,2026):
            model,mean,std=fit(groups['train'],groups['validation'],gated,seed,args.epochs)
            values=predict(model,groups['validation'],mean,std)
            metrics=score(groups['validation'],values)
            candidates.append(dict(gated=gated,seed=seed,model=model,mean=mean,std=std,validation=metrics))
            print('candidate',gated,seed,metrics,flush=True)
    selected=min(candidates,key=lambda i:(i['validation']['fn'],i['validation']['mae_c'],i['validation']['fp']))
    improved=(selected['model'],selected['mean'],selected['std'])
    artifact=dict(model_version=4,architecture='stable_relative_lstm',state_dict=selected['model'].state_dict(),
                  feature_names=['relative_temperature_c','rate_c_per_s','actual_intake_pwm','actual_exhaust_pwm','planned_intake_pwm','planned_exhaust_pwm'],
                  feature_mean=selected['mean'].tolist(),feature_std=selected['std'].tolist(),context_feature_count=4,
                  sequence_length=8,horizon_seconds=5,target_temp_c=26,gated=selected['gated'],training_seed=selected['seed'])
    path=args.output/'models/lstm_temperature_stable_v3.pt'; torch.save(artifact,path)
    names=['persistence','linear_trend','physics5','lstm_v2','stable_v3']
    rows=[]; test_predictions=[]
    for name in names:
        values=model_predictions(name,groups['test'],improved)
        rows.append(dict(dataset='existing_holdout',model=name,**score(groups['test'],values)))
        test_predictions.extend(dict(run=i['run'],timestamp=i['timestamp'],actual=i['target'],predicted=float(p),model=name) for i,p in zip(groups['test'],values))
    with sqlite3.connect(f'file:{args.database}?mode=ro',uri=True) as connection:
        db=pd.read_sql_query('select * from closed_loop_trial_samples order by timestamp',connection)
    db['run']=db.trial_id+'__'+db.groupby('trial_id').timestamp.diff().gt(2.5).groupby(db.trial_id).cumsum().astype(str)
    db['Temp']=db.temperature_c; db['Intake_PWM']=db.feedback_intake_pwm; db['Exhaust_PWM']=db.feedback_exhaust_pwm
    items=examples(db,future_required=False)
    diagnostic=[i for i in items if i['run'].startswith(('lstm_r2','lstm_r3','lstm_v2'))]
    for dataset,part in [('db_lstm_diagnostic_all',diagnostic),('db_lstm_diagnostic_pwm_held',[i for i in diagnostic if i['held']]),('db_all_diagnostic',items)]:
        for name in names:
            values=model_predictions(name,part,improved)
            rows.append(dict(dataset=dataset,model=name,**score(part,values)))
            if dataset=='db_lstm_diagnostic_all':
                test_predictions.extend(dict(run=i['run'],timestamp=i['timestamp'],actual=i['target'],predicted=float(p),model=name) for i,p in zip(part,values))
    pd.DataFrame(rows).to_csv(args.output/'prediction_metrics.csv',index=False)
    pd.DataFrame(test_predictions).to_csv(args.output/'predictions.csv',index=False)
    # Same observed input and candidate set: measures decision stability, not a physical counterfactual.
    replay=[]
    legacy=LstmMpcController(MODELS_DIR/'lstm_temperature_model_v2.pt',26,35)
    for run in sorted(set(i['run'] for i in diagnostic)):
        part=[i for i in diagnostic if i['run']==run]
        for name in ['lstm_v2','stable_v3']:
            decisions=[]
            guarded=[]; guard=PwmStabilityGuard(); previous=(35,35)
            for item in part:
                if name=='lstm_v2':
                    preds=np.array([legacy.forecast(item['history'],a,b) for a,b in COMBOS])
                    current=legacy.forecast(item['history'],item['intake'],item['exhaust'])
                else:
                    batch=[dict(item,intake=a,exhaust=b) for a,b in COMBOS]
                    preds=predict(*improved[:1],batch,*improved[1:])
                    current=float(predict(*improved[:1],[item],*improved[1:])[0])
                risk=max(item['temp'],current)
                allowed=[j for j,pair in enumerate(COMBOS) if (risk<25 or (risk<25.5 and pair!=(35,35)) or pair[1]==100)]
                limit=25 if risk<25 else 25.5 if risk<25.5 else 26
                safe=[j for j in allowed if preds[j]<=limit]
                choice=safe[0] if safe else allowed[-1]
                decisions.append(COMBOS[choice])
                current_forecast=float(preds[COMBOS.index(previous)])
                previous,_=guard.decide(previous,COMBOS[choice],item['raw_temp'],float(item['history'][-1,1]),current_forecast,item['timestamp'])
                guarded.append(previous)
            arr=np.array(decisions)
            filtered=np.array(guarded)
            replay.append(dict(run=run,model=name,samples=len(arr),raw_choice_changes=int(np.any(arr[1:]!=arr[:-1],axis=1).sum()),
                               guarded_choice_changes=int(np.any(filtered[1:]!=filtered[:-1],axis=1).sum()),
                               above_min_choice_pct=float(np.any(arr>35,axis=1).mean()*100)))
    pd.DataFrame(replay).to_csv(args.output/'candidate_decision_replay.csv',index=False)
    metrics=pd.DataFrame(rows)
    fig,axes=plt.subplots(1,3,figsize=(13,4))
    for axis,metric in zip(axes,['mae_c','fp','fn']):
        part=metrics[metrics.dataset=='existing_holdout']
        axis.bar(part.model,part[metric]); axis.set_title('Held-out '+metric); axis.tick_params(axis='x',rotation=30);axis.grid(axis='y',alpha=.2)
    fig.tight_layout(); fig.savefig(args.output/'holdout_comparison.png',dpi=160);plt.close(fig)
    report=dict(selected=dict(gated=selected['gated'],seed=selected['seed'],validation=selected['validation']),
                candidates=[{k:v for k,v in c.items() if k in ['gated','seed','validation']} for c in candidates],
                split_examples={k:len(v) for k,v in groups.items()},metrics=rows,candidate_decision_replay=replay,
                model_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                limitation='Existing holdout has been examined in earlier work. DB guided improvement and is diagnostic validation, not an untouched final test. Offline candidate choices do not establish closed-loop energy savings.')
    (args.output/'evaluation_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(metrics.to_string(index=False)); print(pd.DataFrame(replay).to_string(index=False))


if __name__=='__main__': main()
