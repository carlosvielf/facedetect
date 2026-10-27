import React, {useCallback, useEffect, useRef, useState} from 'react';
import {Camera, Download, Play, Plug, PlugZap, Square, Unplug} from 'lucide-react';
import {api, blob, getToken, wsUrl} from './api';

type CameraRow = {id:number; name:string; status:string};
type SessionRow = {id:number; class_id:number; camera_id:number; status:string};
type Step = {name:string; ok:boolean; ms:number; detail:string};
type Probe = {status:string; steps:Step[]; width:number|null; height:number|null; framesize:number|null; framesize_label:string|null};
type Face = {bbox:number[]; detection_score:number; state:'detected'|'recognized'|'uncertain'|'unknown'; issue:string|null; name:string|null; similarity:number|null};
type Analysis = {seq:number; mode:string; width:number; height:number; faces:Face[]; count:number; processing_ms:number|null; inference_fps:number; error:string|null};
type Live = {state?:string; source?:string|null; error?:string|null; fps?:number; width?:number|null; height?:number|null; acquisition_ms?:number|null; timestamp?:number; corrupted?:number; mode?:string};
type Mode = 'preview'|'detection'|'recognition';

const modeLabels:Record<Mode,string> = {preview:'Pré-visualização', detection:'Detecção facial', recognition:'Reconhecimento facial'};
const stateLabels:Record<string,string> = {disconnected:'Desconectada', testing:'Testando…', connected:'Conectada', connecting:'Conectando ao stream…', streaming:'Transmitindo', error:'Erro', reconnecting:'Reconectando…'};
const faceStyle:Record<Face['state'],{color:string;label:string}> = {
  detected:{color:'#3b82f6', label:'Rosto detectado'}, recognized:{color:'#16a34a', label:'Aluno reconhecido'},
  uncertain:{color:'#f59e0b', label:'Identificação incerta'}, unknown:{color:'#dc2626', label:'Desconhecido'},
};
const issues:Record<string,string> = {too_small:'rosto pequeno', blurry:'desfocado', low_confidence:'detecção fraca', no_embedding:'sem embedding'};

