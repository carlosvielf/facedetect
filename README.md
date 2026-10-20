# Presença Acadêmica

Implementação inicial integrada para Windows 11: FastAPI, PostgreSQL, UniFace 4.1.0 e React/TypeScript/Vite/Tailwind. Não há dados demonstrativos nem reconhecimento simulado no aplicativo.

## Estado e análise inicial

O workspace recebido continha somente `firmware/freenove-camera/`, vazia. Os seis arquivos CameraWebServer mencionados no pedido não estavam disponíveis. Nenhum firmware foi alterado e nenhum pinout foi presumido. A análise da configuração `CAMERA_MODEL_ESP32S3_EYE`, compilação e teste da ESP32 dependem desses arquivos e do hardware.

Arquitetura: navegador → API autenticada → PostgreSQL. Um serviço assíncrono no processo da API busca JPEG em `http://HOST/capture`, com timeout, limite de tamanho e lista explícita de hosts. A inferência serializada roda em thread separada para não bloquear o loop HTTP. As imagens não são gravadas. A interface consulta resultados a cada três segundos; uma falha da câmera não encerra a sessão.

O adaptador usa a API oficial `FaceAnalyzer().analyze(imagem_bgr)`, `face.bbox`, `face.confidence` e `face.embedding`. Os defaults documentados são SCRFD 500M e ArcFace MobileNet. O modelo é identificado junto ao embedding para impedir comparação entre modelos diferentes. Referências consultadas:

- https://github.com/yakhyo/uniface
- https://github.com/yakhyo/uniface/blob/main/uniface/analyzer.py
- https://github.com/yakhyo/uniface/blob/main/pyproject.toml
- https://yakhyo.github.io/uniface/

CPU: `uniface[cpu]` e ONNX Runtime. GPU NVIDIA: substituir a dependência por `uniface[gpu]==4.1.0` e conferir a compatibilidade CUDA/cuDNN do ONNX Runtime. Não instalar simultaneamente as duas variantes de runtime. Os pesos são baixados na primeira inferência, exigindo internet. Alguns pesos têm licenças diferentes da biblioteca; verificar antes de uso institucional/comercial.

## Executar no Windows / VS Code

Pré-requisitos: Python **3.11 ou 3.12** com launcher `py`, Node.js LTS com npm, Docker Desktop com Compose, ESP32 na rede local. Todos os comandos abaixo são PowerShell, a partir da raiz, exceto onde indicado. Instalações dos runtimes são necessárias se os comandos não existirem.

1. Configure ambiente e dependências:

```powershell
Copy-Item .env.example .env
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; import secrets; print('EMBEDDING_KEY='+Fernet.generate_key().decode()); print('JWT_SECRET='+secrets.token_urlsafe(48))"
```

Edite `.env`: substitua `EMBEDDING_KEY`, `JWT_SECRET`, `POSTGRES_PASSWORD` e a senha correspondente em `DATABASE_URL`. Use senha URL-safe ou codifique caracteres especiais na URL. Configure `CAMERA_HOSTS` com o IP real, sem protocolo, porta ou caminho. Guarde a chave de criptografia fora do repositório e faça backup seguro: perdê-la inutiliza os embeddings. O exemplo não deve ser usado com seus placeholders.

2. Suba PostgreSQL e migre (a API lê `.env` a partir do diretório atual):

```powershell
docker compose up -d db
Copy-Item .env backend/.env
Set-Location backend
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m app.bootstrap
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Não usar múltiplos workers nesta versão: cada processo iniciaria seu próprio coletor. O administrador inicial é criado interativamente, sem senha padrão. Documentação da API: http://127.0.0.1:8000/docs. Saúde com conexão ao banco: http://127.0.0.1:8000/api/health. Após editar o `.env` da raiz, atualize também `backend/.env` e reinicie a API.

3. Em outro terminal, na raiz:

```powershell
Set-Location frontend
npm install
npm run dev
```

Abra http://localhost:5173. O Vite encaminha `/api` ao backend local. O token JWT fica apenas em memória; recarregar a página exige novo login.

## Fluxo funcional implementado

1. Administrador cadastra professores, alunos, turmas/disciplinas e câmera. O IP precisa estar também em `CAMERA_HOSTS`; não é enviado aos clientes ao listar câmeras.
2. Em Turmas, matricula alunos e atribui professor. Nesta etapa cada turma possui um professor responsável.
3. Em Alunos → Gerenciar, envia uma foto e referência à autorização institucional. Requer exatamente um rosto, confiança ≥ 0,8, tamanho mínimo de 80 px e verificação de nitidez. São heurísticas iniciais, não validação biométrica certificada. Novos uploads adicionam referências; para substituí-las, exclua a biometria anterior e envie novas fotos.
4. Professor vê apenas suas turmas e alunos. Cria chamada, inicia captura e acompanha as observações. A busca considera somente alunos ativos matriculados e embeddings do mesmo modelo.
5. Similaridade por cosseno com limiar e margem entre os dois melhores **alunos**, não entre fotos. Exige três observações separadas por intervalo, dentro de 30 segundos. Uma mesma imagem não conta duas vezes para o mesmo aluno. Não há persistência de contagem temporal entre reinícios.
6. A identificação gera registro provisório. O professor confirma, rejeita ou marca manualmente todos os integrantes da turma. Para corrigir um nome, rejeita o candidato incorreto e marca o aluno correto. Uma restrição de unicidade impede duplicação aluno/chamada. Registros revisados não são sobrescritos pelo reconhecimento.
7. A finalização bloqueia novas alterações e exige revisão dos provisórios. Alunos sem marcação permanecem explicitamente `unmarked`, sem ausência automática. Histórico e exportação CSV/Excel/PDF usam os dados reais. A API do histórico aceita `class_id`, `professor_id`, `student_id`, `status`, `after` e `before` (datas ISO 8601).

Reiniciar o servidor pausa sessões em andamento. Câmeras podem apresentar `idle`, `connected` ou `error`; `connected` significa última captura e inferência bem-sucedidas, não monitoramento contínuo. A primeira carga de modelos pode demorar.

## Testes e validação

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m pytest -q
Set-Location ../frontend
npm run build
```

