# Diagnóstico técnico — Facial Attendance

Data: 27/10/2026. Ambiente: Windows 11 Pro (10.0.26300), computador em `10.64.69.170/16` (Wi-Fi "BDAG", perfil Público).

**Resumo:** backend, banco, UniFace e frontend foram validados localmente, inclusive com um fluxo de chamada de ponta a ponta (16/16 verificações). **A câmera física não respondeu durante este trabalho**, portanto nenhum item que dependa da ESP32 real está aprovado. Também foi encontrado um problema grave de dados: **as 4 biometrias cadastradas não podem ser descriptografadas com a `EMBEDDING_KEY` atual** (seção 3, erro 1).

## 1. Versões

| Componente | Versão |
|---|---|
| Python (`.venv`) | 3.11.9 — a API em execução na porta 8000 usa o `.venv` (o processo filho `pythoncore-3.11` é o redirecionador normal do venv no Windows) |
| FastAPI / Starlette / Uvicorn | 0.156.0 / 1.9.0 / 0.54.0 |
| SQLAlchemy / Alembic / psycopg | 2.1.5 / 1.20.0 / 3.3.6 |
| UniFace / ONNX Runtime | 4.1.0 / 1.31.0 (providers: `CPUExecutionProvider`, `AzureExecutionProvider`; **sem GPU/CUDA**) |
| OpenCV / NumPy / scikit-image | 5.0.0.93 / 2.4.6 / 0.26.0 |
| Modelos | `~/.uniface/models/scrfd_500m.onnx` (2,5 MB), `arcface_mnet.onnx` (13,6 MB), já baixados |
| PostgreSQL | 16.15 (Docker `postgres:16-alpine`, healthy), migração `0001 (head)` |
| Node / npm | 24.20.0 / 11.19.0 |
| React / Vite / TypeScript / Tailwind | 19.3.1 / 6.4.4 / 5.7.3 / 4.3.3 |
| Docker | 29.9.1 |
| Arduino core esp32 | 3.3.12; FQBN do último upload: `esp32s3`, PSRAM=opi, FlashSize=8M, CPUFreq=240, DebugLevel=none |

## 2. Estado real de cada componente

| Componente | Estado | Evidência |
|---|---|---|
| Firmware: pinagem | **Conferido** | `CAMERA_MODEL_ESP32S3_EYE` = pinagem Freenove ESP32-S3-WROOM CAM; GPIOs mantidos |
| Firmware: compilação | **OK** | compila com o FQBN original (1.003.518 B) |
| Firmware: em execução na placa | **Não testado** | `10.64.70.86` sem resposta a ping, `:80` e `:81` (timeout) |
| PSRAM / sensor OV2640 | **Não testado** | depende do monitor serial; o firmware corrigido agora imprime ambos |
| Captura HTTP / stream MJPEG reais | **Não testado** | câmera inacessível |
| Protocolo de captura/stream do backend | **OK** | testes automatizados + simulador com o mesmo protocolo do firmware |
| UniFace (importação, modelos, embeddings) | **OK** | Testes A, B, C, E, F executados com imagens reais de domínio público |
| Reconhecimento de aluno real (G) | **Não testado** | não há foto autorizada de aluno disponível; as biometrias atuais estão ilegíveis |
| Pessoa desconhecida (H) | **OK no E2E** / não testado no banco real | E2E: similaridade 0,029 → "desconhecido", não marcado; banco real: sem embeddings utilizáveis |
| PostgreSQL + migrações | **OK** | `0001 (head)`; migração executada do zero em banco temporário; restrição `UNIQUE(session_id, student_id)` presente |
| Fluxo de chamada completo | **OK com simulador** | 16/16 (seção 5) |
| Frontend (tipos + build) | **OK** | `tsc --noEmit && vite build` |
| Frontend no navegador | **Parcial** | página, REST e WebSocket testados pelo proxy do Vite; renderização visual e responsividade **não** foram vistas em um navegador |

## 3. Erros identificados, causas e correções

