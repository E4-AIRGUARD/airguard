"""ParaView: actual saved T/C, fixed final U, common scales, 60-second video."""
import argparse
from bisect import bisect_right
import csv
import math
from pathlib import Path
import shutil
import subprocess
from common import load,dump,require,scheduled_times
from recovery_library import SETTINGS


## Pure display mapping; never modifies the CFD or CSV values
def rgba(value,physics,ambient,initial):
    if physics=='HEAT':
        fraction=max(0.,min(1.,(value-ambient)/(initial-ambient)))
        return (220,35,15,round(255*.48*fraction))
    if physics=='SMOKE':
        # Fixed nonlinear display mapping reveals low concentrations; CFD is unchanged.
        c=max(0.,min(1.,value))
        return (0,0,0,round(255*.75*math.sqrt(c)))
    stops=[(0,(210,230,255)),(.1,(60,170,255)),(.4,(80,210,120)),(.7,(255,220,60)),(1,(220,35,15))]
    c=max(0.,min(1.,value))
    j=min(max(bisect_right([x[0] for x in stops],c)-1,0),len(stops)-2)
    left,right=stops[j],stops[j+1];w=(c-left[0])/(right[0]-left[0])
    return (*[round((1-w)*a+w*b) for a,b in zip(left[1],right[1])],round(255*.3*c))


def frame_times(times,frames):
    require(len(times)>=2 and frames>=2,'Need initial and final frames')
    return [times[round(i*(len(times)-1)/(frames-1))] for i in range(frames)]