Os testes usam banco SQLite isolado e vetores sintéticos **somente nos testes** para verificar autorização, imutabilidade após finalização, revisão manual, unicidade, confirmação temporal, incerteza, criptografia, allowlist e exportações. Não provam a precisão do modelo nem o comportamento concorrente específico do PostgreSQL. Não utilizam dados biométricos reais.

Validação real ainda necessária: executar migração em PostgreSQL; testar o `/capture` da ESP32; enviar fotos autorizadas; aguardar download dos pesos; avaliar falsos positivos/negativos na sala real; testar desconexão e reconexão; confirmar a lista como professor e comparar exportações. Nenhum teste de hardware ou de precisão foi realizado no ambiente inicial. Python não executava e Node/Docker não estavam no PATH durante a inspeção, portanto os testes automatizados e o build não foram considerados aprovados.

## Firmware

Consulte [firmware/freenove-camera/README.md](firmware/freenove-camera/README.md). Coloque ali os arquivos originais antes de qualquer mudança. O backend usa o CameraWebServer existente sem necessidade de implementar inferência no ESP32.

## Segurança, privacidade e limites

- Hash Argon2, JWT com expiração de duas horas, controle de acesso no servidor, CORS explícito, auditoria das alterações e embeddings criptografados com Fernet. Nenhuma foto de cadastro é retida.
- Limite de requisições por IP em memória, separado para login. É uma proteção local inicial; ambientes com proxy devem aplicar limites no ingress e configurar corretamente o IP confiável.
- A lista de hosts limita destinos de captura; utilize IPs fixos confiáveis em rede isolada. O HTTP da ESP32 não fornece criptografia em trânsito na LAN. Para rede não confiável, usar gateway/túnel autenticado. Nunca publique o ESP32 na internet.
- A API retorna somente dados necessários e não retorna embeddings. Administradores são responsáveis pelo cadastro biométrico. Professores acessam os eventos e registros de suas turmas.
- Não há detecção de vivacidade nesta etapa. Múltiplas observações **não** impedem apresentação de fotos/vídeos. UniFace oferece MiniFASNet, mas não foi integrado nem validado; nenhum resultado deve ser apresentado como resistente a spoofing.
- Não existe base legal presumida: registre a autorização institucional, estabeleça transparência, prazos de retenção e procedimento de contestação antes de cadastrar pessoas. A presença manual está disponível sem biometria. A retenção deve ser definida pela instituição; exclusão de embeddings está implementada, mas expurgo automático e gestão completa de solicitações ainda não estão.
- Esta primeira entrega usa `users` com papéis e atributos de curso/disciplina nas tabelas de alunos/turmas. As entidades normalizadas `professors`, `courses` e `subjects`, múltiplos professores por turma, remoção de matrícula, edição de turmas/professores, filtros visuais completos do histórico, rotação de chaves e gestão institucional de retenção são próximas etapas. Não é uma entrega institucional completa.
- O serviço mantém um coletor por processo; crescimento requer worker dedicado, filas e travas distribuídas. Deve haver calibração antes de qualquer uso operacional. O limiar 0,55 é um valor inicial configurável, não uma garantia de acurácia.

## Preparação para hospedagem

Frontend: importe `frontend` como diretório raiz na Vercel, configure `VITE_API_URL` com a URL HTTPS da API, build `npm run build`, saída `dist`. O frontend remoto não consegue alcançar o backend do computador pelo endereço `localhost`. Não houve deploy nesta entrega.

Backend: hospedar separadamente com acesso privado à rede da câmera (VPN/gateway), um worker, HTTPS no proxy, PostgreSQL com TLS e disco/backup criptografados. Configure `CORS_ORIGINS` com o domínio exato do painel; não exponha PostgreSQL, credenciais ou câmera. A criptografia dos embeddings não substitui proteção dos discos e backups que contêm cadastros e auditoria. Separe segredos do código e estabeleça retenção antes de publicar dados reais.
#   f a c e d e t e c t  
 #   f a c e d e t e c t  
 #   f a c e d e t e c t  
 #   f a c e d e t e c t  
 #   f a c e d e t e c t  
 