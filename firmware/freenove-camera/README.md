# Firmware Freenove existente

Esta pasta estava vazia na inspeção inicial. Nenhum dos arquivos originais foi modificado ou substituído.

Arquivos aguardados: `Sketch_07.1_CameraWebServer.ino`, `app_httpd.cpp`, `board_config.h`, `camera_pins.h`, `camera_index.h`, `partitions.csv`.

Preserve `CAMERA_MODEL_ESP32S3_EYE` e os pinos existentes. A nomenclatura do módulo ESP32-S3-WROOM, sozinha, não comprova a correspondência do pinout: os arquivos e a revisão da placa precisam ser analisados antes de compilar qualquer mudança.

No Arduino IDE, use o pacote ESP32 compatível com o projeto original e reproduza as configurações originais de placa, PSRAM, flash e partições. Não há informação suficiente para fixar essas opções sem analisar o firmware. Credenciais Wi-Fi não devem ser versionadas.

Com o firmware original em execução e o IP visto no monitor serial, teste a partir do computador do backend:

```powershell
Invoke-WebRequest -Uri 'http://IP_REAL/capture' -OutFile "$env:TEMP/esp32-validation.jpg" -TimeoutSec 5
```

Confirme que a imagem é um JPEG válido; exclua a foto de validação depois, caso contenha pessoas. Configure o IP em `CAMERA_HOSTS` e cadastre a câmera na aplicação. O streaming original permanece independente; o backend usa fotografias do `/capture` e faz a inferência no computador.
