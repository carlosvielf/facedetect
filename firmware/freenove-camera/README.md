# Firmware Freenove ESP32-S3 (CameraWebServer)

`Sketch_07_1_CameraWebServer/` é o Sketch_07.1 da Freenove encontrado em `Documents\Arduino\Sketch_07_1_CameraWebServer`, com correções mínimas. `app_httpd.cpp`, `board_config.h`, `camera_pins.h`, `camera_index.h` e `partitions.csv` estão **idênticos** aos originais; só o `.ino` mudou.

## Verificado

- `CAMERA_MODEL_ESP32S3_EYE`: XCLK 15, SIOD 4, SIOC 5, Y2–Y9 = 11, 9, 8, 10, 12, 18, 17, 16, VSYNC 6, HREF 7, PCLK 13, sem PWDN/RESET. Corresponde à pinagem documentada da Freenove ESP32-S3-WROOM CAM. **Os GPIOs não foram alterados.**
- Rotas: `/`, `/status`, `/control`, `/capture`, `/bmp`, `/xclk`, `/reg`, `/greg`, `/pll` e `/resolution` na porta **80**; `/stream` na porta **81** (`server_port += 1`). O servidor de stream atende **um cliente por vez**.
- Último upload registrado no cache do Arduino IDE (20/10/2026): `esp32:esp32:esp32s3`, PSRAM **OPI**, Flash 8 MB, CPU 240 MHz, núcleo esp32 3.3.12, Core Debug Level **None**.
- A versão corrigida compila com essas mesmas opções (1.003.518 bytes, 30% do espaço de programa).

## Correções no `.ino`

1. `camera_init()` acessava `s->id.PID` mesmo quando `esp_camera_init` falhava com erro diferente de `ESP_ERR_NOT_SUPPORTED`. Com `esp_camera_sensor_get()` nulo, isso reinicia a placa em ciclo. Agora a falha é registrada e a placa segue sem câmera (`/capture` responde 500).
2. A PSRAM é verificada. Com PSRAM: **VGA**, 2 buffers e `CAMERA_GRAB_LATEST`, para que `/capture` devolva um frame atual. O original usava QVGA com 1 buffer e `GRAB_WHEN_EMPTY`, que entrega frames antigos e rostos pequenos demais. Sem PSRAM: QVGA em DRAM.
3. O Wi-Fi tem limite de 30 s antes de reiniciar, reconexão automática e registro de queda e retorno com IP.
4. O monitor serial mostra a PSRAM livre, o PID do sensor (OV2640 = 0x2642), o RSSI, o MAC e as URLs de captura e stream.
5. As credenciais Wi-Fi saíram do código para `wifi_secrets.h` (ignorado pelo git).

## Gravar

1. Copie `wifi_secrets.example.h` para `wifi_secrets.h` e preencha uma rede **2,4 GHz** que seja a mesma do computador do backend.
2. Arduino IDE → placa **ESP32S3 Dev Module**, PSRAM **OPI PSRAM**, Flash Size **8MB**, USB CDC On Boot conforme o seu cabo. Para diagnosticar, use **Core Debug Level: Info** (com "None", os `log_e` do servidor não aparecem).
3. Abra a pasta `Sketch_07_1_CameraWebServer` (o nome da pasta precisa coincidir com o do `.ino`) e faça o upload.
4. Monitor serial a 115200: procure `PSRAM detectada`, `Sensor PID: 0x2642` e `Camera Ready! Use 'http://IP'`.

## Testar a partir do computador do backend

```powershell
Set-Location backend
..\.venv\Scripts\python.exe scripts\diagnose_camera.py --host IP --detect
```

Ou no navegador: `http://IP/` (página da Freenove), `http://IP/capture` e `http://IP:81/stream`. Feche essas abas antes de usar o sistema, pois o stream aceita um cliente por vez.

## Problemas comuns

| Sintoma | Causa provável |
|---|---|
| Sem resposta / tempo esgotado | IP mudou (DHCP), rede diferente ou rede com isolamento de clientes (comum em Wi-Fi institucional). Reserve o IP no roteador ou use uma rede dedicada. |
| `/capture` HTTP 500 | Sensor não inicializou: cabo flat, PSRAM desabilitada ou modelo de placa errado. Veja o serial. |
| Stream congela ou o monitor alterna para `/capture` | Outro cliente aberto em `:81/stream`, sinal fraco ou alimentação instável (use uma fonte de 5 V/2 A e um cabo curto). |
| Reinícios (brownout) | Alimentação insuficiente durante a transmissão Wi-Fi. |
| Frames corrompidos no Diagnóstico | Wi-Fi com perdas ou JPEG maior que o buffer: reduza a resolução ou aumente `quality` (número maior = mais compressão). |