def main():
    ap=argparse.ArgumentParser();ap.add_argument('case',type=Path);ap.add_argument('--csv',type=Path,required=True)
    ap.add_argument('--video',type=Path,required=True);ap.add_argument('--work',type=Path,required=True)
    ap.add_argument('--quality-label',required=True);ap.add_argument('--display-condition')
    ap.add_argument('--display-ambient',type=float);ap.add_argument('--display-initial',type=float);a=ap.parse_args()
    from paraview.simple import (MergeBlocks,CellDatatoPointData,CreateView,Show,ColorBy,
         GetColorTransferFunction,GetScalarBar,ExtractSurface,FeatureEdges,Text,Slice,Calculator,
         PointSource,StreamTracerWithCustomSource,CreateLayout,SaveScreenshot,Sphere,
         TrivialProducer,OpenFOAMReader,ForceTime)
    from paraview import servermanager
    from vtkmodules.vtkCommonCore import vtkUnsignedCharArray
    import vtk
    import numpy as np
    from vtk.util.numpy_support import vtk_to_numpy
    from foam_reader import open_volume
    m=load(a.case/'case.json');c=m['config'];ph=m['physics'];pair=m['condition_id'].replace('_','')
    if a.display_condition:pair=a.display_condition
    ambient=c['ambient_c'] if a.display_ambient is None else a.display_ambient
    initial=c['heat_initial_c'] if a.display_initial is None else a.display_initial
    require(math.isfinite(ambient) and math.isfinite(initial) and initial>ambient,'Invalid display temperature range')
    out=a.work;out.mkdir(parents=True,exist_ok=True)
    with a.csv.open(encoding='utf-8-sig') as f:rows={round(float(r['time_s']),6):r for r in csv.DictReader(f)}
    reader=open_volume(a.case/'case.foam',['T','U']+(['C'] if ph=='SMOKE' else []))
    times=scheduled_times(reader.TimestepValues,m)
    require(all(round(t,6) in rows for t in times),'CSV / saved-field timestamps differ')
    clock=ForceTime(Input=reader);clock.IgnorePipelineTime=1;clock.ForcedTime=m['end']
    cells=MergeBlocks(Input=clock)
    data=CellDatatoPointData(Input=cells);data.UpdatePipeline(time=m['end'])
    volume_filter=vtk.vtkCellSizeFilter();volume_filter.SetInputData(servermanager.Fetch(cells))
    volume_filter.ComputeVolumeOn();volume_filter.Update()
    volumes=vtk_to_numpy(volume_filter.GetOutput().GetCellData().GetArray('Volume'))
    weights=volumes/volumes.sum()
    obj=servermanager.Fetch(data);copy=obj.NewInstance();copy.DeepCopy(obj)
    fixed=TrivialProducer();fixed.GetClientSideObject().SetOutput(copy)
    speed=Calculator(Input=fixed);speed.ResultArrayName='Speed_m_s';speed.Function='mag(U)'
    scalar=Calculator(Input=data);scalar.ResultArrayName='DisplayScalar';scalar.Function='C' if ph=='SMOKE' else 'T-273.15'
    view=CreateView('RenderView');layout=CreateLayout(name='E4 AIRGUARD');layout.AssignView(0,view);layout.SetSize(1600,900)
    view.Background=[1,1,1]
    if 'UseColorPaletteForBackground' in view.ListProperties():view.UseColorPaletteForBackground=0
    view.CameraFocalPoint=[0,0,.16];view.CameraPosition=[.47,-.82,.47];view.CameraViewUp=[0,0,1]
    view.CameraParallelProjection=1;view.CameraParallelScale=.29
    edges=FeatureEdges(Input=ExtractSurface(Input=fixed));edges.ManifoldEdges=0;edges.FeatureEdges=1
    ed=Show(edges,view);ed.ColorArrayName=['POINTS',''];ed.DiffuseColor=[.25,.25,.25];ed.Opacity=1
    # Explicit RGBA avoids the old surface opacity/volume rendering incompatibility.
    slices=[];checks=[]
    if ph!='BASE':
        for z in (.04,.10,.16,.22,.28):
            sl=Slice(Input=scalar);sl.SliceType='Plane';sl.SliceType.Origin=[0,0,z];sl.SliceType.Normal=[0,0,1]
            producer=TrivialProducer();slices.append((sl,producer))
    def update_scalar(t):
        clock.ForcedTime=t;scalar.UpdatePipeline(time=t)
        # Verify the scalar used by the image is the same saved time as the CSV.
        scalar_obj=servermanager.Fetch(scalar)
        values=scalar_obj.GetPointData().GetArray('DisplayScalar')
        require(values is not None,'Missing render scalar')
        cellrow=rows[round(t,6)];lo=float(cellrow['C_min'] if ph=='SMOKE' else cellrow['T_min_C'])
        hi=float(cellrow['C_max'] if ph=='SMOKE' else cellrow['T_max_C'])
        display_min,display_max=values.GetRange()
        # Reader point interpolation may include boundary values; compare CSV to CELL data.
        require(math.isfinite(display_min) and math.isfinite(display_max),'Nonfinite render scalar')
        cell_data=servermanager.Fetch(cells).GetCellData().GetArray('C' if ph=='SMOKE' else 'T')
        require(cell_data is not None,'Missing cell scalar for render validation')
        actual=vtk_to_numpy(cell_data)
        if ph!='SMOKE':actual=actual-273.15
        mean=float(np.dot(weights,actual));expected_mean=float(cellrow['C_mean'] if ph=='SMOKE' else cellrow['T_mean_C'])
        require(np.isfinite(actual).all() and abs(float(actual.min())-lo)<1e-3 and abs(float(actual.max())-hi)<1e-3 and abs(mean-expected_mean)<1e-3,
                'Render scalar inconsistent with CSV time')
        for sl,producer in slices:
            sl.UpdatePipeline(time=t);obj=servermanager.Fetch(sl);poly=obj.NewInstance();poly.DeepCopy(obj)
            vals=poly.GetPointData().GetArray('DisplayScalar');require(vals is not None,'Missing slice scalar')
            colors=vtkUnsignedCharArray();colors.SetName('DisplayRGBA');colors.SetNumberOfComponents(4)
            for j in range(poly.GetNumberOfPoints()):colors.InsertNextTuple4(*rgba(vals.GetTuple1(j),ph,ambient,initial))
            poly.GetPointData().AddArray(colors);producer.GetClientSideObject().SetOutput(poly)
            producer.GetClientSideObject().Modified();producer.MarkModified(producer);producer.UpdatePipeline()
        checks.append(dict(time_s=t,point_scalar_min=display_min,point_scalar_max=display_max,
                           csv_cell_min=lo,csv_cell_max=hi,render_cell_mean=mean,csv_cell_mean=expected_mean))
    if slices:
        update_scalar(0)
        for sl,producer in slices:
            sd=Show(producer,view);ColorBy(sd,('POINTS','DisplayRGBA'));sd.MapScalars=0
            sd.Opacity=1;sd.Ambient=1;sd.Diffuse=0
            if 'ForceTranslucent' in sd.ListProperties():sd.ForceTranslucent=1
            sd.SetScalarBarVisibility(view,False)
    # Same final CFD streamlines throughout playback; no manufactured connections.
    ni=m['inlet_normal'];no=m.get('outlet_normal',[ni[0],0,-ni[2]])
    inlet=[x+.008*n for x,n in zip(m['inlet_center'],ni)]
    outlet=[x-.008*n for x,n in zip(m['outlet_center'],no)]
    seedspec=[([0,0,.16],.14,32,'FORWARD'),(inlet,.006,18,'FORWARD'),(outlet,.006,12,'BACKWARD')]
    lut=GetColorTransferFunction('Speed_m_s');vmax=SETTINGS['speed_max_mps']
    lut.RGBPoints=[0,.12,.22,.65,vmax/2,.05,.55,.3,vmax,.8,.08,.04];lut.RescaleTransferFunction(0,vmax)
    paths=[];termination={}
    for group,(center,radius,count,direction) in enumerate(seedspec):
        seed=PointSource();seed.Center=center;seed.Radius=radius;seed.NumberOfPoints=count
        if 'RandomSeed' in seed.ListProperties():seed.RandomSeed=17
        trace=StreamTracerWithCustomSource(Input=speed,SeedSource=seed);trace.Vectors=['POINTS','U']
        trace.IntegrationDirection=direction;trace.MaximumStreamlineLength=2.5;trace.MaximumSteps=30000;trace.TerminalSpeed=1e-8
        trace.UpdatePipeline();display=Show(trace,view);ColorBy(display,('POINTS','Speed_m_s'))
        display.LookupTable=lut;display.LineWidth=2.;display.Opacity=1
        lines=servermanager.Fetch(trace);require(lines.IsA('vtkPolyData'),'Expected streamline polydata')
        codes=lines.GetCellData().GetArray('ReasonForTermination');counts={}
        if codes is not None:
            for j in range(codes.GetNumberOfTuples()):
                code=str(int(codes.GetTuple1(j)));counts[code]=counts.get(code,0)+1
        termination[str(group)]=counts;chosen=0
        for n in range(lines.GetNumberOfCells()):
            cell=lines.GetCell(n)
            if cell.GetCellDimension()!=1:continue
            pts=[]
            for j in range(cell.GetNumberOfPoints()):
                pt=lines.GetPoint(cell.GetPointId(j))
                if not pts or math.dist(pts[-1],pt)>1e-10:pts.append(pt)
            if len(pts)<2:continue
            if direction=='BACKWARD':pts.reverse()
            arc=[0.]
            for x,y in zip(pts,pts[1:]):arc.append(arc[-1]+math.dist(x,y))
            if chosen<4:paths.append((pts,arc));chosen+=1
        require(chosen>0,'No valid flow paths in seed group '+str(group))
    balls=[]
    for pts,arc in paths:
        ball=Sphere();ball.Radius=.004096;ball.Center=pts[0];bd=Show(ball,view)
        bd.ColorArrayName=['POINTS',''];bd.DiffuseColor=[.65,0,.45];bd.AmbientColor=[.65,0,.45];bd.Ambient=1;bd.Diffuse=0
        balls.append((ball,bd))
    for patch,color in [('inlet',[0,.65,.1]),('outlet',[.9,.1,.05])]:
        port=OpenFOAMReader(FileName=str(a.case/'case.foam'));port.UpdatePipelineInformation()
        names=[n for n in port.MeshRegions.Available if n==patch or n.endswith('/'+patch)]
        require(names,'Boundary missing: '+patch);port.MeshRegions=names;port.UpdatePipeline(time=m['end'])
        obj=servermanager.Fetch(port);copy=obj.NewInstance();copy.DeepCopy(obj)
        frozenport=TrivialProducer();frozenport.GetClientSideObject().SetOutput(copy)
        pd=Show(frozenport,view);pd.ColorArrayName=['POINTS',''];pd.DiffuseColor=color;pd.AmbientColor=color;pd.Opacity=1;pd.Ambient=0;pd.Diffuse=1
    def bar(lut,title,x):
        b=GetScalarBar(lut,view);b.Visibility=1;b.Title=title;b.ComponentTitle=''
        b.TitleColor=[0,0,0];b.LabelColor=[0,0,0];b.TitleFontSize=14;b.LabelFontSize=12
        b.WindowLocation='Any Location';b.Position=[x,.2];b.ScalarBarLength=.40;return b
    bar(lut,'Speed (m/s)',.88)
    if ph!='BASE':
        legend=GetColorTransferFunction('ScalarLegend');legend.ColorSpace='RGB';points=[]
        for fraction in (0,.1,.25,.4,.5,.7,.75,1):
            val=ambient+(initial-ambient)*fraction if ph=='HEAT' else fraction
            r,g,b,alpha=rgba(val,ph,ambient,initial);a0=alpha/255
            points.extend([val,*[1-a0+a0*x/255 for x in (r,g,b)]])
        legend.RGBPoints=points;b=bar(legend,'Temperature (C)' if ph=='HEAT' else 'Tracer C (0-1)',.03)
        if 'UseCustomLabels' in b.ListProperties():
            b.UseCustomLabels=1;b.CustomLabels=[ambient,(ambient+initial)/2,initial] if ph=='HEAT' else [0,.5,1]
    title=Text();td=Show(title,view);td.Color=[0,0,0];td.FontSize=13
    note=Text();note.Text=('Markers: direction only | 5 slices: local saved field; overlap appears darker\n'
                           'HEAT: red fades to clear | SMOKE: black opacity=0.75*sqrt(C), white at C=0; not optical visibility')
    nd=Show(note,view);nd.Color=[0,0,0];nd.FontSize=11;nd.WindowLocation='Lower Left Corner'
    frames=[];count=SETTINGS['fps']*SETTINGS['video_s'];last=None
    ts=[m['end']]*count if ph=='BASE' else frame_times(times,count)
    cmd=['ffmpeg','-y','-loglevel','warning','-f','image2pipe','-vcodec','png','-framerate',str(SETTINGS['fps']),
         '-i','-','-an','-c:v','libx264','-crf','23','-pix_fmt','yuv420p','-movflags','+faststart',str(a.video)]
    with (out/'ffmpeg.log').open('w') as log:
        proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stderr=log)
        try:
            for i,t in enumerate(ts):
                if t!=last and ph!='BASE':update_scalar(t)
                last=t;video_time=i/SETTINGS['fps'];row=rows[round(t,6)]
                mean=(f'mean C={float(row["C_mean"]):.4f}' if ph=='SMOKE' else f'mean T={float(row["T_mean_C"]):.2f} C')
                title.Text=(f'{pair} | {ph} | CFD t={t:g}s | {mean}\n'
                            f'Fixed final {ph} streamlines; evolving scalar from coupled CFD | Ueq={m["velocity"]:.3f} m/s assumed\n'+a.quality_label)
                for j,((pts,arc),(ball,bd)) in enumerate(zip(paths,balls)):
                    d=(video_time*.04+j*.027)%arc[-1];k=min(bisect_right(arc,d)-1,len(pts)-2);f=(d-arc[k])/(arc[k+1]-arc[k])
                    ball.Center=[pts[k][n]*(1-f)+pts[k+1][n]*f for n in range(3)];bd.Opacity=min(1,d/.008,(arc[-1]-d)/.008)
                screenshot=out/'current_frame.png';SaveScreenshot(str(screenshot),layout,ImageResolution=[1600,900])
                proc.stdin.write(screenshot.read_bytes())
                if i in (0,count//2,count-1):shutil.copyfile(screenshot,out/f'preview_{i:04d}.png')
                frames.append(dict(CFD_time_s=t,video_time_s=video_time,representative_flow_time_s=m['end']))
                if i%100==0:print(f'{pair}_{ph}: frame {i+1}/{count}, CFD {t:g}s',flush=True)
            proc.stdin.close();require(proc.wait(timeout=120)==0,'ffmpeg failed; inspect '+str(out/'ffmpeg.log'))
        finally:
            # FFmpeg may still be waiting for pipe input when rendering raises.
            # Bound cleanup so the original exception can reach the log.
            if proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
            if not proc.stdin.closed:
                try:proc.stdin.close()
                except (BrokenPipeError,OSError):pass
    dump(out/'frames.json',frames);dump(out/'scalar_render_checks.json',checks);dump(out/'streamline_termination_codes.json',termination)


if __name__=='__main__':main()
