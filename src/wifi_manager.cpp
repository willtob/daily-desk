#include <Arduino.h>
#include <WiFi.h>
#include "wifi_manager.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

/* SSID/password live in wifi_credentials.h, which is gitignored. */
#include "wifi_credentials.h"
#define WIFI_TIMEOUT_MS  30000

volatile wifi_status_t wifi_conn_status = WIFI_IDLE;
char wifi_ip_str[20] = "0.0.0.0";

static const char *wl_status_str(wl_status_t s)
{
    switch (s) {
        case WL_IDLE_STATUS:     return "IDLE";
        case WL_NO_SSID_AVAIL:   return "NO_SSID_AVAIL";
        case WL_SCAN_COMPLETED:  return "SCAN_COMPLETED";
        case WL_CONNECTED:       return "CONNECTED";
        case WL_CONNECT_FAILED:  return "CONNECT_FAILED (wrong password?)";
        case WL_CONNECTION_LOST: return "CONNECTION_LOST";
        case WL_DISCONNECTED:    return "DISCONNECTED";
        default:                 return "UNKNOWN";
    }
}

static void wifi_task(void *arg)
{
    wifi_conn_status = WIFI_CONNECTING;
    Serial.printf("[WiFi] Connecting to \"%s\"...\n", WIFI_SSID);

    WiFi.mode(WIFI_STA);
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

    uint32_t start = millis();
    while (WiFi.status() != WL_CONNECTED) {
        wl_status_t s = WiFi.status();
        if (millis() - start > WIFI_TIMEOUT_MS) {
            wifi_conn_status = WIFI_FAILED;
            Serial.printf("[WiFi] Timed out — last status: %s\n", wl_status_str(s));
            vTaskDelete(NULL);
            return;
        }
        if (s == WL_CONNECT_FAILED || s == WL_NO_SSID_AVAIL) {
            wifi_conn_status = WIFI_FAILED;
            Serial.printf("[WiFi] Fatal error: %s\n", wl_status_str(s));
            vTaskDelete(NULL);
            return;
        }
        vTaskDelay(pdMS_TO_TICKS(500));
    }

    wifi_conn_status = WIFI_CONNECTED;
    strncpy(wifi_ip_str, WiFi.localIP().toString().c_str(), sizeof(wifi_ip_str) - 1);
    wifi_ip_str[sizeof(wifi_ip_str) - 1] = '\0';
    Serial.printf("[WiFi] Connected — IP: %s\n", wifi_ip_str);

    vTaskDelete(NULL);
}

void wifi_manager_init(void)
{
    xTaskCreatePinnedToCore(wifi_task, "wifi_task", 4096, NULL, 1, NULL, 0);
}
