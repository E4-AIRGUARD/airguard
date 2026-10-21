"""공통 run 분할에서 원본 다중 변화율·상승 가중 LSTM을 학습·평가합니다."""
import argparse
from project_paths import TRAINING_CSV, SPLIT_CSV, MODELS_DIR, RESULTS_DIR
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch
from torch import nn

from improve_lstm_stability import examples,score
from main_runner import LstmMpcController,PhysicsMpcController
from multirate_temperature_lstm import MultiRateTemperatureLSTM


def arrays(items):
    windows=[]
    for item in items:
        history=item['history'].copy(); history[:,0]-=history[-1,0]
        windows.append(np.column_stack((history,np.tile((item['intake'],item['exhaust']),(8,1)))))
    return np.stack(windows).astype(np.float32),np.array([i['target']-i['temp'] for i in items],dtype=np.float32)


def train(train_items,valid_items,rise_weight,epochs):
    torch.manual_seed(42)
    x,y=arrays(train_items); vx,vy=arrays(valid_items)
    mean=x.mean((0,1));std=np.maximum(x.std((0,1)),1e-3)
    model=MultiRateTemperatureLSTM(mean,std)
    counts=pd.Series([i['run'] for i in train_items]).value_counts()
    rising=np.array([i['history'][-1,5]>.03 or i['target']-i['temp']>.15 for i in train_items])
    weights=np.array([1/counts[i['run']] for i in train_items]) * np.where(rising,rise_weight,1)
    weights=weights/weights.mean()
    x=torch.tensor((x-mean)/std);y=torch.tensor(y);weights=torch.tensor(weights,dtype=torch.float32)
    vx=torch.tensor((vx-mean)/std);vy=torch.tensor(vy)
    optimizer=torch.optim.AdamW(model.parameters(),lr=.001,weight_decay=.001)
    best_loss=float('inf');best=None;patience=0
    for _ in range(epochs):
        model.train();order=torch.randperm(len(x))
        for start in range(0,len(x),128):
            ids=order[start:start+128];optimizer.zero_grad()
            error=model(x[ids])-y[ids]
            # 실제 상승을 낮게 예측한 오차에는 가중치를 더 줍니다.
            under=torch.where((y[ids]>.15)&(error<0),2.,1.)
            loss=((nn.functional.smooth_l1_loss(error,torch.zeros_like(error),beta=.2,reduction='none')+.1*error.square())*weights[ids]*under).mean()
            loss.backward();nn.utils.clip_grad_norm_(model.parameters(),1.);optimizer.step()
        model.eval()
        with torch.no_grad():
            error=model(vx)-vy
            value=float((error.abs()*torch.where(vy>.15,rise_weight,1.)).mean())
        if value<best_loss:
            best={k:v.clone() for k,v in model.state_dict().items()};best_loss=value;patience=0
        else:
            patience+=1
            if patience>=12: break
    model.load_state_dict(best);model.eval()
    return model,mean,std,int(rising.sum())


