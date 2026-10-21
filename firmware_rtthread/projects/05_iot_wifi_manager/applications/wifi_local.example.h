#ifndef WIFI_LOCAL_H
#define WIFI_LOCAL_H

/* Copy to wifi_local.h locally. Never commit wifi_local.h. */
#define WIFI_LOCAL_SSID "your-home-2.4ghz-ssid"
#define WIFI_LOCAL_PASSWORD "your-password"
/* Set these to the actual AP security and 2.4 GHz channel. */
#define WIFI_LOCAL_SECURITY SECURITY_WPA2_AES_PSK
#define WIFI_LOCAL_CHANNEL 2
/* 0 for a visible SSID; set 1 only when the AP hides its name. */
#define WIFI_LOCAL_HIDDEN 0

#endif
