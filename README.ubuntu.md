# Executar no Ubuntu

Guia para instalar e iniciar o Facial Attendance no Ubuntu **24.04 LTS** ou **22.04 LTS**, em desktop ou notebook. A arquitetura, o fluxo de uso e os limites do sistema estão no [README.md](README.md). O estado validado está em [DIAGNOSTICO.md](DIAGNOSTICO.md).

O computador precisa estar na **mesma rede** da ESP32-S3: o backend busca as imagens diretamente na câmera.

## 1. Pacotes do sistema

```bash
sudo apt update
sudo apt install -y git curl ca-certificates libgl1 libglib2.0-0
```

`libgl1` e `libglib2.0-0` são exigidas pelo `opencv-python` (dependência do UniFace). Sem elas, a importação falha com `libGL.so.1: cannot open shared object file`.

### Python 3.11 ou 3.12

O UniFace 4.1.0 com ONNX Runtime atual exige Python ≥ 3.11.

**Ubuntu 24.04** (já vem com o 3.12):

```bash
sudo apt install -y python3.12 python3.12-venv
PY=python3.12
```

**Ubuntu 22.04** (vem com o 3.10; instale o 3.11 pelo PPA deadsnakes):

```bash
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.11 python3.11-venv
PY=python3.11
```

### Docker Engine + Compose

Use o repositório oficial do Docker (o pacote `docker.io` do Ubuntu não traz o plugin `docker compose`):

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker $USER
```

Encerre a sessão e entre de novo (ou rode `newgrp docker`) para usar o Docker sem `sudo`. Confira com `docker run --rm hello-world`.

### Node.js LTS

Com o [nvm](https://github.com/nvm-sh/nvm), sem precisar de `sudo`:

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.3/install.sh | bash
source ~/.bashrc
nvm install --lts
node --version && npm --version
```

## 2. Obter o projeto

```bash
git clone <URL_DO_REPOSITORIO> facial-attendance
cd facial-attendance
```

**Se você copiou a pasta de um computador Windows**, apague os ambientes gerados lá, porque contêm binários do Windows:

```bash
rm -rf .venv frontend/node_modules frontend/dist
```

## 3. Configuração (`.env`)

```bash
cp .env.example .env
$PY -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r backend/requirements.txt
.venv/bin/python -c "from cryptography.fernet import Fernet; import secrets; print('EMBEDDING_KEY='+Fernet.generate_key().decode()); print('JWT_SECRET='+secrets.token_urlsafe(48))"
```

Edite `.env` (por exemplo, com `nano .env`):

- `EMBEDDING_KEY` e `JWT_SECRET`: valores gerados acima.
- `POSTGRES_PASSWORD` e a mesma senha dentro de `DATABASE_URL`. Use uma senha sem caracteres especiais de URL, ou codifique-os.
- `CAMERA_HOSTS`: IP da ESP32 mostrado no monitor serial, sem `http://`.

> **Migrando um banco existente?** Use a **mesma** `EMBEDDING_KEY` do computador de origem. Com outra chave, as biometrias cadastradas ficam ilegíveis (o Diagnóstico mostra isso em "Embeddings cadastrados").

Se o `.env` veio do Windows, remova as quebras de linha CRLF. Sem isso, o Docker Compose inclui um `\r` invisível no fim da senha do PostgreSQL:

```bash
sed -i 's/\r$//' .env
```

## 4. Banco de dados e administrador

```bash
docker compose up -d db
docker compose ps                     # aguarde "healthy"
cd backend
../.venv/bin/python -m alembic upgrade head
../.venv/bin/python -m app.bootstrap  # cria o primeiro administrador (interativo)
cd ..
```

O PostgreSQL fica acessível apenas em `127.0.0.1:5432`. Os dados ficam no volume Docker `postgres_data`.

## 5. Executar

Use dois terminais, ambos na raiz do projeto.

**Terminal 1 — API** (um único worker: cada processo teria seu próprio leitor de câmera):