| # | Erro | Causa | Correção |
|---|---|---|---|
| 1 | **Biometrias cadastradas ilegíveis**: as 4 linhas de `face_embeddings` (20/10) geram `InvalidToken`. Todo ciclo de reconhecimento de uma chamada em andamento falhava, então ninguém seria reconhecido mesmo com a câmera funcionando. | O `.env` da raiz foi reescrito em 27/10 14:50 com outra `EMBEDDING_KEY`. O `backend/.env`, onde o README mandava copiar o arquivo, não existe mais. | **Os dados não foram apagados.** Agora embeddings ilegíveis são ignorados e registrados no log em vez de derrubar o ciclo (`recognition.usable`), e o indicador "Embeddings cadastrados" do Diagnóstico mostra a contagem. **Ação sua:** restaurar a chave antiga, se existir, ou excluir e recadastrar as 4 biometrias (Alunos → Gerenciar). |
| 2 | API dependia de `backend/.env` copiado manualmente | `env_file='.env'` era relativo ao diretório atual; o README já pedia para manter duas cópias em sincronia | `config.py` lê `.env` da raiz por caminho absoluto (`backend/.env` ainda tem precedência, se existir) |
| 3 | Rostos bons descartados | O gate fixo `confidence >= 0.8`, mas o SCRFD dá **0,783** a um rosto frontal nítido | Configurável: `MIN_DETECTION_CONFIDENCE=0.6` |
| 4 | Rostos pequenos demais | Firmware em **QVGA 320×240**; mínimo fixo de 80 px; um rosto a 1–2 m fica com cerca de 50 px | Firmware em VGA quando há PSRAM; seletor de resolução no monitor; `MIN_FACE_SIZE=64` ao vivo e `ENROLL_MIN_FACE_SIZE=80` no cadastro |
| 5 | Frames atrasados em `/capture` | `fb_count=1` + `CAMERA_GRAB_WHEN_EMPTY` entrega o frame capturado após a requisição anterior | 2 buffers + `CAMERA_GRAB_LATEST` com PSRAM |
| 6 | Reinício em ciclo se a câmera não iniciar | `esp_camera_sensor_get()` nulo seguido de `s->id.PID` | `camera_init()` retorna `false` e registra a falha |
| 7 | Serial sem diagnóstico | Core Debug Level `None` silencia `log_e`; sem PSRAM/PID/IP de reconexão | Mensagens explícitas no `.ino`; documentado usar "Info" |
| 8 | Wi-Fi: espera infinita e queda silenciosa | Laço `while` sem limite | Limite de 30 s com reinício, `setAutoReconnect` e registro de queda e retorno |
| 9 | Credenciais Wi-Fi no código | SSID e senha no `.ino` | `wifi_secrets.h` ignorado pelo git |
| 10 | Vários consumidores no sensor | Prévia e reconhecimento faziam requisições separadas; o stream :81 atende 1 cliente | `CameraHub`: 1 leitor por câmera, frame compartilhado, fallback stream → capture |
| 11 | JPEG truncado aceito | Só o `Content-Type` era verificado | `jpeg_size()` valida SOI/SOF/EOI; frames corrompidos são contados e descartados |
| 12 | Frame congelado processado repetidamente | Sem controle de sequência | `CaptureService` ignora frames com o mesmo `seq`; o leitor exige frame com idade ≤ 2×intervalo |
| 13 | Sem pré-visualização contínua | Só havia uma foto sob demanda dentro da chamada | Página **Monitoramento da Câmera** (WebSocket) |
| 14 | `/api/health` era o único indicador | — | `/api/live`, `/api/ready` e página **Diagnóstico do Sistema** |
| 15 | README mandava copiar um `.env.example` inexistente | Arquivo ausente na raiz | `.env.example` criado com todas as variáveis |
| 16 | `frontend/.env.example` com `VITE_API_URL=http://localhost:8000` | Ignora o proxy e causa erro de CORS quando o painel é aberto em `127.0.0.1:5173` | Vazio em desenvolvimento; `CORS_ORIGINS` de exemplo inclui as duas origens |
| 17 | Vite sem proxy de WebSocket | Não havia WebSocket antes | `ws: true` no proxy |
| 18 | README com linhas corrompidas | Texto UTF-16 anexado (`#   f a c e d e t e c t`) | Removidas |
| 19 | Aviso de depreciação do Alembic 1.20 | Falta `path_separator` | Adicionado em `alembic.ini` |

