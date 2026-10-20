"""As-built geometry and Foundation fluid dictionaries; validate using OpenFOAM 14."""
import math
from pathlib import Path
from common import MODES, PROBES, dump, digest, require, validate

def foam(path, body, cls='dictionary'):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('FoamFile\n{ format ascii; class '+cls+'; object '+path.name+'; }\n'+body+'\n',encoding='utf-8')

def mesh(case, refinement):
    slope=.065/2/.320;dz=.04/math.sqrt(1+slope*slope)
    zs=[0,.08,.08+dz,.20,.20+dz,.32];ys=[-.095,-.055,.055,.095]
    keys=[(i,j,k) for i in range(2) for j in range(6) for k in range(6)]
    ids={t:n for n,t in enumerate(keys)}
    def xyz(i,j,k):
        z=zs[k];h=.125+.125*z
        return ((2*i-1)*(.1725+slope*z),[-h,*ys,h][j],z)
    fmt=lambda seq:'('+' '.join(map(str,seq))+')'
    patches={'inlet':[],'outlet':[],'walls':[]};blocks=[];ncells=0
    for j in range(5):
        for k in range(5):
            v=[ids[t] for t in [(0,j,k),(1,j,k),(1,j+1,k),(0,j+1,k),(0,j,k+1),(1,j,k+1),(1,j+1,k+1),(0,j+1,k+1)]]
            cells=[41*refinement,[7,4,11,4,7][j]*refinement,math.ceil((zs[k+1]-zs[k])/.01)*refinement]
            ncells+=math.prod(cells);blocks.append('hex '+fmt(v)+' '+fmt(cells)+' simpleGrading (1 1 1)')
            def face(seq):return fmt([v[i] for i in seq])
            patches['inlet' if j==1 and k==1 else 'walls'].append(face([0,4,7,3]))
            patches['outlet' if j==3 and k==3 else 'walls'].append(face([1,2,6,5]))
            if j==0:patches['walls'].append(face([0,1,5,4]))
            if j==4:patches['walls'].append(face([3,7,6,2]))
            if k==0:patches['walls'].append(face([0,3,2,1]))
            if k==4:patches['walls'].append(face([4,5,6,7]))
    body='convertToMeters 1;\nvertices (\n'+'\n'.join(fmt(xyz(*t)) for t in keys)+');\nblocks (\n'+'\n'.join(blocks)+');\nedges ();\nboundary (\n'
    for name,fs in patches.items():body+=name+' { type '+('wall' if name=='walls' else 'patch')+'; faces ('+' '.join(fs)+'); }\n'
    foam(case/'system/blockMeshDict',body+');\nmergePatchPairs ();')
    for x,y,z in PROBES:require(0<z<.32 and abs(x)<.1725+slope*z and abs(y)<.125+.125*z,'Probe outside geometry')
    n=[1/math.sqrt(1+slope*slope),0,slope/math.sqrt(1+slope*slope)]
    zi=.08+dz/2;zo=.20+dz/2
    return dict(inlet_normal=n,outlet_normal=[n[0],0,-n[2]],inlet_center=[-(.1725+slope*zi),-.075,zi],outlet_center=[.1725+slope*zo,.075,zo],patch_area_m2=.0016,mesh_cells=ncells)

