"""Train 1/3/5 s trajectories, future rise and upper-limit risk on fixed run splits."""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch import nn

from improve_lstm_stability import examples
from project_paths import TRAINING_CSV,SPLIT_CSV,RESULTS_DIR,MODELS_DIR
from trajectory_lstm_v6 import TrajectoryLSTM
from main_runner import LstmMpcController


def build_items(data):
    groups={r:g.sort_values('timestamp').reset_index(drop=True) for r,g in data.groupby('run',sort=False)}
    result=[]
    for item in examples(data,include_raw_rates=True):
        part=groups[item['run']];times=part.timestamp.to_numpy(float)
        targets=[]
        for horizon in (1,3,5):
            j=int(np.searchsorted(times,item['timestamp']+horizon))
            choices=[k for k in (j-1,j) if 0<=k<len(times) and times[k]>item['timestamp']]
            index=min(choices,key=lambda k:abs(times[k]-item['timestamp']-horizon)) if choices else None
            if index is None or abs(times[index]-item['timestamp']-horizon)>.6:break
            targets.append(float(part.Temp.iloc[index]))
        if len(targets)!=3:continue
        item=item.copy();item['trajectory_target']=targets
        future=part[(part.timestamp>item['timestamp'])&(part.timestamp<=times[index])]
        item['peak_label']=float(future.Temp.max()>=26)
        item['history']=np.column_stack((item['history'],item['history'][:,0]))
        result.append(item)
    return result


def arrays(items):
    windows=[]
    for i in items:
        context=i['history'].copy();context[:,0]-=context[-1,0]
        windows.append(np.column_stack((context,np.tile((i['intake'],i['exhaust']),(8,1)))))
    x=np.stack(windows).astype(np.float32)
    y=np.array([np.array(i['trajectory_target'])-i['temp'] for i in items],dtype=np.float32)
    label=np.array([i['peak_label'] for i in items],dtype=np.float32)
    return x,y,label


def predict(model,items,mean,std):
    x,_,_=arrays(items)
    with torch.inference_mode():
        delta,rate,logit=model.details(torch.tensor((x-mean)/std,dtype=torch.float32))
    return np.array([i['temp'] for i in items])[:,None]+delta.numpy(),rate.numpy(),torch.sigmoid(logit).numpy()


def metrics(items,prediction,scores):
    actual=np.array([i['trajectory_target'] for i in items]);result=dict(samples=len(items))
    for j,h in enumerate((1,3,5)):
        error=prediction[:,j]-actual[:,j]
        result[f'mae_{h}s']=float(np.abs(error).mean());result[f'rmse_{h}s']=float(np.sqrt((error**2).mean()))
    result['fp_5s']=int(((prediction[:,2]>=26)&(actual[:,2]<26)).sum())
    result['fn_5s']=int(((prediction[:,2]<26)&(actual[:,2]>=26)).sum())
    return result


def fit(train,valid,monotone_weight,epochs):
    torch.manual_seed(42)
    x,y,label=arrays(train);vx,vy,vl=arrays(valid)
    mean=x.mean((0,1));std=np.maximum(x.std((0,1)),1e-3)
    model=TrajectoryLSTM(mean,std)
    counts=pd.Series([i['run'] for i in train]).value_counts()
    weights=np.array([1/counts[i['run']] for i in train]);weights/=weights.mean()
    x=torch.tensor((x-mean)/std);y=torch.tensor(y);label=torch.tensor(label)
    vx=torch.tensor((vx-mean)/std);vy=torch.tensor(vy);vl=torch.tensor(vl)
    weights=torch.tensor(weights,dtype=torch.float32)
    pos_weight=float((1-label).sum()/label.sum().clamp_min(1))
    bce=nn.BCEWithLogitsLoss(pos_weight=torch.tensor(pos_weight),reduction='none')
    optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.001)
    best=None;best_value=float('inf');patience=0
    for epoch in range(epochs):
        model.train();order=torch.randperm(len(x))
        for start in range(0,len(x),128):
            ids=order[start:start+128];optimizer.zero_grad()
            delta,rate,logit=model.details(x[ids]);error=delta-y[ids]
            under=torch.where((y[ids]>.15)&(error<0),2.,1.)
            regression=((nn.functional.smooth_l1_loss(error,torch.zeros_like(error),beta=.2,reduction='none')+.1*error.square())*under).mean(1)
            true_rate=(y[ids,2]-y[ids,1])/2
            rate_loss=nn.functional.smooth_l1_loss(rate,true_rate,beta=.05,reduction='none')
            early=(label[ids]>0)&(torch.tensor([train[int(i)]['raw_temp'] for i in ids])<26)
            loss=((regression+.2*rate_loss+.05*bce(logit,label[ids])*torch.where(early,3.,1.))*weights[ids]).mean()
            if monotone_weight:
                weak=x[ids].clone();strong=x[ids].clone()
                for col in (8,9):
                    weak[:,:,col]=(35-float(mean[col]))/float(std[col])
                    strong[:,:,col]=(100-float(mean[col]))/float(std[col])
                low=model.details(weak)[0];high=model.details(strong)[0]
                loss+=monotone_weight*torch.relu(high-low).square().mean()
            loss.backward();nn.utils.clip_grad_norm_(model.parameters(),1.);optimizer.step()
        model.eval()
        with torch.inference_mode():
            delta,rate,logit=model.details(vx)
            value=float((delta-vy).abs().mean()+.05*nn.functional.binary_cross_entropy_with_logits(logit,vl))
        if value<best_value:
            best={k:v.clone() for k,v in model.state_dict().items()};best_value=value;patience=0
        else:
            patience+=1
            if patience>=12:break
    model.load_state_dict(best);model.eval()
    return model,mean,std,epoch+1


