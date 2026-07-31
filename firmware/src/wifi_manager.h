#pragma once

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    WIFI_IDLE = 0,
    WIFI_CONNECTING,
    WIFI_CONNECTED,
    WIFI_FAILED,
} wifi_status_t;

extern volatile wifi_status_t wifi_conn_status;
extern char wifi_ip_str[20];

void wifi_manager_init(void);

#ifdef __cplusplus
}
#endif