def generate(case, c, physics, level, end=None, pilot=False, mode='BOTH'):
    validate(c)
    require(physics in ('BASE','SMOKE','HEAT'),'Unknown physics')
    case=Path(case);case.mkdir(parents=True,exist_ok=False)
    require(mode in MODES,'Unknown fan mode '+str(mode))
    g=mesh(case,c['mesh_refinement']);u=c['levels'][level]['velocity_mps'];end=end or c['duration_s']
    ta=c['ambient_c']+273.15;ti=c['heat_initial_c']+273.15 if physics=='HEAT' else ta
    boundary_model=('exhaust_driven_pressure_inlet' if mode=='EXHAUST'
                    else 'equivalent_inlet_flow_pressure_outlet')
    pwm=c['levels'][level]
    intake_pwm=pwm['intake_pwm_percent'] if mode in ('BOTH','INTAKE') else 0
    exhaust_pwm=pwm['exhaust_pwm_percent'] if mode in ('BOTH','EXHAUST') else 0
    m=dict(config=c,config_hash=digest(c),mode=mode,physics=physics,condition_id=level,
           case_id=f'{physics}_{level}_{c["revision"]}',pilot=pilot,end=end,velocity=u,
           intake_pwm_percent=intake_pwm,exhaust_pwm_percent=exhaust_pwm,
           boundary_model=boundary_model,inactive_fan_resistance_modeled=False,
           initial=ti,ambient=ta,probes=PROBES,probes_status='virtual_unconfirmed',flow_basis=pwm['basis']+'_equivalent_flow', flow_source=pwm['source_note'],equivalent_flow_m3_s=u*g['patch_area_m2'],fans_individually_modeled=False,**g)
    m['equivalent_flow_group']=pwm.get('equivalent_flow_group',level)
    m['equivalent_flow_assumption']=c.get('equivalent_flow_assumption',{})
    m['case_setup_hash']=digest({'config':c,'mode':mode,'boundary_model':boundary_model})
    if pilot:m['case_id']+='_PILOT'
    dump(case/'case.json',m)
    foam(case/'constant/physicalProperties',f'''thermoType
{{ type heRhoThermo; mixture pureMixture; transport const; thermo eConst;
equationOfState Boussinesq; specie specie; energy sensibleInternalEnergy; }}
mixture {{ specie {{ molWeight 28.97; }}
equationOfState {{ rho0 {c['rho0_kg_m3']}; T0 {ta}; beta {1/ta}; }}
thermodynamics {{ Cv {c.get('effective_heat_capacity_J_kgK',718)}; hf 0; }} transport {{ mu {c['mu_Pa_s']}; Pr 0.71; }} }}''')
    gravity=c['heat_gravity_mps2'] if physics=='HEAT' else [0,0,0]
    foam(case/'constant/g','dimensions [0 1 -2 0 0 0 0]; value ('+' '.join(map(str,gravity))+');','uniformDimensionedVectorField')
    def field(name,dim,initial,inlet,outlet,wall,vector=False):
        foam(case/'0'/name,f'dimensions {dim}; internalField uniform {initial};\nboundaryField {{ inlet {{ {inlet} }} outlet {{ {outlet} }} walls {{ {wall} }} }}','volVectorField' if vector else 'volScalarField')
    if mode=='EXHAUST':
        uin='type pressureInletOutletVelocity; value uniform (0 0 0);'
        uout='type fixedValue; value uniform ('+' '.join(str(u*x) for x in g['outlet_normal'])+');'
        tin=f'type inletOutlet; inletValue uniform {ta}; value uniform {ta};'
        tout='type zeroGradient;'
        pin='type prghPressure; rho rho; p uniform 101325; value uniform 101325;'
        pout='type fixedFluxPressure; value uniform 101325;'
    else:
        uin='type fixedValue; value uniform ('+' '.join(str(u*x) for x in g['inlet_normal'])+');'
        uout='type pressureInletOutletVelocity; value uniform (0 0 0);'
        tin=f'type fixedValue; value uniform {ta};'
        tout=f'type inletOutlet; inletValue uniform {ta}; value uniform {ti};'
        pin='type fixedFluxPressure; value uniform 101325;'
        pout='type prghPressure; rho rho; p uniform 101325; value uniform 101325;'
    field('U','[0 1 -1 0 0 0 0]','(0 0 0)',uin,uout,'type noSlip;',True)
    field('T','[0 0 0 1 0 0 0]',ti,tin,tout,'type zeroGradient;')
    field('p','[1 -1 -2 0 0 0 0]',101325,*(['type calculated; value uniform 101325;']*3))
    field('p_rgh','[1 -1 -2 0 0 0 0]',101325,pin,pout,'type fixedFluxPressure; value uniform 101325;')
    sst=c['flow_model']=='sst'
    foam(case/'constant/momentumTransport','simulationType RAS; RAS { model kOmegaSST; turbulence on; }' if sst else 'simulationType laminar;')
    foam(case/'constant/thermophysicalTransport','simulationType RAS; RAS { model eddyDiffusivity; Prt 0.85; }' if sst else 'simulationType laminar; laminar { model Fourier; }')
    if sst:
        kval=1.5*(u*.05)**2;omega=math.sqrt(kval)/(.09**.25*.0028)
        for name,dim,val,wall in [('k','[0 2 -2 0 0 0 0]',kval,'kqRWallFunction'),('omega','[0 0 -1 0 0 0 0]',omega,'omegaWallFunction')]:
            field(name,dim,val,f'type fixedValue; value uniform {val};',f'type inletOutlet; inletValue uniform {val}; value uniform {val};',f'type {wall}; value uniform {val};')
        for name,dim,wall in [('nut','[0 2 -1 0 0 0 0]','nutkWallFunction'),('alphat','[1 -1 -1 0 0 0 0]','compressible::alphatWallFunction')]:
            field(name,dim,0,'type calculated; value uniform 0;','type calculated; value uniform 0;',f'type {wall}; value uniform 0;')
    if physics=='SMOKE':
        cin=('type inletOutlet; inletValue uniform 0; value uniform 0;' if mode=='EXHAUST'
             else 'type fixedValue; value uniform 0;')
        cout=('type zeroGradient;' if mode=='EXHAUST'
              else 'type inletOutlet; inletValue uniform 0; value uniform 1;')
        field('C','[0 0 0 0 0 0 0]',1,cin,cout,'type zeroGradient;')
    foam(case/'system/fvSchemes','''ddtSchemes { default Euler; }
gradSchemes { default Gauss linear; }
divSchemes { default none;
div(phi,U) Gauss upwind; div(phi,e) Gauss upwind;
div(phi,(p|rho)) Gauss linear; div(phi,K) Gauss linear;
div(phi,k) Gauss upwind; div(phi,omega) Gauss upwind; div(phi,C) Gauss upwind;
div(((rho*nuEff)*dev2(T(grad(U))))) Gauss linear; }
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
wallDist { method meshWave; }''')
    foam(case/'system/fvSolution','''solvers {
"rho.*" { solver diagonal; }
p_rgh { solver PCG; preconditioner DIC; tolerance 1e-8; relTol 0.01; }
p_rghFinal { $p_rgh; relTol 0; }
"(U|e|k|omega|C)" { solver PBiCGStab; preconditioner DILU; tolerance 1e-8; relTol 0; }
"(U|e|k|omega|C)Final" { $U; relTol 0; }
}
PIMPLE { momentumPredictor yes; nOuterCorrectors 2; nCorrectors 2;
nNonOrthogonalCorrectors 1; pRefCell 0; pRefValue 101325; }''')
    iv=c['field_interval_s'];func=''
    if physics=='SMOKE':
        func+=f'''tracer {{ type scalarTransport; libs ("libsolverFunctionObjects.so");
field C; phi phi; rho rho; diffusivity viscosity; alphal {1/c['smoke_Sc']}; alphat {1/c['smoke_Sct']};
executeControl timeStep; executeInterval 1; writeControl writeTime; }}\n'''
    for name in ('inlet','outlet'):
        func+=f'''{name}Flux {{ type surfaceFieldValue; libs ("libfieldFunctionObjects.so");
writeControl writeTime; patch {name}; operation sum; fields (phi); writeFields false; }}\n'''
    func+='sensors { type probes; libs ("libsampling.so"); fields (T U'+(' C' if physics=='SMOKE' else '')+'); writeControl writeTime; probeLocations ('+' '.join('(%g %g %g)'%p for p in PROBES)+'); }\n'
    if sst:func+='yPlusCheck { type yPlus; libs ("libfieldFunctionObjects.so"); executeControl writeTime; writeControl writeTime; }\n'
    foam(case/'system/controlDict',f'''application foamRun; solver fluid;
startFrom startTime; startTime 0; stopAt endTime; endTime {end};
deltaT 0.001; adjustTimeStep yes; maxCo 0.4; maxDeltaT 0.02;
writeControl adjustableRunTime; writeInterval {iv}; purgeWrite 0;
writeFormat ascii; writePrecision 10; writeCompression on;
timeFormat general; timePrecision 10; runTimeModifiable true;
functions {{ {func} }}''')
    (case/'case.foam').touch()
    dump(case/'status.json',{'status':'GENERATED'})
    return m