def predictions(model,items,mean,std):
    x,_=arrays(items)
    with torch.inference_mode():delta=model(torch.tensor((x-mean)/std)).numpy()
    return np.array([i['temp'] for i in items])+delta


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=RESULTS_DIR/'lstm_v4')
    parser.add_argument('--epochs',type=int,default=100)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False);(args.output/'models').mkdir()
    data=pd.read_csv(TRAINING_CSV)
    data['timestamp']=pd.to_datetime(data.Time,utc=True,format='mixed').map(lambda t:t.timestamp())
    manifest=pd.read_csv(SPLIT_CSV);manifest.to_csv(args.output/'run_manifest.csv',index=False)
    items=examples(data,include_raw_rates=True)
    groups={s:[i for i in items if i['run'] in set(manifest.loc[manifest.split==s,'run'])] for s in ('train','validation','test')}
    candidates=[]
    for weight in (1.,4.,8.):
        model,mean,std,nrise=train(groups['train'],groups['validation'],weight,args.epochs)
        p=predictions(model,groups['validation'],mean,std);m=score(groups['validation'],p)
        candidates.append(dict(weight=weight,model=model,mean=mean,std=std,metrics=m,rising_train_examples=nrise))
        print('validation',weight,m,flush=True)
    eligible=[c for c in candidates if c['metrics']['fp']/max(c['metrics']['actual_normal'],1)<=.05]
    selected=min(eligible or candidates,key=lambda c:(c['metrics']['fn'],c['metrics']['mae_c'],c['metrics']['fp']))
    artifact=dict(model_version=5,architecture='multirate_relative_lstm',state_dict=selected['model'].state_dict(),
                  feature_names=['relative_temperature_c','smoothed_rate_c_per_s','actual_intake_pwm','actual_exhaust_pwm',
                                 'raw_rate_1s','raw_rate_3s','raw_rate_5s','planned_intake_pwm','planned_exhaust_pwm'],
                  feature_mean=selected['mean'].tolist(),feature_std=selected['std'].tolist(),context_feature_count=7,
                  sequence_length=8,horizon_seconds=5,target_temp_c=26.,rise_loss_weight=selected['weight'],underprediction_weight=2.)
    torch.save(artifact,args.output/'models/lstm_temperature_multirate_v4.pt')
    rows=[];exports=[]
    baselines={
        'LSTM v2':LstmMpcController(MODELS_DIR/'lstm_temperature_model_v2.pt',26,35),
        'LSTM v3':LstmMpcController(MODELS_DIR/'lstm_temperature_stable_v3.pt',26,35),
        'Physics 5s':PhysicsMpcController(MODELS_DIR/'physics_mpc_model.pkl',26,5,35),
    }
    for split in ('validation','test'):
        current=groups[split]
        all_predictions={'LSTM multirate v4':predictions(selected['model'],current,selected['mean'],selected['std'])}
        for name,model in baselines.items():
            if name=='Physics 5s':
                values=[model.forecast(i['temp'],float(i['history'][-1,1]),i['intake'],i['exhaust'],5) for i in current]
            else:values=[model.forecast(i['history'][:,:4],i['intake'],i['exhaust']) for i in current]
            all_predictions[name]=np.array(values)
        for name,p in all_predictions.items():
            rows.append(dict(split=split,model=name,**score(current,p)))
            exports.extend(dict(split=split,model=name,run=i['run'],timestamp=i['timestamp'],actual=i['target'],predicted=float(v)) for i,v in zip(current,p))
    pd.DataFrame(rows).to_csv(args.output/'prediction_metrics.csv',index=False)
    pd.DataFrame(exports).to_csv(args.output/'predictions.csv',index=False)
    report=dict(horizon_seconds=5,sequence_length=8,selected_rise_weight=selected['weight'],underprediction_weight=2.,
                split_examples={k:len(v) for k,v in groups.items()},rising_train_examples=selected['rising_train_examples'],
                selection='Validation FN first, then MAE, among candidates with validation FPR <=5%. Test not used for selection.',
                candidates=[dict(rise_weight=c['weight'],validation=c['metrics']) for c in candidates],metrics=rows,
                limitations=['Existing test has been examined during development. New final physical trials remain necessary.',
                             'Raw-rate assistance is a separate rule, not a learned prediction. No energy savings are established.'])
    (args.output/'evaluation_report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    fig,axes=plt.subplots(1,3,figsize=(12,4))
    test=pd.DataFrame(rows);test=test[test.split=='test']
    for ax,key in zip(axes,('mae_c','fp','fn')):
        ax.bar(test.model,test[key]);ax.set_title('Common test '+key);ax.tick_params(axis='x',rotation=25,labelsize=8);ax.grid(axis='y',alpha=.2)
    fig.tight_layout();fig.savefig(args.output/'prediction_comparison.png',dpi=160);plt.close(fig)
    print(pd.DataFrame(rows).to_string(index=False))


if __name__=='__main__':main()