Itens verificados **sem** problema: ordem de canais (o OpenCV decodifica em BGR, que é o formato esperado por `FaceAnalyzer.analyze`); embeddings 512-d já normalizados; serialização do histórico e das observações; CORS em produção (lista explícita); autorização por turma; unicidade aluno/chamada no PostgreSQL; nenhum vazamento de conexão (`with SessionLocal()` em todos os caminhos).

## 4. Arquivos modificados

Novos: `backend/app/camera.py`, `backend/app/monitor.py`, `backend/app/diagnostics.py`, `backend/scripts/{diagnose_camera,diagnose_uniface,calibrate,fake_esp32}.py`, `backend/tests/test_camera_monitor.py`, `backend/tests/test_uniface_models.py`, `frontend/src/CameraMonitor.tsx`, `frontend/src/Diagnostics.tsx`, `firmware/freenove-camera/Sketch_07_1_CameraWebServer/*`, `.env.example`, `DIAGNOSTICO.md`.

Alterados: `backend/app/{config,recognition,capture,main,security}.py`, `backend/alembic.ini`, `backend/tests/{conftest,test_capture}.py`, `frontend/src/{main.tsx,api.ts,style.css}`, `frontend/vite.config.ts`, `frontend/.env.example`, `firmware/freenove-camera/README.md`, `README.md`, `.gitignore`.

Não alterados: modelos e migração (o esquema já tinha a restrição única), `docker-compose.yml`, o banco `attendance`, a pasta `Documents\Arduino` e a API que estava em execução na porta 8000. Essa API ainda roda o código **antigo**: reinicie-a para carregar as mudanças.

## 5. Testes executados e resultados

**Automatizados** — `pytest`: **26 passaram** (11 originais + 15 novos). Cobrem: parser MJPEG com pedaços fragmentados; rejeição de JPEG truncado; leitor único compartilhado; contagem de frames corrompidos; fallback stream → capture; encerramento sem consumidores; estados da sobreposição; ocultação de identidade fora do modo reconhecimento; WebSocket sem token rejeitado; reconhecimento negado sem chamada em andamento e para professor de outra turma; teste de conexão por etapas; snapshot; diagnósticos começando em "não testado"; `/api/ready` falhando sem migração; embedding de chave perdida ignorado; e inferência UniFace real (detecção, embedding, similaridade, desconhecido). Os 2 testes originais de captura passaram a usar um JPEG real, porque `b'jpeg-content'` agora é rejeitado de propósito.

**Build do frontend:** `tsc --noEmit && vite build` OK.

**UniFace (`scripts/diagnose_uniface.py`)**, com imagens de domínio público (retrato NASA e "cameraman" do scikit-image):

| Teste | Resultado |
|---|---|
| A Importação | OK — `.venv` correto, uniface 4.1.0, ORT 1.31.0 |
| B Inicialização | OK — pesos em cache, carga em < 0,2 s |
| C Imagem estática | OK — 1 rosto, detecção 0,783, cerca de 120 ms na 1ª inferência e 13–17 ms nas seguintes (CPU) |
| D Câmera ESP32 | **ERRO / não testado** — timeout em 10.64.70.86 |
| E Embeddings | OK — 512-d, finito, norma 1 |
| F Comparação | OK — mesma pessoa (512 px × 320×240) 0,864; pessoas diferentes 0,019 |
| G Aluno cadastrado | **Não testado** — sem foto autorizada; biometrias ilegíveis |
| H Desconhecido | Banco real: **não testado** (0 embeddings utilizáveis); E2E: OK |

