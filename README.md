# Presença Acadêmica — Facial Attendance

Chamada universitária assistida por reconhecimento facial: FastAPI, PostgreSQL, UniFace 4.1.0 (SCRFD 500M + ArcFace MobileNet), React/TypeScript/Vite e câmera ESP32-S3 Freenove. O reconhecimento gera apenas uma lista **provisória**; o professor confirma, corrige e finaliza.

O estado verificado de cada componente, os erros encontrados e o que ainda depende da câmera física estão em [DIAGNOSTICO.md](DIAGNOSTICO.md).

## Arquitetura

```
ESP32-S3 (CameraWebServer)          Backend local (Windows, 1 processo)                 Navegador
  :80  /capture /status /control  ◄── CameraHub: 1 leitor por câmera ──► WebSocket /api/cameras/{id}/ws
  :81  /stream (MJPEG, 1 cliente) ◄──   (frame compartilhado)          ──► REST /api/...
                                         ├─ pré-visualização (sem inferência)
                                         ├─ sobreposição: detecção/reconhecimento (só exibição)
                                         └─ CaptureService: chamadas em andamento → PostgreSQL
```

- A ESP32 só captura. Detecção, embeddings e comparação rodam somente no backend Python.
- Um único leitor por câmera alimenta todos os consumidores; nada abre conexões paralelas ao sensor (o stream da porta 81 atende um cliente por vez). Se o stream falhar, o leitor usa `/capture` por 30 s e tenta o stream de novo. O backoff de reconexão é limitado (1–10 s), e o leitor encerra 10 s depois que o último consumidor sai.
- A inferência roda em thread, sempre no frame mais recente, com teto de `INFERENCE_MAX_FPS`. Frames antigos são descartados, não enfileirados.
- A sobreposição do monitor **não** grava presença. Registros provisórios vêm apenas do `CaptureService`, depois de `OBSERVATIONS` observações espaçadas, com restrição única `(session_id, student_id)` no banco.

## Executar no Windows 11

No Ubuntu, siga [README.ubuntu.md](README.ubuntu.md).

Pré-requisitos: Python 3.11 (`py`), Node.js LTS, Docker Desktop, ESP32 na mesma rede do computador. Comandos em PowerShell, a partir da raiz do repositório.

Primeira instalação:

```powershell
Copy-Item .env.example .env
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; import secrets; print('EMBEDDING_KEY='+Fernet.generate_key().decode()); print('JWT_SECRET='+secrets.token_urlsafe(48))"
```

Edite `.env`: `EMBEDDING_KEY`, `JWT_SECRET`, `POSTGRES_PASSWORD` (e a mesma senha em `DATABASE_URL`) e `CAMERA_HOSTS` com o IP mostrado no monitor serial da ESP32. **Faça backup da `EMBEDDING_KEY`**: trocá-la torna as biometrias cadastradas ilegíveis. O backend lê `.env` da raiz a partir de qualquer pasta, e não é mais necessário copiá-lo para `backend/`.

```powershell
docker compose up -d db
Set-Location backend
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m app.bootstrap          # primeiro administrador (interativo)
```

Execução diária (dois terminais):

