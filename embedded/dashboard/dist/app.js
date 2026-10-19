'use strict';
const $ = s => document.querySelector(s);
const icons = {
 dashboard:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
 box:'<path d="m12 3 9 5v8l-9 5-9-5V8zM3 8l9 5 9-5M12 13v8M7.5 5.5l9 5"/>',
 chart:'<path d="M3 3v18h18M6 15l4-5 4 3 6-8"/>',
 history:'<path d="M3 11a9 9 0 1 1 2.5 7M3 5v6h6M12 7v5l3 2"/>',
 chip:'<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 3v3m6-3v3M9 18v3m6-3v3M3 9h3m-3 6h3m12-6h3m-3 6h3"/>',
 dust:'<circle cx="12" cy="5" r="1"/><circle cx="5" cy="9" r="1"/><circle cx="19" cy="9" r="1"/><circle cx="8" cy="16" r="1"/><circle cx="16" cy="16" r="1"/><circle cx="12" cy="11" r="1"/><circle cx="12" cy="21" r=".7"/>',
 temp:'<path d="M9 14V5a3 3 0 0 1 6 0v9a5 5 0 1 1-6 0Z"/><path d="M12 7v10"/><circle cx="12" cy="17" r="1"/>',
 drop:'<path d="M12 3C9 7 5 11 5 15a7 7 0 0 0 14 0c0-4-4-8-7-12Z"/>',
 fan:'<circle cx="12" cy="12" r="2"/><path d="M10 10C5 2 17 1 16 6c0 3-2 4-3 4M14 12c9-1 4 11 0 8-3-2-3-4-2-6M11 14C7 22 0 12 5 10c3-1 5 0 6 1" fill="currentColor" stroke="none"/>',
 camera:'<rect x="3" y="6" width="13" height="13" rx="2"/><path d="m16 10 5-3v11l-5-3"/>',
 bell:'<path d="M5 17h14l-2-3V9a5 5 0 0 0-10 0v5zM10 21h4M12 2v2"/>',
 link:'<path d="m9 15 6-6M8 17l-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0m2-1 1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0" transform="translate(1 0)"/>',
 file:'<path d="M6 3h8l4 4v14H6zM14 3v5h4M9 12h6m-6 4h6"/>',
 download:'<path d="M12 3v12m-4-4 4 4 4-4M4 15v5h16v-5"/>',
 upload:'<path d="M12 16V4m-4 4 4-4 4 4M4 15v5h16v-5"/>',
 expand:'<path d="M3 9V3h6m6 0h6v6M3 15v6h6m6 0h6v-6"/>',
 info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7v1"/>',
 check:'<path d="m5 12 4 4L19 6"/>',
 photo:'<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 6-5 4 3 4-6 4 5"/>',
 power:'<path d="M12 2v10M6 5a9 9 0 1 0 12 0"/>'
};
const icon = (name, cls='') => `<svg class="icon ${cls}" viewBox="0 0 24 24" aria-hidden="true">${icons[name]||icons.chip}</svg>`;
const esc = str => String(str).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const badge=(text,kind='')=>`<span class="badge ${kind}">${text}</span>`;
const today = () => new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
const state = {page:'dashboard',range:5,date:today(),filter:'all',eventPage:1,event:'DEMO-003',situation:'SMOKE',intake:35,exhaust:100,clips:new Map()};
const events = [
 {id:'DEMO-003',time:'15:01',type:'PM2.5',unit:'µg/m³',value:82,threshold:50,intake:60,exhaust:60,done:false,duration:'—',values:[82,79,74,69,64]},
 {id:'DEMO-002',time:'14:32',type:'PM2.5',unit:'µg/m³',value:132,threshold:50,intake:100,exhaust:100,done:true,duration:'02:18',values:[132,105,79,58,43]},
 {id:'DEMO-001',time:'14:05',type:'온도',unit:'°C',value:29.8,threshold:25,intake:60,exhaust:100,done:true,duration:'01:42',values:[29.8,28.3,26.9,25.2,24.1]}
];
// Historical samples belong to the same explicitly labelled demonstration dataset.
['13:40','13:12','12:48','12:15','11:52','11:26'].forEach((time,i)=>{
 const base=events[i%2+1];events.push({...base,id:'DEMO-OLDER-'+(i+1),time,values:[...base.values]});
});
const EVENT_PAGE_SIZE=5;
function eventWindow(rows){
 const pages=Math.max(1,Math.ceil(rows.length/EVENT_PAGE_SIZE));
 state.eventPage=Math.min(Math.max(1,state.eventPage),pages);
 return {pages,rows:rows.slice((state.eventPage-1)*EVENT_PAGE_SIZE,state.eventPage*EVENT_PAGE_SIZE)};
}
function eventTimeline(e){const end=e.done?e.duration.split(':').reduce((m,n)=>m*60+Number(n),0):120;return [0,30,60,90,end];}
const narrowCharts=window.matchMedia('(max-width:650px)');
const eventPlotWidth=()=>narrowCharts.matches?340:700;
function eventLabels(e){return ['발생','+30초','+1분',narrowCharts.matches&&eventTimeline(e).at(-1)<120?'':'+1분30초',e.done?e.duration:'+2분'];}
function powerSamples(e){return e.type==='온도'?[1.52,1.49,1.47,1.44,1.42]:e.intake===100?[1.92,1.89,1.91,1.88,1.86]:[1.16,1.14,1.15,1.13,1.12];}
function eventEnergy(e){const values=powerSamples(e),times=eventTimeline(e);return values.slice(1).reduce((sum,v,i)=>sum+(values[i]+v)/2*(times[i+1]-times[i])/3600,0);}
function fanReason(e){return e.type==='온도'?'온도 상승 상황을 가정하여 배기를 흡기보다 높게 설정했습니다. 흡기로 공기를 보충하면서 배기 중심으로 내부 열을 내보내는 예시 조합입니다.':e.intake===100?'미세먼지 농도가 높은 상황을 가정하여 흡기·배기를 모두 100%로 설정했습니다. 이 예시는 소비 전력보다 빠른 공기 교환을 우선하는 선택을 보여줍니다.':'미세먼지가 감소하고 있는 상황을 가정하여 흡기·배기를 60%로 유지했습니다. 환기를 이어가면서 최대 출력 운전을 줄이는 예시 조합입니다.';}
function showPowerReason(e){
 $('#power-detail').innerHTML=`<p class="eyebrow">FAN COMBINATION</p><h2 id="power-title">팬 조합 선택 이유</h2><p class="note">${e.time} · ${e.type} · ${e.id}</p><div class="chosen-fans"><span>흡기 <b>${e.intake}%</b></span><span>배기 <b>${e.exhaust}%</b></span></div><h3>이 상황에서의 선택 근거 · 예시</h3><p class="reason-copy">${fanReason(e)}</p><div class="reason-context">발생값 <b>${e.value} ${e.unit}</b> → 마지막 표시값 <b>${e.values.at(-1)} ${e.unit}</b><br>회복 기준 ${e.threshold} ${e.unit} · ${e.done?'회복 완료':'회복 진행 중'}</div><h3>전력 데이터 안내</h3><p class="reason-copy">그래프는 흡기·배기 팬 2대의 합산 소비 전력 예시(W)입니다. 센서·ESP32·Jetson 전력은 포함하지 않습니다. 실제 전력 계측값이나 제어기의 선택 로그가 아니며, PWM 비율만으로 전력을 환산하지 않았습니다.</p><p class="note">실제 선택 근거는 팬 조합별 회복 성능·전력 측정 결과와 제어기 로그를 연결하면 표시할 수 있습니다. 이 예시는 최적 조합 또는 절감 효과를 입증하지 않습니다.</p>`;
 $('#power-dialog').showModal();
}
const deviceData=[
 {
  "name": "PMS5003",
  "model": "미세먼지 센서 · Plantower",
  "kind": "dust",
  "image": "pms5003.jpg",
  "role": "챔버 내부의 PM2.5 농도를 측정하여 환기 제어와 회복 판단에 사용합니다.",
  "measure": "PM1.0 · PM2.5 · PM10",
  "connection": "ESP32 · UART",
  "specs": [
   [
    "PM2.5 유효 범위",
    "0–500 µg/m³"
   ],
   [
    "일치 오차 · 0–100",
    "±10 µg/m³"
   ],
   [
    "일치 오차 · 100–500",
    "±10%"
   ],
   [
    "분해능",
    "1 µg/m³"
   ]
  ],
  "note": "표준 입자 기준의 센서 간 일치 오차입니다. 절대 정확도 보증과는 다르며, 입자 조성·습도·설치 환경에 따라 측정값이 달라질 수 있습니다.",
  "source": "https://cdn-shop.adafruit.com/product-files/3686/plantower-pms5003-manual_v2-3.pdf",
  "credit": "Adafruit",
  "photoSource": "https://www.adafruit.com/product/3686"
 },
 {
  "name": "BME280",
  "model": "Environmental Sensor · 6pin",
  "kind": "temp",
  "image": "bme280.jpg",
  "role": "챔버의 온도와 상대습도를 기록합니다. 기압도 측정할 수 있으며, 현재 화면에는 온도·습도를 표시합니다.",
  "measure": "온도 · 습도 · 기압",
  "connection": "ESP32 · I²C / SPI",
  "specs": [
   [
    "온도 측정 범위",
    "−40–85 °C"
   ],
   [
    "온도 정확도",
    "±1 °C (0–65 °C)"
   ],
   [
    "습도 범위 / 정확도",
    "0–100 %RH / ±3 %RH"
   ],
   [
    "기압 측정 범위",
    "300–1,100 hPa"
   ]
  ],
  "note": "온도 정확도는 Waveshare 모듈 명시값입니다. 습도 오차 ±3 %RH는 25 °C·20–80 %RH 조건의 전형값이며, 결로 없는 환경을 기준으로 합니다. 보드 발열과 설치 위치는 온도에 영향을 줄 수 있습니다.",
  "source": "https://www.waveshare.com/product/modules/bme280-environmental-sensor.htm",
  "credit": "The Pi Hut · Waveshare",
  "photoSource": "https://thepihut.com/products/bme280-environmental-sensor"
 },
 {
  "name": "ESP32 DevKitC",
  "model": "WROOM-32D V4 CP2102 · CMODULE-40",
  "kind": "chip",
  "image": "esp32.jpg",
  "role": "PMS5003·BME280의 데이터를 수집하고, 흡기·배기 팬의 PWM 제어와 회전수 피드백을 중계합니다.",
  "measure": "센서 수집 · PWM / RPM",
  "connection": "Jetson Orin Nano ↔ ESP32",
  "specs": [
   [
    "무선 모듈",
    "ESP32-WROOM-32D"
   ],
   [
    "USB–UART",
    "CP2102"
   ],
   [
    "보드 / USB",
    "DevKitC V4 · Micro USB"
   ]
  ],
  "note": "사용자가 지정한 CMODULE-40 제품의 사진입니다. 실제 수집·제어 상태는 장비 API 연결 후 표시됩니다.",
  "source": "https://www.devicemart.co.kr/goods/view?no=15313999",
  "credit": "디바이스마트 · Happy House",
  "photoSource": "https://www.devicemart.co.kr/goods/view?no=15313999"
 },
 {
  "name": "흡기·배기 팬",
  "model": "FAN-4020-PWM-5V · 2대",
  "kind": "fan",
  "image": "fan.jpg",
  "role": "흡기는 외부 공기를 넣고, 배기는 챔버 내부 공기를 내보냅니다. 같은 모델 2대의 PWM을 각각 조절하여 환기 흐름을 만듭니다.",
  "measure": "흡기 IN + 배기 OUT",
  "connection": "ESP32 · PWM / Tach",
  "specs": [
   [
    "전원 / 크기",
    "5 V · 40 × 40 × 20 mm"
   ],
   [
    "구성",
    "흡기 1대 + 배기 1대"
   ],
   [
    "제어 / 피드백",
    "PWM 속도 제어 · RPM"
   ]
  ],
  "note": "사진은 공통으로 사용하는 팬 1대입니다. 흡기와 배기는 같은 모델이며, 장착 방향과 역할이 다릅니다. PWM 비율과 실제 전력은 동일한 비율이 아닙니다.",
  "source": "https://www.waveshare.com/fan-4020-pwm-5v.htm",
  "credit": "Waveshare · 판매 제품 사진",
  "photoSource": "https://bigamart.com/product/waveshare-2-pack-dedicated-cooling-fan-for-jetson-nano-developer-kit-5v-pwm-speed-adjustment-strong-cooling-air-4pin-reverse-proof-connector/"
 },
 {
  "name": "InnoMaker USB 카메라",
  "model": "USB U20CAM-720P",
  "kind": "camera",
  "image": "camera.jpg",
  "role": "챔버 내부를 관찰하고 이상 발생·진행·회복 시점의 현장 사진을 기록합니다.",
  "measure": "영상 · 이벤트 스냅샷",
  "connection": "Jetson Orin Nano · USB",
  "specs": [
   [
    "해상도",
    "1280 × 720"
   ],
   [
    "영상 형식",
    "MJPEG · 720p 최대 30 fps"
   ],
   [
    "인터페이스",
    "USB 2.0 · UVC"
   ]
  ],
  "note": "InnoMaker U20CAM-720P 제품 사진입니다. 현재 CCTV는 저장된 실험 사진이며 실제 영상 스트림은 미연결 상태입니다.",
  "source": "https://www.inno-maker.com/product/u20cam-720p/",
  "credit": "InnoMaker",
  "photoSource": "https://www.inno-maker.com/product/u20cam-720p/"
 },
 {
  "name": "Jetson Orin Nano",
  "model": "시스템 연산 · 통합 제어",
  "kind": "chip",
  "image": "jetson.jpg",
  "role": "센서와 카메라 데이터를 통합하고 팬 제어 로직, 영상 처리, 이상·회복 기록을 관리합니다.",
  "measure": "영상 처리 · 제어 · 기록",
  "connection": "ESP32 · USB 카메라 · 대시보드",
  "specs": [
   [
    "역할",
    "중앙 연산 및 제어"
   ],
   [
    "연동 장치",
    "ESP32 · U20CAM-720P"
   ],
   [
    "대시보드",
    "상태 및 이벤트 전송"
   ]
  ],
  "note": "NVIDIA Jetson Orin Nano 개발자 키트 제품 사진입니다. 실제 장비의 케이스와 부속 구성은 다를 수 있습니다.",
  "source": "https://developer.nvidia.com/blog/develop-ai-powered-robots-smart-vision-systems-and-more-with-nvidia-jetson-orin-nano-developer-kit/",
  "credit": "NVIDIA",
  "photoSource": "https://developer.nvidia.com/blog/develop-ai-powered-robots-smart-vision-systems-and-more-with-nvidia-jetson-orin-nano-developer-kit/"
 }
];
const names={dashboard:'메인 대시보드','cfd-current':'CFD · 현재 팬 조합','cfd-select':'CFD · 선택 팬 조합',trends:'센서 트렌드',events:'이상·회복 기록',hardware:'하드웨어 연결 상태'};
function heading(title,desc,eyebrow,extra=''){return `<div class="page-heading"><div><p class="eyebrow">${eyebrow}</p><h1>${title}</h1><p>${desc}</p></div>${extra||'<div class="heading-meta"><span class="dot"></span> CHAMBER 01 <span>·</span> 예시 모드</div>'}</div>`;}
const mini=(color='#438cf4')=>`<svg class="mini-line" viewBox="0 0 70 22" aria-hidden="true"><path d="M1 16 9 13 17 15 25 7 33 10 41 6 49 11 57 7 69 5" fill="none" stroke="${color}" stroke-width="1.7"/></svg>`;
function metric(name,ic,value,unit,tag,bottom,kind='',extra=''){return `<article class="card metric ${extra}"><div class="metric-top"><span class="metric-name">${icon(ic)}${name}</span>${badge(tag,kind)}</div><div class="metric-number">${value}<small>${unit}</small></div><div class="metric-bottom"><span>${bottom}</span>${mini(kind==='green'?'#2fbaa6':'#438cf4')}</div></article>`;}
function fan(label,dir,rpm){return `<article class="card fan-card"><div class="fan-info"><div class="fan-circle">${icon('fan','spin')}</div><div><div class="fan-title">${label} <span>(${dir})</span></div><div class="rpm">${rpm}<small>RPM</small></div><span class="small-label">현재 회전수 · 예시</span></div></div><div class="fan-bars">${badge('● 회전 확인 · 예시','green')}<div class="pwm"><span>목표 PWM</span><div class="track"><i style="width:60%"></i></div><b>60%</b></div><div class="pwm"><span>장치 PWM</span><div class="track teal"><i style="width:60%"></i></div><b>60%</b></div></div></article>`;}
function connections(){return ['ESP32 · PMS5003 / BME280','U20CAM-720P 카메라','Jetson Orin Nano','Aiven DB 전송'].map(n=>`<div class="connection">${n}<span><i class="dot"></i>미연결</span></div>`).join('');}
function dashboard(){return heading('대시보드','챔버 환경과 팬 운전 상태를 한눈에 확인하세요.','OVERVIEW')+`
 <div class="metrics">${metric('미세먼지 PM2.5','dust','82','µg/m³','기준 초과','제어용 PM 79 · ↓ 감소 중','red')}${metric('온도','temp','21.8','°C','정상 범위','설정 기준 25 °C','blue')}${metric('습도','drop','57.7','%RH','측정 기록','상대습도 · 상태 판정 미사용')}${metric('컨테이너 상태','box','환기 중','','자동 운전','예시 상황 · 팬 중풍 운전','green','container-metric')}</div>
 <h2 class="section-label">팬 운전 상태 <span>흡기·배기 팬의 목표값과 장치 피드백</span></h2><div class="fans">${fan('흡기 팬','IN','3,420')}${fan('배기 팬','OUT','3,580')}</div>
 <div class="dashboard-bottom"><section class="card camera-card"><div class="card-head"><h2>${icon('camera')}챔버 CCTV</h2>${badge('기록 사진 · 실시간 아님')}</div><div class="camera" id="camera-view"><img src="assets/chamber.jpg" alt="챔버 내부의 실험 기록 사진"><div class="camera-top"><span class="camera-tag">CAM 01 &nbsp; CHAMBER</span><span class="camera-tag">RECORDED IMAGE</span></div><div class="camera-bottom"><div><b>챔버 내부 모니터링</b><small>카메라 연결 전 · 저장된 실험 사진을 표시합니다</small></div><button class="camera-action" id="camera-expand" aria-label="챔버 사진 전체 화면">${icon('expand')}</button></div></div><div class="camera-footer"><span>단일 챔버 · 내부 카메라</span><span>실시간 스트림 연결 대기</span></div></section>
 <div class="right-stack"><section class="card"><div class="card-head"><h2>${icon('bell')}최근 알림·운전 기록</h2><a class="text-link" href="#events">전체 보기 →</a></div><div class="activity">${[['15:01','dust','PM2.5 기준 초과','제어용 PM 79 · 중풍으로 환기 중'],['14:34','check','미세먼지 회복 완료','회복 소요 시간 2분 18초'],['14:22','link','센서 수신 확인 · 예시','센서 데이터 수신 예시 기록']].map(x=>`<div class="activity-row"><time>${x[0]}</time><span class="activity-icon">${icon(x[1])}</span><div><b>${x[2]}</b><p>${x[3]}</p></div></div>`).join('')}</div></section><section class="card"><div class="card-head"><h2>${icon('link')}시스템 연동 상태</h2><a class="text-link" href="#hardware">상세 →</a></div><div class="connections">${connections()}</div></section></div></div>`;}