**Fim a fim (backend real + PostgreSQL em banco temporário `attendance_e2e`, removido depois + simulador do protocolo da ESP32): 16/16.** Login do professor; turma; teste de conexão (status/capture/stream); captura JPEG 640×480; pré-visualização (42 frames em 4 s, **0 inferências**); detecção sem nomes (cerca de 13–16 ms por inferência, limitada a 5 FPS); reconhecimento bloqueado antes de iniciar a chamada; aluna cadastrada reconhecida (similaridade 0,96); registro provisório criado após 3 observações e **um único registro**; pessoa desconhecida rotulada "desconhecido" (0,029) e **não** marcada; finalização bloqueada com provisórios; professor confirma e corrige; chamada finalizada; histórico e CSV corretos; diagnóstico com API, banco, câmera, serviço de captura, UniFace, modelos, embeddings e processamento "ok"; `/api/ready` 200. Também passaram REST e WebSocket através do proxy do Vite.

**Desconexão:** com a câmera simulada desligada durante a prévia, o erro aparece em cerca de 1 s com mensagem compreensível e o backoff fica limitado (2 tentativas em 14 s). Religada, os frames voltam em cerca de 1 s via `/capture` e o stream é retomado após 30 s. Ao fechar o cliente, o leitor encerra.

Observação honesta: o simulador reproduz o **protocolo** do CameraWebServer, não o hardware. Latência de Wi-Fi, brownout, PSRAM e qualidade do sensor só podem ser validados com a placa.

## 6. Critérios de aceitação

| Critério | Situação |
|---|---|
| Firmware compatível com a placa | Pinagem e opções conferidas; compila. **Execução não testada** |
| Captura HTTP retorna JPEG válido | **Não testado no hardware** (OK com simulador) |
| Pré-visualização com frames reais | **Não testado no hardware** (OK com simulador) |
| UniFace inicializa os modelos | **OK** |
| Detecção em imagens reais | **OK** (fotografias reais de domínio público); câmera real não testada |
| Extração de embeddings | **OK** |
| Reconhece casos autorizados | OK no E2E com imagem pública; **aluno real não testado** |
| Desconhecido não marcado presente | OK no E2E; banco real não testado |
| Banco persiste registros | **OK** (PostgreSQL) |
| Frontend atualiza a lista | API verificada no E2E; **interface não observada em navegador** |
| Professor confirma e encerra | **OK** (API) |
| Trata desconexões | **OK** com simulador; hardware não testado |

O sistema **não** está validado para uso real enquanto a câmera física, o Teste G com alunos reais e a calibração do limiar não forem executados.

## 7. O que preciso que você faça com a ESP32

1. Ligue a placa pelo USB e abra o Monitor Serial do Arduino IDE (115200). Pressione RESET e copie as linhas impressas. Os pontos essenciais são `Camera Ready! Use 'http://IP'` e, após regravar o firmware corrigido, `PSRAM detectada` e `Sensor PID`.
2. Se o IP for diferente de `10.64.70.86`, atualize `CAMERA_HOSTS` no `.env` e o cadastro da câmera.
3. Com o computador na **mesma rede** da ESP32, rode `scripts\diagnose_camera.py --host IP --detect` e me envie a saída. Se o IP estiver certo e mesmo assim não houver resposta, a rede "BDAG" provavelmente isola clientes Wi-Fi entre si (comum em redes institucionais). Nesse caso, use um roteador ou hotspot dedicado para a câmera e o computador.
4. Para o Teste G: uma foto autorizada de um aluno já recadastrado (`diagnose_uniface.py --photo ... --registration ...`).
5. Para calibrar: fotos autorizadas de várias pessoas tiradas pela própria ESP32 na sala (`calibrate.py`).

## 8. Limitações ainda existentes

- Hardware, PSRAM, sensor, Wi-Fi e alimentação reais: não verificados.
- `THRESHOLD`, `UNCERTAIN_BAND` e os filtros de qualidade são valores iniciais **sem calibração** no ambiente real.
- Sem detecção de vivacidade (anti-spoofing).
- Sem GPU disponível neste computador (somente CPU, suficiente para cerca de 5 FPS de inferência).
- Um único processo de API (cada processo teria seu próprio leitor de câmera).
- Hospedagem com frontend na Vercel exige expor o backend local por um túnel HTTPS autenticado; não foi implantado.
- A interface nova não foi inspecionada visualmente em navegador; o build e os tipos passam, e o protocolo foi testado por scripts.