```bash
docker compose up -d db
cd backend
../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Terminal 2 — frontend:**

```bash
cd frontend
npm install
npm run dev
```

Abra http://127.0.0.1:5173. O Vite encaminha `/api`, inclusive o WebSocket do monitor da câmera, para a API. Deixe `VITE_API_URL` vazio em desenvolvimento.

Endereços úteis:

- Documentação da API: http://127.0.0.1:8000/docs
- `/api/live`: o processo está ativo.
- `/api/ready`: banco migrado e UniFace importável.

Na primeira detecção, o UniFace baixa os pesos para `~/.uniface/models` (cerca de 16 MB, exige internet). Para levá-los de outro computador, copie essa pasta.

## 6. Verificar a instalação

```bash
cd backend
../.venv/bin/python -m pytest -q
../.venv/bin/python scripts/diagnose_uniface.py
../.venv/bin/python scripts/diagnose_camera.py --host IP_DA_ESP32 --detect
cd ../frontend && npm run build
```

Depois, no painel, entre como administrador e abra **Diagnóstico do Sistema → Executar todas**.

## 7. Câmera ESP32 no Linux

Teste a rede antes de abrir o sistema:

```bash
ping -c 3 IP_DA_ESP32
curl -s -o /tmp/esp32.jpg -w "%{http_code} %{content_type} %{size_download}\n" http://IP_DA_ESP32/capture
```

- Sem resposta: confira o IP no monitor serial e se o computador está na mesma rede. Redes institucionais costumam isolar clientes Wi-Fi entre si; nesse caso, use um roteador ou hotspot dedicado.
- O `ufw` bloqueia apenas conexões **de entrada** por padrão; o backend só faz conexões de saída para a câmera, então não é preciso liberar portas.
- Feche abas do navegador abertas em `http://IP:81/stream`: o stream da ESP32 atende um cliente por vez.

### Gravar o firmware pelo Ubuntu

1. Baixe o Arduino IDE 2 (AppImage) em https://www.arduino.cc/en/software. O AppImage precisa do FUSE 2:
   ```bash
   sudo apt install -y libfuse2t64   # 24.04
   sudo apt install -y libfuse2      # 22.04
   chmod +x arduino-ide_*.AppImage && ./arduino-ide_*.AppImage
   ```
2. Dê permissão de acesso à porta serial e entre de novo na sessão:
   ```bash
   sudo usermod -aG dialout $USER
   ```
3. Se a placa conecta e desconecta logo em seguida (comum no 22.04 com conversores USB-serial), o `brltty` está tomando a porta:
   ```bash
   sudo apt remove -y brltty
   ```
4. Instale o core **esp32** (Espressif) no Boards Manager e siga [firmware/freenove-camera/README.md](firmware/freenove-camera/README.md): placa **ESP32S3 Dev Module**, PSRAM **OPI PSRAM**, Flash **8MB**, porta `/dev/ttyACM0` ou `/dev/ttyUSB0`.

### Simulador (somente desenvolvimento)

`scripts/fake_esp32.py` imita o protocolo da câmera nas portas 80 e 81. No Linux, portas abaixo de 1024 exigem privilégio. Libere-as temporariamente (a configuração volta ao padrão no reboot):

```bash
sudo sysctl -w net.ipv4.ip_unprivileged_port_start=80
cd backend && ../.venv/bin/python scripts/fake_esp32.py --image /caminho/foto.jpg
```

Coloque `127.0.0.1` em `CAMERA_HOSTS` **somente** num `.env` de desenvolvimento.

## 8. (Opcional) API como serviço systemd

Inicia a API automaticamente no boot. Ajuste `User` e os caminhos:

```bash
sudo tee /etc/systemd/system/facial-attendance.service > /dev/null <<'EOF'
[Unit]
Description=Facial Attendance API
After=network-online.target docker.service
Wants=network-online.target docker.service

[Service]
User=SEU_USUARIO
WorkingDirectory=/home/SEU_USUARIO/facial-attendance/backend
ExecStartPre=/usr/bin/docker compose -f /home/SEU_USUARIO/facial-attendance/docker-compose.yml up -d db
ExecStart=/home/SEU_USUARIO/facial-attendance/.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now facial-attendance
journalctl -u facial-attendance -f      # logs
```

O usuário do serviço precisa estar no grupo `docker`. Para servir o frontend sem o Vite, gere `npm run build` e publique `frontend/dist` atrás de um proxy reverso (nginx/Caddy) que encaminhe `/api` e o WebSocket para `127.0.0.1:8000`.

## Problemas comuns

| Sintoma | Solução |
|---|---|
| `libGL.so.1: cannot open shared object file` | `sudo apt install libgl1 libglib2.0-0` |
| `No matching distribution found for onnxruntime` | Python antigo: use 3.11 ou 3.12 (seção 1) |
| `permission denied ... docker.sock` | `sudo usermod -aG docker $USER` e entre de novo na sessão |
| `password authentication failed` no Alembic | Senha diferente entre `POSTGRES_PASSWORD` e `DATABASE_URL`, ou `.env` com CRLF (`sed -i 's/\r$//' .env`). Se o volume foi criado com outra senha, ela continua valendo. |
| `ValidationError ... database_url` ao iniciar | Falta `.env` na raiz do projeto |
| Página abre mas `/api` falha | A API não está rodando na porta 8000, ou `VITE_API_URL` foi preenchido em desenvolvimento |
| `Permission denied: '/dev/ttyACM0'` no upload | Grupo `dialout` (seção 7) |
| Monitor mostra "Tempo esgotado ao conectar" | Rede ou IP da câmera (seção 7) |
