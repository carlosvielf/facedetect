let credential = '';
export const setToken = (value: string) => { credential = value; };
const base = (import.meta as unknown as {env: Record<string, string>}).env.VITE_API_URL || '';
export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const multipart = body instanceof FormData;
  const response = await fetch(base + '/api' + path, {
    method, headers: {...(credential ? {Authorization: `Bearer ${credential}`} : {}), ...(!multipart && body ? {'Content-Type':'application/json'} : {})},
    body: body ? (multipart ? body : JSON.stringify(body)) : undefined,
  });
  if (!response.ok) {
    const text = await response.text();
    try { throw new Error(JSON.parse(text).detail || text); } catch (error) { throw error instanceof SyntaxError ? new Error(text) : error; }
  }
  return response.json() as Promise<T>;
}
export async function download(session: number, format = 'csv') {
  const response = await fetch(base + `/api/attendance/export?session_id=${session}&format=${format}`, {headers:{Authorization:`Bearer ${credential}`}});
  if (!response.ok) throw new Error('Falha ao exportar chamada');
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement('a'); link.href=url; link.download=`chamada-${session}.${format}`; link.click(); URL.revokeObjectURL(url);
}
export async function frame(session: number) {
  const response = await fetch(base + `/api/attendance/sessions/${session}/frame`, {headers:{Authorization:`Bearer ${credential}`}});
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(data?.detail || 'Câmera indisponível');
  }
  return URL.createObjectURL(await response.blob());
}
export const getToken = () => credential;
// ws(s)://… for the backend: VITE_API_URL when set (https -> wss), else the page origin via the Vite proxy.
export function wsUrl(path: string) {
  const origin = base || window.location.origin;
  return origin.replace(/^http/, 'ws') + '/api' + path;
}
export async function blob(path: string, method = 'GET') {
  const response = await fetch(base + '/api' + path, {method, headers:{Authorization:`Bearer ${credential}`}});
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw new Error(data?.detail || `Falha HTTP ${response.status}`);
  }
  return {data: await response.blob(), headers: response.headers};
}