def select_threshold(labels,scores):
    normal=labels==0;positive=labels>0
    candidates=[]
    for t in np.r_[np.linspace(.05,.95,19),1.]:
        fp=int(((scores>=t)&normal).sum());fn=int(((scores<t)&positive).sum())
        if fp/max(int(normal.sum()),1)<=.05:candidates.append((fn,fp,-t,t))
    return min(candidates)[-1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=RESULTS_DIR/'lstm_v6')
    parser.add_argument('--epochs',type=int,default=100)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False);(args.output/'models').mkdir()
    torch.set_num_threads(1)
    data=pd.read_csv(TRAINING_CSV);data['timestamp']=pd.to_datetime(data.Time,utc=True,format='mixed').map(lambda t:t.timestamp())
    manifest=pd.read_csv(SPLIT_CSV);manifest.to_csv(args.output/'run_manifest.csv',index=False)
    if set(data.run)!=set(manifest.run) or manifest.run.duplicated().any():raise ValueError('Run split mismatch')
    items=build_items(data)
    groups={s:[i for i in items if i['run'] in set(manifest.loc[manifest.split==s,'run'])] for s in ('train','validation','test')}
    candidates=[]
    for weight in (0.,.2):
        model,mean,std,epochs=fit(groups['train'],groups['validation'],weight,args.epochs)
        pred,rate,scores=predict(model,groups['validation'],mean,std)
        m=metrics(groups['validation'],pred,scores)
        candidates.append(dict(model=model,mean=mean,std=std,weight=weight,epochs=epochs,metrics=m))
        print('validation',weight,m,flush=True)
    chosen=min(candidates,key=lambda c:(c['metrics']['fn_5s'],c['metrics']['fp_5s'],c['metrics']['mae_5s']))
    pred,rate,scores=predict(chosen['model'],groups['validation'],chosen['mean'],chosen['std'])
    actual=np.array([i['trajectory_target'] for i in groups['validation']])
    margin=np.maximum(0,np.quantile(actual-pred,.90,axis=0))
    labels=np.array([i['peak_label'] for i in groups['validation']]);threshold=select_threshold(labels,scores)
    artifact=dict(model_version=6,architecture='trajectory_relative_lstm',model_release='v6',
        state_dict=chosen['model'].state_dict(),feature_mean=chosen['mean'].tolist(),feature_std=chosen['std'].tolist(),
        feature_names=['relative_temperature_c','smoothed_rate','actual_intake_pwm','actual_exhaust_pwm',
                       'raw_rate_1s','raw_rate_3s','raw_rate_5s','absolute_temperature_c','planned_intake_pwm','planned_exhaust_pwm'],
        context_feature_count=8,sequence_length=8,horizon_seconds=5,horizons_seconds=[1,3,5],target_temp_c=26.,
        prediction_margin_c=margin.tolist(),risk_threshold=float(threshold),rate_weight=25.,control_policy='v6',
        monotone_weight=chosen['weight'],monotone_prior='Assumes ventilation cools rather than heats; not causal evidence.',
        training_csv_sha256=hashlib.sha256(TRAINING_CSV.read_bytes()).hexdigest(),split_csv_sha256=hashlib.sha256(SPLIT_CSV.read_bytes()).hexdigest())
    torch.save(artifact,args.output/'models/lstm_temperature_trajectory_v6.pt')
    # Only validation is reported now. The fixed configuration is tested after assistance selection.
    valid_items=groups['validation']
    exports=[]
    for i,p,r,s in zip(valid_items,pred,rate,scores):
        exports.append(dict(run=i['run'],timestamp=i['timestamp'],current_temperature_c=i['temp'],
            actual_1s=i['trajectory_target'][0],actual_3s=i['trajectory_target'][1],actual_5s=i['trajectory_target'][2],
            predicted_1s=float(p[0]),predicted_3s=float(p[1]),predicted_5s=float(p[2]),predicted_tail_rate=float(r),
            actual_peak_alarm=i['peak_label'],risk_score=float(s)))
    pd.DataFrame(exports).to_csv(args.output/'validation_predictions.csv',index=False)
    report=dict(candidates=[dict(monotone_weight=c['weight'],epochs=c['epochs'],validation=c['metrics']) for c in candidates],
        selected_monotone_weight=chosen['weight'],risk_threshold=threshold,prediction_margin_c=margin.tolist(),
        split_examples={k:len(v) for k,v in groups.items()},selection='Validation FN, FP, MAE; model/policy/assistance frozen before test replay.',
        limitation='Fixed-PWM observational data do not identify causal fan effects. Margin is an empirical 90th residual quantile, not a guaranteed bound.')
    (args.output/'training_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2),flush=True)


if __name__=='__main__':main()
