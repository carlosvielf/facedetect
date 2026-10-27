import {useEffect, useState} from 'react';
import {RefreshCw} from 'lucide-react';
import {api, getToken, wsUrl} from './api';

type Check = {key:string; label:string; status:'ok'|'error'|'disconnected'|'not_tested'; detail:string; ms:number|null; checked_at:string|null};
type Report = {checks:Check[]; config:Record<string,unknown>; model:Record<string,unknown>; readers:Record<string,unknown>[]};
const statusLabels:Record<Check['status'],string> = {ok:'Funcionando', error:'Erro', disconnected:'Desconectado', not_tested:'Não testado'};
const untested = (key:string,label:string):Check => ({key,label,status:'not_tested',detail:'Ainda não executado',ms:null,checked_at:null});

// Runs in the browser: the only place that can prove the frontend reaches the backend (REST + WebSocket).
async function frontendCheck(cameraId:number|undefined):Promise<Check> {
  const started = performance.now();
  const base = {key:'frontend', label:'Comunicação frontend/backend'};
  try { await api('/live'); } catch(e) { return {...base, status:'disconnected', detail:`REST falhou: ${e instanceof Error?e.message:e}. Confira VITE_API_URL, CORS_ORIGINS e se a API está ativa.`, ms:null, checked_at:new Date().toISOString()}; }
  const rest = Math.round(performance.now()-started);
  if(cameraId===undefined) return {...base, status:'ok', detail:`REST ok (${rest} ms). WebSocket não testado: nenhuma câmera cadastrada.`, ms:rest, checked_at:new Date().toISOString()};
  const wsStarted = performance.now();
  const ws = await new Promise<string>(resolve=>{
    const socket = new WebSocket(wsUrl(`/cameras/${cameraId}/ws`));
    const timer = window.setTimeout(()=>{socket.close(); resolve('tempo esgotado');}, 5000);
    socket.onopen = ()=>socket.send(JSON.stringify({type:'auth', token:getToken(), mode:'preview'}));
    socket.onmessage = event=>{ if(typeof event.data==='string'){ const m = JSON.parse(event.data); window.clearTimeout(timer); socket.close(); resolve(m.type==='status'?'':m.message||m.type); } };
    socket.onerror = ()=>{ window.clearTimeout(timer); resolve('conexão recusada (proxy WebSocket/HTTPS?)'); };
  });
  const wsMs = Math.round(performance.now()-wsStarted);
  return ws ? {...base, status:'error', detail:`REST ok (${rest} ms); WebSocket falhou: ${ws}`, ms:rest, checked_at:new Date().toISOString()}
            : {...base, status:'ok', detail:`REST ${rest} ms; WebSocket autenticado em ${wsMs} ms`, ms:rest+wsMs, checked_at:new Date().toISOString()};
}

export function Diagnostics({cameraIds}:{cameraIds:number[]}) {
  const [report,setReport] = useState<Report|null>(null);
  const [frontend,setFrontend] = useState<Check>(untested('frontend','Comunicação frontend/backend'));
  const [running,setRunning] = useState<string[]>([]); const [error,setError] = useState('');
  const load = async ()=>{ try { setReport(await api<Report>('/diagnostics')); } catch(e){ setError(e instanceof Error?e.message:String(e)); } };
  useEffect(()=>{void load();},[]);
  const run = async (key?:string)=>{
    setError(''); setRunning(key?[key]:['all']);
    try {
      if(!key||key==='frontend') setFrontend(await frontendCheck(cameraIds[0]));
      if(key!=='frontend') await api(`/diagnostics/run${key?`?check=${key}`:''}`,'POST');
      await load();
    } catch(e){ setError(e instanceof Error?e.message:String(e)); }
    finally { setRunning([]); }
  };
  const checks = report ? [...report.checks.slice(0,1), frontend, ...report.checks.slice(1)] : [frontend];
  const busy = (key:string)=>running.includes('all')||running.includes(key);
  return <>
    <article className="panel">
      <div className="section-head"><div><h2>Verificações</h2><p className="muted">Cada indicador só aparece como "Funcionando" depois que o teste correspondente rodou com sucesso.</p></div>
        <button disabled={running.length>0} onClick={()=>void run()}><RefreshCw size={17}/>{running.includes('all')?'Executando…':'Executar todas'}</button></div>
      {error&&<div className="error" role="alert">{error}</div>}
      <div className="checks">{checks.map(c=><div key={c.key} className={`check ${c.status}`}>
        <div><strong>{c.label}</strong><span className={`status-pill ${c.status}`}><span className="dot"/>{busy(c.key)?'Executando…':statusLabels[c.status]}</span></div>
        <pre>{c.detail}</pre>
        <small>{c.checked_at?`Verificado ${new Date(c.checked_at).toLocaleTimeString('pt-BR')}${c.ms!=null?` · ${c.ms} ms`:''}`:'Nunca verificado'}</small>
        <button className="ghost" disabled={running.length>0} onClick={()=>void run(c.key)}>Repetir</button>
      </div>)}</div>
    </article>
    {report&&<article className="panel"><h2>Configuração efetiva do backend</h2>
      <details><summary>Parâmetros de câmera e reconhecimento</summary><pre>{JSON.stringify(report.config,null,2)}</pre></details>
      <details><summary>Modelo UniFace</summary><pre>{JSON.stringify(report.model,null,2)}</pre></details>
      <details><summary>Leitores de câmera ativos ({report.readers.length})</summary><pre>{JSON.stringify(report.readers,null,2)}</pre></details>
    </article>}
  </>;
}
