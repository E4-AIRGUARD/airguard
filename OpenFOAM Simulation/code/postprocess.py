"""Run with pvpython. Rebuild CSV from saved fields; never synthesize missing frames."""
import argparse
import math
from pathlib import Path
from common import load,dump,csv_write,verify,require,merge_function,recovery,assess_quality,scheduled_times

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('case',type=Path);p.add_argument('--root',type=Path,required=True);a=p.parse_args()
    case=a.case.resolve();root=a.root.resolve();verify(case);m=load(case/'case.json');c=m['config']
    from paraview.simple import OpenFOAMReader, MergeBlocks
    from paraview import servermanager
    import vtk
    import numpy as np
    from vtk.util.numpy_support import vtk_to_numpy
    from foam_reader import open_volume
    reader=open_volume(case/'case.foam', ['T','U']+(['C'] if m['physics']=='SMOKE' else []))
    merged=MergeBlocks(Input=reader)
    times=scheduled_times(reader.TimestepValues,m)
    require(times and times[0]==0 and abs(times[-1]-m['end'])<1e-6,'ParaView missing initial/final time')
    restart_audit=[]
    flux={k:merge_function(case,k+'Flux',restart_audit) for k in ('inlet','outlet')}
    probepts=vtk.vtkPoints()
    for xyz in m['probes']:probepts.InsertNextPoint(*xyz)
    probeinput=vtk.vtkPolyData();probeinput.SetPoints(probepts)
    rows=[];volume=None
    for ti,t in enumerate(times):
        merged.UpdatePipeline(time=t);data=servermanager.Fetch(merged)
        require(data.IsA('vtkUnstructuredGrid'),'Expected merged internal volume mesh')
        size=vtk.vtkCellSizeFilter();size.SetInputData(data);size.ComputeVolumeOn();size.Update()
        v=vtk_to_numpy(size.GetOutput().GetCellData().GetArray('Volume'))
        require(len(v)>0 and np.isfinite(v).all() and (v>0).all(),'Invalid cell volumes')
        volume=float(v.sum());w=v/volume
        def arr(name):
            obj=data.GetCellData().GetArray(name);require(obj is not None,'Missing '+name)
            values=vtk_to_numpy(obj);require(np.isfinite(values).all(),'Nonfinite '+name);return values
        T=arr('T')-273.15;speed=np.linalg.norm(arr('U'),axis=1)
        rho=c['rho0_kg_m3']*(1-(T-c['ambient_c'])/(c['ambient_c']+273.15))
        require((rho>0).all(),'Invalid density')
        tm=float(np.dot(w,T))
        row=dict(case_id=m['case_id'],mode=m.get('mode','BOTH'),time_s=t,physics=m['physics'],condition_id=m['condition_id'],source_type='CFD',flow_basis=m['flow_basis'],geometry_version=c['geometry_version'],velocity_assumed_mps=m['velocity'],Q_assumed_m3_s=m['velocity']*m['patch_area_m2'],intake_pwm_percent=m.get('intake_pwm_percent',c['levels'][m['condition_id']]['intake_pwm_percent']),exhaust_pwm_percent=m.get('exhaust_pwm_percent',c['levels'][m['condition_id']]['exhaust_pwm_percent']),T_mean_C=tm,T_min_C=float(T.min()),T_max_C=float(T.max()),T_std_C=float(np.sqrt(np.dot(w,(T-tm)**2))),U_mean_mps=float(np.dot(w,speed)),U_max_mps=float(speed.max()),low_speed_volume_fraction=float(w[speed<c['low_speed_threshold_mps']].sum()),domain_mass_kg=float(np.dot(v,rho)),volume_m3=volume)
        cp=vtk.vtkCellDataToPointData();cp.SetInputData(data);cp.Update()
        probe=vtk.vtkProbeFilter();probe.SetInputData(probeinput);probe.SetSourceData(cp.GetOutput());probe.Update();pd=probe.GetOutput().GetPointData()
        mask=pd.GetArray('vtkValidPointMask');require(mask is not None and all(mask.GetTuple1(i)>0 for i in range(5)),'Virtual probe outside valid mesh')
        for i in range(5):
            row[f'T{i+1}_C']=pd.GetArray('T').GetTuple1(i)-273.15
            row[f'U{i+1}_mps']=math.sqrt(sum(x*x for x in pd.GetArray('U').GetTuple(i)))
        for k in ('inlet','outlet'):
            val=flux[k].get(round(t,7))
            if t>0:require(val is not None,f'Missing {k} phi at saved time {t}; no interpolation allowed')
            row[f'mdot_{k}_signed_kg_s']=val[0] if val else None
            row[f'mdot_{k}_source']=val[1] if val else ''
        row['heat_residual_ratio']=(tm-c['ambient_c'])/(c['heat_initial_c']-c['ambient_c']) if m['physics']=='HEAT' else None
        if m['physics']=='SMOKE':
            C=arr('C');row.update(C_mean=float(np.dot(w,C)),C_min=float(C.min()),C_max=float(C.max()),tracer_mass_proxy_kg=float(np.dot(v*rho,C)))
            for i in range(5):row[f'C{i+1}']=pd.GetArray('C').GetTuple1(i)
        row['equivalent_flow_group']=m.get('equivalent_flow_group')
        row['flow_source']=m.get('flow_source')
        row['fans_individually_modeled']=False
        rows.append(row)
        if ti%20==0:print(f'CSV: {ti+1}/{len(times)}, t={t:g}',flush=True)
    for i,r in enumerate(rows):
        mi,mo=r['mdot_inlet_signed_kg_s'],r['mdot_outlet_signed_kg_s']
        r['net_boundary_mdot_kg_s']=mi+mo if mi is not None and mo is not None else None
        r['boundary_imbalance_fraction']=abs(mi+mo)/max(abs(mi),abs(mo),1e-12) if mi is not None and mo is not None else None
        r['mass_balance_residual_kg_s']=None
        if i and r['net_boundary_mdot_kg_s'] is not None:
            r['mass_balance_residual_kg_s']=(r['domain_mass_kg']-rows[i-1]['domain_mass_kg'])/(r['time_s']-rows[i-1]['time_s'])+r['net_boundary_mdot_kg_s']
        if m['physics']=='SMOKE':r['smoke_residual_ratio']=r['C_mean']/rows[0]['C_mean']
    summary={'case_id':m['case_id'],'final_time_s':times[-1],'T_final_mean_C':rows[-1]['T_mean_C'],'U_final_mean_mps':rows[-1]['U_mean_mps'],'low_speed_final_fraction':rows[-1]['low_speed_volume_fraction'],'volume_m3':volume,'quality':'REVIEW_REQUIRED','solver_complete':True,'physical_validation':'not_performed'}
    ratio='heat_residual_ratio' if m['physics']=='HEAT' else 'smoke_residual_ratio' if m['physics']=='SMOKE' else None
    for pct in (90,95):
        rt=recovery(times,[r[ratio] for r in rows],1-pct/100,c['recovery_hold_s'],c['field_interval_s']) if ratio else None
        summary[f't{pct}_s']=rt;summary[f't{pct}_reached']=rt is not None
    tail=[r for r in rows if r['time_s']>=max(0,m['end']-30)]
    mean_u=sum(r['U_mean_mps'] for r in tail)/len(tail)
    summary['tail_U_range_fraction']=(max(r['U_mean_mps'] for r in tail)-min(r['U_mean_mps'] for r in tail))/max(mean_u,1e-12)
    summary['tail_max_boundary_imbalance_fraction']=max((r['boundary_imbalance_fraction'] for r in tail if r['boundary_imbalance_fraction'] is not None),default=None)
    summary['mass_balance_note']='Storage derivative estimated at saved time interval; boundary imbalance alone is not a transient conservation test.'
    if ratio=='smoke_residual_ratio':summary.update(C_final_mean=rows[-1]['C_mean'],C_global_min=min(r['C_min'] for r in rows),C_global_max=max(r['C_max'] for r in rows))
    summary['flow_basis']=m['flow_basis']
    summary['flow_source']=m.get('flow_source')
    summary['equivalent_flow_group']=m.get('equivalent_flow_group')
    summary['intake_pwm_percent']=m['intake_pwm_percent']
    summary['exhaust_pwm_percent']=m['exhaust_pwm_percent']
    summary['equivalent_velocity_mps']=m['velocity']
    summary['mode']=m.get('mode','BOTH');summary['boundary_model']=m.get('boundary_model','legacy_equivalent_inlet_flow_pressure_outlet')
    summary['case_setup_hash']=m.get('case_setup_hash');summary['config_hash']=m['config_hash']
    summary.update(assess_quality(rows,m))
    csv_write(root/'csv'/f'{m["case_id"]}_timeseries.csv',rows)
    csv_write(root/'csv'/f'{m["case_id"]}_summary.csv',[summary]);dump(root/'csv'/f'{m["case_id"]}_summary.json',summary)
    dump(root/'csv'/f'{m["case_id"]}_integration.json',dict(
        case_id=m['case_id'],start_s=times[0],end_s=times[-1],rows=len(rows),
        interval_s=c['field_interval_s'],missing_scheduled_times=[],
        policy='Rebuild from all saved fields; later restart flux wins; no interpolation.',
        ignored_superseded_trailing_rows=restart_audit))
    # Portable overview chart (no plotting dependency).
    key='C_mean' if m['physics']=='SMOKE' else 'T_mean_C' if m['physics']=='HEAT' else 'U_mean_mps'
    values=[r[key] for r in rows];lo=min(values);hi=max(values);span=max(hi-lo,1e-6)
    pts=' '.join(f'{70+780*t/max(times[-1],1):.2f},{350-270*(v-lo)/span:.2f}' for t,v in zip(times,values))
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="920" height="420"><rect width="100%" height="100%" fill="white"/><text x="30" y="30">{m["case_id"]} | {key}</text><text x="30" y="60">range: {lo:.5g} to {hi:.5g}</text><path d="M70 70 V350 H850" stroke="black" fill="none"/><polyline points="{pts}" fill="none" stroke="#086bc9" stroke-width="2"/><text x="70" y="380">0 s</text><text x="780" y="380">{times[-1]:g} s</text></svg>'
    (root/'csv'/f'{m["case_id"]}_overview.svg').write_text(svg,encoding='utf-8')
    print('Exported:',root/'csv')

if __name__=='__main__':main()