export function CameraMonitor({cameras, sessions, classes}:{cameras:CameraRow[]; sessions:SessionRow[]; classes:{id:number;name:string}[]}) {
  const [cameraId,setCameraId] = useState<number|null>(cameras[0]?.id ?? null);
  const [connection,setConnection] = useState('disconnected');
  const [probe,setProbe] = useState<Probe|null>(null);
  const [viewing,setViewing] = useState(false);
  const [mode,setMode] = useState<Mode>('preview');
  const [sessionId,setSessionId] = useState<number|null>(null);
  const [overlay,setOverlay] = useState(true);
  const [live,setLive] = useState<Live>({});
  const [analysis,setAnalysis] = useState<Analysis|null>(null);
  const [error,setError] = useState(''); const [notice,setNotice] = useState('');
  const [sizes,setSizes] = useState<{value:number;label:string}[]>([]);
  const img = useRef<HTMLImageElement>(null); const canvas = useRef<HTMLCanvasElement>(null);
  const socket = useRef<WebSocket|null>(null); const pending = useRef<Live|null>(null);
  const retry = useRef<{attempt:number; timer?:number}>({attempt:0}); const wanted = useRef(false);
  const lastUi = useRef(0); const lastAnalysis = useRef<Analysis|null>(null);
  const overlayRef = useRef(true); const current = useRef<{mode:Mode; session:number|null}>({mode:'preview', session:null});
  const running = sessions.filter(s=>s.status==='running' && s.camera_id===cameraId);

  useEffect(()=>{void api<{value:number;label:string}[]>('/cameras/framesizes').then(setSizes).catch(()=>undefined);},[]);
  useEffect(()=>{if(cameraId===null&&cameras.length)setCameraId(cameras[0].id);},[cameras,cameraId]);

  const draw = useCallback(()=>{
    const c = canvas.current, image = img.current, data = lastAnalysis.current;
    if(!c||!image)return;
    c.width = image.clientWidth; c.height = image.clientHeight;
    const ctx = c.getContext('2d'); if(!ctx)return;
    ctx.clearRect(0,0,c.width,c.height);
    if(!overlayRef.current||!data||!data.width)return;
    const sx = c.width/data.width, sy = c.height/data.height;
    ctx.font = '600 12px Inter, Segoe UI, sans-serif'; ctx.lineWidth = 2;
    for(const face of data.faces){
      const [x1,y1,x2,y2] = face.bbox; const style = faceStyle[face.state];
      ctx.strokeStyle = style.color; ctx.strokeRect(x1*sx,y1*sy,(x2-x1)*sx,(y2-y1)*sy);
      const text = face.state==='recognized' ? `${face.name} · similaridade ${face.similarity?.toFixed(2)}`
        : face.state==='detected' ? `${style.label} · detecção ${face.detection_score.toFixed(2)}${face.issue?` · ${issues[face.issue]||face.issue}`:''}`
        : `${style.label}${face.similarity!==null?` · similaridade ${face.similarity.toFixed(2)}`:''}`;
      const width = ctx.measureText(text).width + 10, top = Math.max(0, y1*sy - 20);
      ctx.fillStyle = style.color; ctx.fillRect(x1*sx, top, width, 18);
      ctx.fillStyle = '#fff'; ctx.fillText(text, x1*sx + 5, top + 13);
    }
  },[]);
  useEffect(()=>{overlayRef.current=overlay; draw();},[overlay,draw]);
  useEffect(()=>{draw(); const onResize=()=>draw(); window.addEventListener('resize',onResize); return()=>window.removeEventListener('resize',onResize);},[draw]);

  const closeSocket = useCallback(()=>{
    window.clearTimeout(retry.current.timer);
    const ws = socket.current; socket.current = null;
    if(ws){ws.onclose=null; ws.close();}
  },[]);

  const open = useCallback((id:number, nextMode:Mode, session:number|null)=>{
    closeSocket();
    const ws = new WebSocket(wsUrl(`/cameras/${id}/ws`)); ws.binaryType = 'blob'; socket.current = ws;
    ws.onopen = ()=>ws.send(JSON.stringify({type:'auth', token:getToken(), mode:nextMode, session_id:session}));
    ws.onmessage = event=>{
      if(typeof event.data !== 'string'){
        const meta = pending.current; pending.current = null;
        const url = URL.createObjectURL(event.data as Blob); const image = img.current;
        if(!image){URL.revokeObjectURL(url);return;}
        const previous = image.dataset.url; image.dataset.url = url;
        image.onload = ()=>{if(previous)URL.revokeObjectURL(previous); draw();};
        image.src = url;
        retry.current.attempt = 0;
        const now = performance.now();
        if(meta && now - lastUi.current > 250){lastUi.current = now; setLive(old=>({...old, ...meta}));}
        return;
      }
      const message = JSON.parse(event.data);
      if(message.type==='frame') pending.current = message;
      else if(message.type==='analysis'){lastAnalysis.current = message; setAnalysis(message); draw();}
      else if(message.type==='status'){setLive(old=>({...old, state:message.state, source:message.source, error:message.error, corrupted:message.corrupted, mode:message.mode, ...(message.state!=='streaming'?{fps:message.fps}:{})})); if(message.mode){setMode(message.mode); current.current={mode:message.mode, session:message.mode==='recognition'?current.current.session:null};}}
      else if(message.type==='error') setError(message.message);
    };
    ws.onclose = ()=>{
      socket.current = null;
      if(!wanted.current)return;
      // Bounded reconnection: 1, 2, 4, 8, 15 s, then stop and ask the user.
      const attempt = ++retry.current.attempt;
      if(attempt > 5){wanted.current=false; setViewing(false); setLive(old=>({...old,state:'error'})); setError('A conexão com o backend caiu e não foi restabelecida. Clique em "Iniciar visualização" para tentar novamente.'); return;}
      setLive(old=>({...old,state:'reconnecting'}));
      retry.current.timer = window.setTimeout(()=>open(id,current.current.mode,current.current.session), Math.min(15000, 1000*2**(attempt-1)));
    };
  },[closeSocket,draw]);

  useEffect(()=>()=>{wanted.current=false; closeSocket(); const url=img.current?.dataset.url; if(url)URL.revokeObjectURL(url);},[closeSocket]);

  const test = async (connect:boolean)=>{
    if(cameraId===null)return;
    setError(''); setNotice(''); if(connect)setConnection('testing');
    try {
      const result = await api<Probe>(`/cameras/${cameraId}/test`,'POST'); setProbe(result);
      if(connect) setConnection(result.steps.find(s=>s.name==='capture')?.ok ? 'connected' : result.status==='disconnected' ? 'disconnected' : 'error');
      if(result.status!=='ok') setError(result.steps.filter(s=>!s.ok).map(s=>`${s.name}: ${s.detail}`).join(' | '));
      else setNotice('Câmera respondeu em todas as etapas.');
    } catch(e){ if(connect)setConnection('error'); setError(e instanceof Error?e.message:String(e)); }
  };
  const start = ()=>{ if(cameraId===null)return; setError(''); wanted.current=true; retry.current.attempt=0; setViewing(true); setLive({state:'connecting'}); current.current={mode:'preview',session:null}; open(cameraId,'preview',null); setMode('preview'); setAnalysis(null); lastAnalysis.current=null; };
  const stop = ()=>{ wanted.current=false; closeSocket(); setViewing(false); setLive(old=>({...old,state:'disconnected',fps:0})); setAnalysis(null); lastAnalysis.current=null; draw(); };
  const disconnect = ()=>{ stop(); setConnection('disconnected'); setProbe(null); setNotice(''); setError(''); };
  const changeMode = (next:Mode)=>{
    setError('');
    if(next==='recognition' && !sessionId){setError('Selecione uma chamada em andamento desta câmera para habilitar o reconhecimento.');return;}
    if(next==='preview'){setAnalysis(null); lastAnalysis.current=null; draw();}
    current.current = {mode:next, session:next==='recognition'?sessionId:null};
    socket.current?.send(JSON.stringify({type:'mode', mode:next, session_id:current.current.session}));
  };
  const snapshot = async ()=>{
    if(cameraId===null)return;
    try { const {data,headers} = await blob(`/cameras/${cameraId}/snapshot`); const url = URL.createObjectURL(data);
      const link = document.createElement('a'); link.href=url; link.download=`camera-${cameraId}-${new Date().toISOString().replace(/[:.]/g,'-')}.jpg`; link.click(); URL.revokeObjectURL(url);
      setNotice(`Imagem capturada (${headers.get('x-frame-width')}×${headers.get('x-frame-height')}).`);
    } catch(e){ setError(e instanceof Error?e.message:String(e)); }
  };
  const framesize = async (value:number)=>{
    if(cameraId===null)return;
    try { const result = await api<{label:string}>(`/cameras/${cameraId}/framesize`,'POST',{value}); setNotice(`Resolução solicitada: ${result.label}. A resolução medida aparece no próximo frame.`); }
    catch(e){ setError(e instanceof Error?e.message:String(e)); }
  };

  const state = viewing ? (live.state||'connecting') : connection;
  const counts = analysis ? analysis.faces.reduce<Record<string,number>>((acc,f)=>({...acc,[f.state]:(acc[f.state]||0)+1}),{}) : {};
  const lastFrame = live.timestamp ? new Date(live.timestamp*1000).toLocaleTimeString('pt-BR') : '—';

  return <>
    <article className="panel">
      <div className="section-head"><div><h2>Câmera</h2><p className="muted">Visualize a imagem antes de qualquer reconhecimento. Nada é reconhecido na pré-visualização.</p></div>
        <span className={`status-pill ${state}`}><span className="dot"/>{stateLabels[state]||state}</span></div>
      <div className="form-grid">
        <label>Câmera<select value={cameraId??''} disabled={viewing} onChange={e=>{disconnect(); setCameraId(Number(e.target.value)); setSessionId(null);}}>
          {!cameras.length&&<option value="">Nenhuma câmera cadastrada</option>}{cameras.map(c=><option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
        <label>Resolução da ESP32<select value="" disabled={connection!=='connected'} onChange={e=>e.target.value&&void framesize(Number(e.target.value))}>
          <option value="">{probe?.framesize_label ? `Atual: ${probe.framesize_label}` : 'Alterar resolução'}</option>{sizes.map(s=><option key={s.value} value={s.value}>{s.label}</option>)}</select></label>
      </div>
      <div className="actions monitor-actions">
        <button disabled={cameraId===null||connection==='testing'||connection==='connected'} onClick={()=>void test(true)}><Plug size={17}/>Conectar câmera</button>
        <button className="ghost" disabled={connection==='disconnected'&&!viewing} onClick={disconnect}><Unplug size={17}/>Desconectar câmera</button>
        <button disabled={connection!=='connected'||viewing} onClick={start}><Play size={17}/>Iniciar visualização</button>
        <button className="ghost" disabled={!viewing} onClick={stop}><Square size={17}/>Parar visualização</button>
        <button className="ghost" disabled={connection!=='connected'} onClick={()=>void snapshot()}><Download size={17}/>Capturar imagem</button>
        <button className="ghost" disabled={cameraId===null||connection==='testing'} onClick={()=>void test(false)}><PlugZap size={17}/>Testar conexão</button>
      </div>
      {error&&<div className="error" role="alert">{error}</div>}{notice&&<div className="success" role="status">{notice}</div>}
      {probe&&<div className="steps">{probe.steps.map(s=><div key={s.name} className={s.ok?'ok':'fail'}><strong>{s.name}</strong><span>{s.ok?'OK':'Falha'} · {s.ms} ms</span><small>{s.detail}</small></div>)}</div>}
    </article>
    <div className="monitor-grid">
      <article className="panel">
        <div className="viewer">
          <img ref={img} alt="Imagem ao vivo da câmera" hidden={!viewing}/>
          <canvas ref={canvas} hidden={!viewing}/>
          {!viewing&&<div className="viewer-empty"><Camera size={34}/><p>{connection==='connected'?'Clique em "Iniciar visualização".':'Conecte a câmera para começar.'}</p></div>}
        </div>
        {live.error&&viewing&&<p className="error" role="alert">{live.error}</p>}
      </article>
      <article className="panel">
        <h2>Medições</h2>
        <dl className="metrics">
          <dt>Resolução</dt><dd>{live.width ? `${live.width}×${live.height}` : probe?.width ? `${probe.width}×${probe.height}` : '—'}</dd>
          <dt>FPS medido</dt><dd>{viewing && live.fps ? live.fps.toFixed(1) : '—'}</dd>
          <dt>Latência de aquisição</dt><dd>{viewing && live.acquisition_ms!=null ? `${Math.round(live.acquisition_ms)} ms` : '—'}</dd>
          <dt>Último frame</dt><dd>{viewing ? lastFrame : '—'}</dd>
          <dt>Origem</dt><dd>{live.source==='stream'?'MJPEG :81/stream':live.source==='capture'?'/capture (polling)':'—'}</dd>
          <dt>Frames corrompidos</dt><dd>{live.corrupted ?? '—'}</dd>
        </dl>
        <h2>Modo</h2>
        <div className="mode-switch" role="radiogroup">{(Object.keys(modeLabels) as Mode[]).map(m=><button key={m} role="radio" aria-checked={mode===m} className={mode===m?'':'ghost'} disabled={!viewing} onClick={()=>changeMode(m)}>{modeLabels[m]}</button>)}</div>
        <label>Chamada para reconhecimento<select value={sessionId??''} onChange={e=>setSessionId(e.target.value?Number(e.target.value):null)}>
          <option value="">{running.length?'Selecione':'Nenhuma chamada em andamento nesta câmera'}</option>
          {running.map(s=><option key={s.id} value={s.id}>#{s.id} • {classes.find(c=>c.id===s.class_id)?.name||`Turma ${s.class_id}`}</option>)}</select></label>
        <small>O reconhecimento exige uma chamada em andamento, iniciada pelo professor, desta câmera. A sobreposição não registra presença: a lista provisória é gerada pela captura da chamada e confirmada pelo professor.</small>
        <label className="inline-check"><input type="checkbox" checked={overlay} onChange={e=>{setOverlay(e.target.checked);}}/>Mostrar sobreposição</label>
        {analysis&&mode!=='preview'&&<dl className="metrics">
          <dt>Rostos</dt><dd>{analysis.count}</dd>
          <dt>Processamento</dt><dd>{analysis.processing_ms!=null?`${Math.round(analysis.processing_ms)} ms`:'—'}</dd>
          <dt>FPS de inferência</dt><dd>{analysis.inference_fps.toFixed(1)}</dd>
          {(Object.keys(faceStyle) as Face['state'][]).filter(k=>counts[k]).map(k=><React.Fragment key={k}><dt>{faceStyle[k].label}</dt><dd>{counts[k]}</dd></React.Fragment>)}
        </dl>}
        {analysis?.error&&<p className="error" role="alert">{analysis.error}</p>}
        {mode!=='preview'&&<div className="legend">{(Object.keys(faceStyle) as Face['state'][]).filter(k=>mode==='recognition'||k==='detected').map(k=><span key={k}><i style={{background:faceStyle[k].color}}/>{faceStyle[k].label}</span>)}
          <small>"Detecção" é a confiança do detector de que há um rosto, não a probabilidade de identidade. "Similaridade" é o cosseno entre embeddings; o limiar precisa ser calibrado e não equivale a uma probabilidade de acerto.</small></div>}
      </article>
    </div>
  </>;
}