let chartCounter=0;
function chart(values,{max=100,min=0,threshold=null,labels=['15:00','15:01','15:02','15:03','15:04'],height=238,width=420,color='#2985f8',times=null,decimals=0,label='센서 측정값'}={}){
 const w=width,h=height,l=35,r=12,t=25,b=32,pw=w-l-r,ph=h-t-b,id='area-'+(++chartCounter);
 const y=v=>t+ph-(v-min)/(max-min)*ph,x=i=>l+(times?(times[i]-times[0])/(times.at(-1)-times[0]):i/(values.length-1))*pw,points=values.map((v,i)=>`${x(i)},${y(v)}`).join(' ');
 return `<svg class="chart" role="img" aria-label="${esc(label)}: ${values.map(v=>v.toFixed(1)).join(', ')}" viewBox="0 0 ${w} ${h}"><defs><linearGradient id="${id}" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#d9eaff" stop-opacity=".85"/><stop offset="1" stop-color="#edf5ff" stop-opacity=".15"/></linearGradient></defs>${Array.from({length:5},(_,i)=>{let v=min+(max-min)*i/4,yy=y(v);return `<line x1="${l}" x2="${w-r}" y1="${yy}" y2="${yy}" stroke="#edf1f7"/><text x="${l-10}" y="${yy+3}" text-anchor="end">${v.toFixed(decimals)}</text>`;}).join('')}<path d="M${l},${t+ph} L${points.replaceAll(' ',' L')} L${w-r},${t+ph}Z" fill="url(#${id})"/>${threshold!==null?`<line x1="${l}" x2="${w-r}" y1="${y(threshold)}" y2="${y(threshold)}" stroke="#f07389" stroke-dasharray="5 5"/><text class="threshold-label" x="${w-r}" y="${y(threshold)-8}" text-anchor="end">기준 ${threshold}</text>`:''}<polyline points="${points}" fill="none" stroke="${color}" stroke-width="2.6" stroke-linejoin="round"/>${values.map((v,i)=>`<circle cx="${x(i)}" cy="${y(v)}" r="3" fill="${color}"><title>${esc(label)} ${v}</title></circle>`).join('')}${labels.map((label,i)=>`<text x="${times?x(i):l+i/(labels.length-1)*pw}" y="${h-9}" text-anchor="${i===0?'start':i===labels.length-1?'end':'middle'}">${label}</text>`).join('')}</svg>`;
}
function trends(){
 const count=state.range===5?11:state.range===15?31:61;
 const series=(last,amp)=>Array.from({length:count},(_,i)=>+(last+amp*Math.sin((count-i-1)*.4)+amp*.7*(count-i-1)/count).toFixed(1));
 const pm=Array.from({length:count},(_,i)=>+(82+Math.sin((count-i-1)/count*Math.PI)*113+(count-i-1)*.4).toFixed(1));
 const end=15*60+4,labels=Array.from({length:5},(_,i)=>{let v=Math.round(end-state.range+state.range*i/4);return `${String(Math.floor(v/60)).padStart(2,'0')}:${String(v%60).padStart(2,'0')}`;});
 const cards=[{name:'PM2.5',ic:'dust',value:'82',unit:'µg/m³',values:pm,max:250,threshold:50,tag:'↓ 감소 중',color:'blue'}, {name:'온도',ic:'temp',value:'21.8',unit:'°C',values:series(21.8,.22),max:40,threshold:25,tag:'정상 범위',color:'blue'}, {name:'습도',ic:'drop',value:'57.7',unit:'%RH',values:series(57.7,.8),max:100,threshold:null,tag:'측정 기록',color:''}];
 return heading('센서 트렌드','시간에 따른 미세먼지, 온도, 습도 변화를 확인하세요.','SENSOR ANALYTICS')+`<div class="toolbar"><div class="segmented" role="group" aria-label="조회 기간">${[5,15,60].map(n=>`<button data-range="${n}" class="${state.range===n?'active':''}" aria-pressed="${state.range===n}">${n===5?'최근 5분':n===15?'15분':'1시간'}</button>`).join('')}</div><span class="small-label">예시 데이터 · 실제 센서 측정값이 아닙니다</span></div><div class="trend-grid">${cards.map(c=>`<section class="card trend-card"><div class="metric-top"><span class="metric-name">${icon(c.ic)}<b>${c.name}</b></span><span class="small-label">예시 데이터</span></div><div class="metric-number">${c.value}<small>${c.unit}</small></div>${badge(c.tag,c.color)}<div class="trend-chart">${chart(c.values,{max:c.max,threshold:c.threshold,labels,label:c.name})}</div><div class="chart-legend"><span><i></i>측정값</span>${c.threshold!==null?'<span><i class="dash"></i>기준선</span>':''}</div></section>`).join('')}</div><section class="card summary"><h2>현재 상태 요약</h2><div class="summary-grid"><div><small>PM2.5</small><strong class="red-text">기준 초과</strong><p>82 µg/m³ · 예시 회복 기준 50 µg/m³</p></div><div><small>온도</small><strong class="blue-text">정상 범위</strong><p>21.8 °C · 예시 설정 기준 25 °C</p></div><div><small>습도</small><strong>측정 기록</strong><p>57.7 %RH · 상태 판정에 사용하지 않음</p></div></div></section>`;
}
function filteredEvents(){return state.date===today()?events.filter(e=>state.filter==='all'||(state.filter==='done'?e.done:!e.done)):[];}
function eventDetail(e){if(!e)return '<section class="card event-detail"><div class="empty-events">선택한 조건에 해당하는 기록이 없습니다.</div></section>';
 return `<section class="card event-detail"><h2><span>${e.time} · ${e.type} 기준 초과</span>${badge(e.done?'회복 완료':'진행 중',e.done?'green':'red')}</h2><div class="event-stats">${[['발생값',e.value,e.unit,'red-text'],['기준',e.threshold,e.unit,''],['흡기 PWM',e.intake,'%','blue-text'],['배기 PWM',e.exhaust,'%','blue-text']].map(x=>`<div><small>${x[0]}</small><b class="${x[3]}">${x[1]}<span>${x[2]}</span></b></div>`).join('')}</div><h3>${e.type} 회복 추이</h3>${chart(e.values,{max:e.type==='온도'?40:e.value>100?160:100,threshold:e.threshold,labels:eventLabels(e),label:e.type+' 회복 추이',height:165,width:eventPlotWidth(),times:eventTimeline(e)})}<section class="power-panel"><div class="power-heading"><h3>전력 소모 <small>팬 2대 합산 · W</small></h3><button class="info-button" id="power-info" aria-label="팬 조합 선택 이유" aria-haspopup="dialog" title="팬 조합 선택 이유">${icon('info')}</button></div><div class="power-summary"><span><i></i>예시 소비 전력</span><span>구간 사용량 <b>${eventEnergy(e).toFixed(3)} Wh</b></span></div>${chart(powerSamples(e),{max:2.4,height:155,width:eventPlotWidth(),decimals:1,color:'#00a68a',times:eventTimeline(e),labels:eventLabels(e),label:'팬 합산 예시 전력 W'})}<p class="power-note">실측값 아님 · 센서·제어 보드 전력 제외</p></section><h3 style="margin-top:24px">현장 사진 &nbsp; ${badge('배치 확인용 기록 사진')}</h3><div class="event-photos">${['발생 사진','진행 사진','회복 사진'].map((t,i)=>`<div><small>${t}</small>${i===2&&!e.done?`<div class="photo-empty">${icon('photo')}회복 대기 중</div>`:`<img src="assets/chamber.jpg" alt="${t} 위치의 예시 사진; 동일한 실험 기록 사진 사용">`}</div>`).join('')}</div><p class="note" style="font-size:9px;margin-bottom:0">${e.id} · 예시 이벤트입니다. 사진은 배치 확인을 위해 동일한 실험 기록 사진을 사용했습니다.</p></section>`;
}
function eventsPage(){let allRows=filteredEvents(),window=eventWindow(allRows),rows=window.rows;if(!rows.some(e=>e.id===state.event))state.event=rows[0]?.id;
 return heading('이상·회복 기록','발생 시점부터 회복까지, 센서 변화와 현장 사진을 함께 확인하세요.','EVENT HISTORY')+`<div class="toolbar"><div class="toolbar-group"><input type="date" id="event-date" aria-label="조회 날짜" value="${state.date}"><select id="event-filter" aria-label="이벤트 상태"><option value="all" ${state.filter==='all'?'selected':''}>전체 상태</option><option value="active" ${state.filter==='active'?'selected':''}>진행 중</option><option value="done" ${state.filter==='done'?'selected':''}>회복 완료</option></select><span class="small-label">${allRows.length}개의 이벤트</span></div><button class="button" id="export-csv">${icon('download')}예시 CSV 내보내기</button></div><div class="events-layout"><section class="card events-list"><h2><span>이상·회복 이벤트</span>${badge('예시 기록')}</h2><table><thead><tr><th>발생 시각</th><th>항목</th><th>상태</th><th>회복 시간</th></tr></thead><tbody>${rows.map(e=>`<tr data-event="${e.id}" class="${e.id===state.event?'selected':''}"><td><button class="event-select" aria-label="${e.time} ${e.type} 이벤트 상세 보기">${e.time}</button></td><td>${e.type}</td><td>${badge(e.done?'회복 완료':'진행 중',e.done?'green':'red')}</td><td>${e.duration}</td></tr>`).join('')}</tbody></table>${!rows.length?'<div class="empty-events">조회된 이벤트가 없습니다.</div>':''}<nav class="pagination" aria-label="이벤트 목록 페이지"><button data-event-page="${state.eventPage-1}" ${state.eventPage===1?'disabled':''} aria-label="이전 페이지">‹</button>${Array.from({length:window.pages},(_,i)=>`<button data-event-page="${i+1}" ${state.eventPage===i+1?'aria-current="page"':''} aria-label="${i+1}페이지">${i+1}</button>`).join('')}<button data-event-page="${state.eventPage+1}" ${state.eventPage===window.pages?'disabled':''} aria-label="다음 페이지">›</button><span>${state.eventPage} / ${window.pages} 페이지</span></nav><p class="note">한 페이지에 최대 ${EVENT_PAGE_SIZE}개씩 표시합니다. 한 사건 안에서 센서 기록과 사진을 함께 확인합니다. 오늘 날짜에 표시된 목록은 화면 검토용 예시입니다.</p></section>${eventDetail(rows.find(e=>e.id===state.event))}</div>`;
}
function specRows(d){return d.specs.map(([name,value])=>`<div><dt>${name}</dt><dd>${value}</dd></div>`).join('');}
function hardware(){return heading('하드웨어 연결 상태','실제 사용 제품과 측정 사양, 시스템에서의 역할을 확인하세요.','HARDWARE DIRECTORY',badge('장치 6종 · 연결 0 / 7대'))+`<div class="info-strip">${icon('info')}<div>사용 모델의 제품 사진입니다. 흡기·배기 팬은 동일 모델 2대를 한 카드로 표시합니다. 장비 상태는 현재 미연결입니다.</div></div><div class="hardware-grid">${deviceData.map((d,i)=>`<article class="card hardware-card"><div class="device-picture">${badge('● 미연결')}<img src="assets/hardware/${d.image}" alt="${d.name} ${d.model} 실제 제품 사진" loading="lazy"><span class="illustration-label">PRODUCT PHOTO</span></div><div class="device-content"><h2>${d.name}</h2><span class="device-model">${d.model}</span><p>${d.role}</p><dl class="device-specs">${specRows(d)}</dl><div class="device-footer"><span>${d.measure}</span><button class="text-link" data-device="${i}">상세 보기 ↗</button></div><a class="photo-credit" href="${d.photoSource}" target="_blank" rel="noopener noreferrer">사진: ${d.credit} ↗</a></div></article>`).join('')}</div>`;}
function combo(){return state.page==='cfd-current'?{situation:'SMOKE',intake:60,exhaust:60}:{situation:state.situation,intake:state.intake,exhaust:state.exhaust};}
const clipKey=c=>`I${c.intake}E${c.exhaust}_${c.situation}`;
function cfd(){const c=combo(),key=clipKey(c),clip=state.clips.get(key),current=state.page==='cfd-current';return heading(current?'CFD · 현재 팬 조합':'CFD · 선택 팬 조합',current?'현재 운전 조합에 해당하는 사전 계산 영상을 확인하세요.':'상황과 흡기·배기 팬 조합을 선택하여 영상을 확인하세요.','AIRFLOW VISUALIZATION')+`<div class="info-strip">${icon('info')}<div>영상을 선택하면 재생할 수 있습니다. 사전 계산된 실험 데이터 기반 영상입니다.<br>실시간 CFD 계산이나 실제 PM2.5 채널 영상이 아닙니다.</div></div><section class="card"><div class="cfd-toolbar"><span><b style="font-size:12px">${key}</b> &nbsp; ${badge(current?'현재 조합 · 예시':'선택 조합','blue')}</span>${current?'<div class="combination"><span>상황 <b>연기</b></span><span>흡기 <b>60%</b></span><span>배기 <b>60%</b></span></div>':`<div class="combination"><label>상황<select id="situation"><option value="SMOKE" ${c.situation==='SMOKE'?'selected':''}>연기</option><option value="HEAT" ${c.situation==='HEAT'?'selected':''}>열</option></select></label>${['intake','exhaust'].map(n=>`<label>${n==='intake'?'흡기':'배기'}<select id="${n}">${[35,60,100].map(v=>`<option value="${v}" ${c[n]===v?'selected':''}>${v}%</option>`).join('')}</select></label>`).join('')}</div>`}</div>${clip?`<video class="cfd-video" controls preload="metadata" src="${clip.url}" aria-label="${key} 사전 계산 영상"></video>`:`<div class="cfd-canvas"><canvas id="airflow" aria-label="챔버 기류 개념도. 계산된 CFD 결과가 아닙니다."></canvas><div class="cfd-caption"><b>CHAMBER AIRFLOW</b><br>흡기 ${c.intake}% / 배기 ${c.exhaust}%<br>구조 개념도 · CFD 결과 아님</div><div class="cfd-legend"><i></i><span>기류<br>방향<br>개념</span></div><div class="cfd-watermark">SCHEMATIC PREVIEW &nbsp; / &nbsp; ${key}</div><div class="cfd-empty">원본 영상 미등록</div></div>`}<div class="player-controls"><div><p>${clip?esc(clip.name):`${key}.mp4 · 영상 파일을 등록해 주세요`}</p><small>${clip?'이 브라우저 세션에서만 재생 · 서버에 업로드되지 않습니다':'미등록 상태에서는 기류 개념도를 표시합니다'}</small></div><label class="button" tabindex="0" id="clip-label">${icon('upload')}${clip?'영상 변경':'이 조합 영상 등록'}<input id="clip-upload" type="file" accept="video/mp4,video/webm,video/ogg" hidden></label></div></section><div class="cfd-foot"><div class="card"><small>선택 상황</small><b>${c.situation==='SMOKE'?'연기 · SMOKE':'열 · HEAT'}</b></div><div class="card"><small>흡기 팬 PWM</small><b>${c.intake}<small> %</small></b></div><div class="card"><small>배기 팬 PWM</small><b>${c.exhaust}<small> %</small></b></div></div>`;}
function drawAirflow(){const canvas=$('#airflow');if(!canvas)return;const rect=canvas.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,2);canvas.width=rect.width*dpr;canvas.height=rect.height*dpr;const ctx=canvas.getContext('2d');ctx.scale(dpr,dpr);let w=rect.width,h=rect.height,scale=Math.min(w/850,h/490),ox=w/2,oy=h/2+15;const point=(x,y,z)=>[ox+(x-z*.5)*scale,oy+(z*.28-y)*scale];
 const line=(points,color,width=1)=>{ctx.beginPath();points.forEach((p,i)=>{let [x,y]=point(...p);i?ctx.lineTo(x,y):ctx.moveTo(x,y);});ctx.strokeStyle=color;ctx.lineWidth=width;ctx.stroke();};
 const box=[[-160,-145,-110],[160,-145,-110],[160,145,-110],[-160,145,-110]];
 [0,1].forEach(side=>line([...box.map(p=>[p[0],p[1],side?110:-110]),[...box[0].slice(0,2),side?110:-110]],'#b6c6d9',1.2));
 box.forEach(p=>line([p,[p[0],p[1],110]],'#b6c6d9',1.2));
 for(let y=-145;y<=145;y+=58)line([[-160,y,-110],[160,y,-110],[160,y,110],[-160,y,110],[-160,y,-110]],'#dce5ef');
 const c=combo();
 for(let j=0;j<35;j++){let pts=[];for(let i=0;i<=100;i++){let t=i/100,x=-160+320*t,y=-85+180*t+Math.sin(t*Math.PI*2+j*.17)*(45+j*1.7),z=Math.sin(t*Math.PI*2+j*.48)*88;pts.push([x,y,z]);}line(pts,j%4===0?'#39b9bb88':'#4e97e65e',1.1);}
 for(let side of [-1,1]){const [x,y]=point(side*160,side*87,0);ctx.fillStyle=side<0?'#20b99a':'#327fdc';ctx.fillRect(x-7*scale,y-20*scale,14*scale,40*scale);ctx.font='10px sans-serif';ctx.fillStyle='#6886a7';ctx.fillText(side<0?`IN ${c.intake}%`:`OUT ${c.exhaust}%`,x-21,y+(side<0?40:-30)*scale);}
}
function showToast(text){$('#toast').textContent=text;$('#toast').classList.add('show');clearTimeout(showToast.timer);showToast.timer=setTimeout(()=>$('#toast').classList.remove('show'),3000);}
function downloadCSV(){const header=['날짜','발생 시각','항목','상태','회복 시간','발생값','기준','흡기 PWM','배기 PWM','데이터 구분'];const rows=filteredEvents().map(e=>[state.date,e.time,e.type,e.done?'회복 완료':'진행 중',e.duration,e.value,e.threshold,e.intake,e.exhaust,'예시 데이터']);const csv='\uFEFF'+[header,...rows].map(row=>row.map(v=>'"'+String(v).replaceAll('"','""')+'"').join(',')).join('\r\n');const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8;'}));const a=document.createElement('a');a.href=url;a.download=`AIRGUARD_예시이벤트_${state.date}.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);showToast(`${rows.length}개 예시 기록을 내보냈습니다.`);}
function bind(){
 document.querySelectorAll('[data-range]').forEach(b=>b.onclick=()=>{state.range=Number(b.dataset.range);render();});
 $('#event-date')?.addEventListener('change',e=>{state.date=e.target.value;state.eventPage=1;render();});
 $('#event-filter')?.addEventListener('change',e=>{state.filter=e.target.value;state.eventPage=1;render();});
 document.querySelectorAll('[data-event]').forEach(row=>row.onclick=()=>{state.event=row.dataset.event;render();});
 document.querySelectorAll('[data-event-page]').forEach(b=>b.onclick=()=>{state.eventPage=Number(b.dataset.eventPage);render();});
 $('#power-info')?.addEventListener('click',()=>showPowerReason(events.find(e=>e.id===state.event)));
 $('#export-csv')?.addEventListener('click',downloadCSV);
 $('#camera-expand')?.addEventListener('click',async()=>{try{await $('#camera-view').requestFullscreen();}catch{showToast('이 브라우저에서는 전체 화면을 사용할 수 없습니다.');}});
 ['situation','intake','exhaust'].forEach(key=>$('#'+key)?.addEventListener('change',e=>{state[key]=key==='situation'?e.target.value:Number(e.target.value);render();}));
 $('#clip-label')?.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();$('#clip-upload').click();}});
 $('#clip-upload')?.addEventListener('change',e=>{const f=e.target.files[0];if(!f)return;if(!/\.(mp4|webm|ogg)$/i.test(f.name)){showToast('MP4, WebM, OGG 영상 파일을 선택해 주세요.');return;}const key=clipKey(combo()),old=state.clips.get(key);if(old)URL.revokeObjectURL(old.url);state.clips.set(key,{url:URL.createObjectURL(f),name:f.name});render();showToast('선택한 조합의 영상이 등록되었습니다.');});
 $('.cfd-video')?.addEventListener('error',()=>showToast('영상을 재생할 수 없습니다. 지원되는 MP4/WebM 파일인지 확인해 주세요.'));
 document.querySelectorAll('[data-device]').forEach(b=>b.onclick=()=>{const d=deviceData[Number(b.dataset.device)];$('#hardware-detail').innerHTML=`<p class="eyebrow">HARDWARE DETAIL</p><h2 id="hardware-title">${d.name}</h2><p class="note">${d.role}</p><dl class="device-specs">${specRows(d)}</dl>${[['연결 상태','미연결'],['측정 / 제어 항목',d.measure],['연결 경로',d.connection],['모델 정보',d.model]].map(x=>`<div class="detail-spec"><span>${x[0]}</span><b>${x[1]}</b></div>`).join('')}<p class="note">${d.note}</p><a class="text-link" href="${d.source}" target="_blank" rel="noopener noreferrer">제품 사양 / 데이터시트 ↗</a>`;$('#hardware-dialog').showModal();});
}
function render(){chartCounter=0;$('#main').innerHTML=state.page==='dashboard'?dashboard():state.page==='trends'?trends():state.page==='events'?eventsPage():state.page==='hardware'?hardware():cfd();$('#breadcrumb').textContent=names[state.page];document.title=`${names[state.page]} | 에어가드`;document.querySelectorAll('[data-nav]').forEach(a=>{const active=a.dataset.nav===state.page||(a.dataset.nav==='cfd'&&state.page.startsWith('cfd'));a.classList.toggle('active',active);if(a.dataset.nav===state.page)a.setAttribute('aria-current','page');else a.removeAttribute('aria-current');});bind();requestAnimationFrame(drawAirflow);}
function route(){const page=location.hash.slice(1)||'dashboard';state.page=Object.hasOwn(names,page)?page:'dashboard';render();window.scrollTo(0,0);$('#sidebar').classList.remove('open');$('#menu').setAttribute('aria-expanded','false');}
document.querySelectorAll('[data-icon]').forEach(el=>el.innerHTML=icon(el.dataset.icon));
$('#menu').onclick=()=>{const open=$('#sidebar').classList.toggle('open');$('#menu').setAttribute('aria-expanded',String(open));};
document.addEventListener('click',e=>{if(!e.target.closest('#sidebar')&&!e.target.closest('#menu')){$('#sidebar').classList.remove('open');$('#menu').setAttribute('aria-expanded','false');}});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){$('#sidebar').classList.remove('open');$('#menu').setAttribute('aria-expanded','false');}});
document.querySelectorAll('dialog').forEach(dialog=>{dialog.querySelector('.close-dialog').onclick=()=>dialog.close();dialog.addEventListener('click',e=>{const r=dialog.getBoundingClientRect();if(e.target===dialog&&(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom))dialog.close();});});
function updateClock(){$('#clock').textContent=new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date());}
narrowCharts.addEventListener('change',()=>{if(state.page==='events')render();});
updateClock();setInterval(updateClock,1000);window.addEventListener('hashchange',route);window.addEventListener('resize',drawAirflow);route();