```powershell
# Terminal 1 — API (um único worker: cada processo teria seu próprio leitor de câmera)
docker compose up -d db
Set-Location backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```powershell
# Terminal 2 — frontend
Set-Location frontend
npm install
npm run dev
```

Abra http://127.0.0.1:5173. O Vite encaminha `/api`, inclusive o WebSocket, para a API local. Deixe `VITE_API_URL` vazio em desenvolvimento. API: http://127.0.0.1:8000/docs. Saúde: `/api/live` (processo), `/api/ready` (banco migrado e UniFace importável), `/api/health` (banco).

## Fluxo de uso

1. **Configurações** (admin): cadastre a câmera com o IP que estiver em `CAMERA_HOSTS`.
2. **Monitoramento da Câmera**: *Conectar câmera* (testa `/status`, `/capture` e `/stream`, mostrando cada etapa) → *Iniciar visualização* → confira enquadramento, resolução, FPS, latência e último frame. *Capturar imagem* baixa um JPEG. Os modos são:
   - **Pré-visualização**: só imagem, nenhuma inferência.
   - **Detecção facial**: retângulos e pontuação do detector, sem nomes.
   - **Reconhecimento facial**: exige uma chamada **em andamento**, desta câmera, de uma turma do professor. Mostra nome e similaridade (verde), "incerto" (âmbar), "desconhecido" (vermelho) e "rosto detectado" sem qualidade para comparar (azul).
3. **Nova chamada**: crie a sessão e clique em *Iniciar captura*. Alunos reconhecidos em `OBSERVATIONS` frames aparecem como provisórios, uma vez cada.
4. O professor confirma (*Presente*), rejeita ou marca manualmente qualquer aluno e então finaliza. A finalização é bloqueada enquanto houver provisórios. O histórico e as exportações CSV/XLSX/PDF usam os dados gravados.
5. **Diagnóstico do Sistema** (admin): indicadores independentes de API, banco, câmera, serviço de captura, UniFace, modelos, embeddings, processamento de frames, eventos de chamada e comunicação frontend/backend. Cada um só mostra "Funcionando" depois de executado com sucesso.

A pontuação de **detecção** indica a confiança de que há um rosto e **não** é probabilidade de identidade. A **similaridade** é o cosseno entre embeddings e precisa de calibração.

## Diagnóstico por linha de comando

```powershell
Set-Location backend
..\.venv\Scripts\python.exe scripts\diagnose_camera.py --host IP_DA_ESP32 --detect --save $env:TEMP\esp32.jpg
..\.venv\Scripts\python.exe scripts\diagnose_uniface.py --camera-host IP_DA_ESP32
..\.venv\Scripts\python.exe scripts\diagnose_uniface.py --photo C:\fotos\aluno.jpg --registration MATRICULA   # Teste G
..\.venv\Scripts\python.exe scripts\calibrate.py C:\fotos\calibracao        # limiar: FAR/FRR medidos
..\.venv\Scripts\python.exe -m pytest -q
```

`scripts\fake_esp32.py` imita o protocolo HTTP do CameraWebServer **somente para desenvolvimento** sem a placa. A aplicação nunca o usa, e ele não valida o hardware.

## Calibração

`THRESHOLD=0.55` é um ponto de partida, não uma garantia. Reúna fotos autorizadas de várias pessoas (pasta por pessoa, ≥ 2 fotos, idealmente da própria ESP32 na sala) e rode `calibrate.py`. Ele mostra as distribuições de similaridade genuína e impostora e o FAR/FRR para cada limiar, sem gravar nada. Escolha o limiar com folga acima do maior impostor e repita a calibração ao trocar câmera, resolução ou modelo. `UNCERTAIN_BAND` define a faixa abaixo do limiar exibida como "incerto".

## Firmware

Veja [firmware/freenove-camera/README.md](firmware/freenove-camera/README.md). Use resolução VGA ou maior para reconhecimento: em QVGA (320×240), rostos a 1–2 m ficam abaixo do tamanho mínimo e são apenas "detectados".

## Hospedagem

O frontend pode ir para a Vercel (`frontend` como raiz, `npm run build`, saída `dist`), mas **um site HTTPS público não alcança `192.168.x.x`/`10.x.x.x`** nem `localhost` do computador da sala. O backend continua no computador local, junto da câmera, e precisa de uma URL HTTPS pública e autenticada, por exemplo um túnel (Cloudflare Tunnel) ou um proxy reverso com TLS. Configure `VITE_API_URL` com essa URL (o WebSocket vira `wss://` automaticamente) e `CORS_ORIGINS` com o domínio exato da Vercel. A câmera e o PostgreSQL nunca devem ser expostos. A pré-visualização remota trafega JPEG pelo túnel; para turmas, prefira usar o monitor na rede local e limite `MONITOR_MAX_FPS`.

## Segurança, privacidade e limites

- Hash Argon2, JWT de duas horas (também exigido na primeira mensagem do WebSocket, nunca na URL), controle de acesso no servidor, CORS explícito, auditoria e embeddings cifrados com Fernet. Fotos de cadastro e frames não são gravados.
- Limite de requisições por IP em memória. Atrás de proxy, aplique limites no ingress.
- O HTTP da ESP32 não é cifrado na LAN; use rede isolada e IPs fixos, e nunca publique a câmera na internet.
- Não há detecção de vivacidade. Fotos e vídeos podem enganar o reconhecimento, e nenhum resultado deve ser apresentado como resistente a spoofing.
- Registre a base legal, a autorização institucional, os prazos de retenção e o procedimento de contestação antes de cadastrar pessoas. A presença manual funciona sem biometria.
- Um único processo de API. Escalar exige worker dedicado e travas distribuídas.
- Alguns pesos do UniFace têm licenças próprias; verifique-as antes de uso institucional.
