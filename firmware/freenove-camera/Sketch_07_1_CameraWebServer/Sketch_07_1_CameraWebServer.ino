/**********************************************************************
  Filename    : Camera Web Server
  Description : The camera images captured by the ESP32S3 are displayed on the web page.
  Auther      : www.freenove.com
  Modification: 2026/05/16
  Facial Attendance changes (2026/10/27):
    - Wi-Fi credentials moved to wifi_secrets.h (not versioned).
    - camera_init() no longer dereferences a NULL sensor after a failed init.
    - PSRAM is checked: 2 buffers + GRAB_LATEST (fresh /capture frames) and VGA
      when present; safe QVGA/DRAM fallback when missing.
    - Serial diagnostics: PSRAM, sensor PID, IP, RSSI, Wi-Fi reconnects.
  GPIOs were NOT changed: CAMERA_MODEL_ESP32S3_EYE matches the Freenove
  ESP32-S3-WROOM CAM board pinout.
**********************************************************************/
#include "esp_camera.h"
#include <WiFi.h>
#include "board_config.h"
// ===================
// Select camera model
// ===================
//#define CAMERA_MODEL_WROVER_KIT // Has PSRAM
//#define CAMERA_MODEL_ESP_EYE // Has PSRAM
#define CAMERA_MODEL_ESP32S3_EYE // Has PSRAM
//#define CAMERA_MODEL_M5STACK_PSRAM // Has PSRAM
//#define CAMERA_MODEL_M5STACK_V2_PSRAM // M5Camera version B Has PSRAM
//#define CAMERA_MODEL_M5STACK_WIDE // Has PSRAM
//#define CAMERA_MODEL_M5STACK_ESP32CAM // No PSRAM
//#define CAMERA_MODEL_M5STACK_UNITCAM // No PSRAM
//#define CAMERA_MODEL_AI_THINKER // Has PSRAM
//#define CAMERA_MODEL_TTGO_T_JOURNAL // No PSRAM
// ** Espressif Internal Boards **
//#define CAMERA_MODEL_ESP32_CAM_BOARD
//#define CAMERA_MODEL_ESP32S2_CAM_BOARD
//#define CAMERA_MODEL_ESP32S3_CAM_LCD

#include "camera_pins.h"

// ===========================
// WiFi credentials: copy wifi_secrets.example.h to wifi_secrets.h
// ===========================
#include "wifi_secrets.h"
const char* ssid     = WIFI_SSID;
const char* password = WIFI_PASSWORD;
camera_config_t config;

void startCameraServer();
bool camera_init();

void setup() {
  Serial.begin(115200);
  Serial.setDebugOutput(true);
  Serial.println();

  if (psramFound()) {
    Serial.printf("PSRAM detectada: %u bytes livres\n", ESP.getFreePsram());
  } else {
    Serial.println("PSRAM NAO detectada. Arduino IDE: Tools > PSRAM > OPI PSRAM (Freenove ESP32-S3 N8R8).");
  }

  if (!camera_init()) {
    Serial.println("ERRO: camera nao inicializada. Confira o cabo flat, o modelo da placa e a PSRAM. /capture respondera 500.");
  }

  WiFi.begin(ssid, password);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);

  unsigned long started = millis();
  while (WiFi.status() != WL_CONNECTED || WiFi.STA.hasIP() != true) {
    delay(500);
    Serial.print(".");
    if (millis() - started > 30000) {
      Serial.println("\nWi-Fi nao conectou em 30 s (SSID/senha, rede 2.4 GHz?). Reiniciando.");
      ESP.restart();
    }
  }
  Serial.println("");
  Serial.println("WiFi connected");
  Serial.printf("RSSI: %d dBm  MAC: %s\n", WiFi.RSSI(), WiFi.macAddress().c_str());

  startCameraServer();

  Serial.print("Camera Ready! Use 'http://");
  Serial.print(WiFi.localIP());
  Serial.println("' to connect");
  Serial.print("Captura: http://");
  Serial.print(WiFi.localIP());
  Serial.println("/capture   Stream: porta 81, /stream");
}

void loop() {
  // The web server runs in its own tasks; this only reports Wi-Fi health.
  static bool wasConnected = true;
  bool connected = WiFi.status() == WL_CONNECTED;
  if (connected != wasConnected) {
    if (connected) {
      Serial.print("Wi-Fi reconectado. IP: ");
      Serial.println(WiFi.localIP());
    } else {
      Serial.println("Wi-Fi desconectado; tentando reconectar.");
    }
    wasConnected = connected;
  }
  delay(5000);
}

bool camera_init() {
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 10000000;
  config.pixel_format = PIXFORMAT_JPEG; // for streaming
  config.jpeg_quality = 10;
  if (psramFound()) {
    // VGA keeps faces large enough for recognition; two buffers with
    // GRAB_LATEST make /capture return a current frame instead of a stale one.
    config.frame_size = FRAMESIZE_VGA;
    config.fb_location = CAMERA_FB_IN_PSRAM;
    config.fb_count = 2;
    config.grab_mode = CAMERA_GRAB_LATEST;
  } else {
    config.frame_size = FRAMESIZE_QVGA;
    config.fb_location = CAMERA_FB_IN_DRAM;
    config.fb_count = 1;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  }

  // camera init
  esp_err_t err = esp_camera_init(&config);
  if (err == ESP_ERR_NOT_SUPPORTED) {
    config.pixel_format = PIXFORMAT_RGB565;
    err = esp_camera_init(&config);
  }
  if (err != ESP_OK) {
    Serial.printf("Camera init failed with error 0x%x\n", err);
    return false;
  }

  sensor_t * s = esp_camera_sensor_get();
  if (s == NULL) {
    Serial.println("Sensor da camera nao encontrado.");
    return false;
  }
  uint16_t pid = s->id.PID;
  Serial.printf("Sensor PID: 0x%04x (OV2640=0x%04x, OV3660=0x%04x)\n", pid, OV2640_PID, OV3660_PID);
  if(pid == OV2640_PID){
    s->set_hmirror(s, 1);
    s->set_vflip(s, 1);
  }
  else if(pid == OV3660_PID){
    s->set_hmirror(s, 1);
    s->set_vflip(s, 0);
  }
  else if(pid == GC2145_PID){
    s->set_hmirror(s, 0);
    delay(500);
    s->set_vflip(s, 0);
  }
  else if(pid == GC0308_PID){
    s->set_hmirror(s, 0);
    delay(500);
    s->set_vflip(s, 0);
  }
  else{
    s->set_hmirror(s, 1);
    s->set_vflip(s, 0);
  }
  s->set_brightness(s, 1);  // Slightly increase brightness
  s->set_saturation(s, 0);  // Reduce saturation
  s->set_ae_level(s, -3);   // Set exposure compensation level
  return true;
}
