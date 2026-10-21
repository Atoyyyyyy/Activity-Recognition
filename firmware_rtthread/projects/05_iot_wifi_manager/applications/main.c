#include <rtthread.h>
#include <rtdevice.h>
#include <wlan_mgnt.h>
#include <msh.h>
#include <drv_lcd.h>
#include <lwip/apps/mqtt.h>
#include <netdev.h>
#include "sensor_diag.h"
#include "mqtt_publisher.h"

/* Copy wifi_local.example.h to the ignored wifi_local.h on this machine. */
#if defined(__has_include)
# if __has_include("wifi_local.h")
#  include "wifi_local.h"
#  define HAVE_WIFI_LOCAL 1
# endif
#endif

static struct rt_semaphore scan_done;
static struct rt_semaphore association_done;
static struct rt_semaphore ip_ready;
static rt_bool_t associated;

#define WIFI_STATUS_REPORT_PERIOD_MS 10000U

static void wifi_status_report(void)
{
    static rt_bool_t initialized;
    static rt_bool_t last_connected;
    static rt_bool_t last_ready;
    static rt_bool_t last_link_up;
    static rt_tick_t last_report_ms;
    static rt_uint32_t state_transition_count;
    rt_tick_t now = rt_tick_get_millisecond();
    rt_bool_t connected = rt_wlan_is_connected() == RT_TRUE;
    rt_bool_t ready = rt_wlan_is_ready() == RT_TRUE;
    struct netdev *netdev = netdev_get_by_name("w0");
    rt_bool_t link_up = netdev != RT_NULL &&
                        (netdev->flags & NETDEV_FLAG_LINK_UP) != 0;
    rt_bool_t ip_assigned = netdev != RT_NULL &&
                            !ip_addr_isany(&netdev->ip_addr);
    rt_bool_t changed = !initialized || connected != last_connected ||
                        ready != last_ready || link_up != last_link_up;

    if (initialized && changed)
        state_transition_count++;

    if (!changed && (rt_tick_t)(now - last_report_ms) < WIFI_STATUS_REPORT_PERIOD_MS)
        return;

    if (netdev != RT_NULL)
    {
        char ip[IPADDR_STRLEN_MAX];
        char mask[IPADDR_STRLEN_MAX];
        char gateway[IPADDR_STRLEN_MAX];

        ipaddr_ntoa_r(&netdev->ip_addr, ip, sizeof(ip));
        ipaddr_ntoa_r(&netdev->netmask, mask, sizeof(mask));
        ipaddr_ntoa_r(&netdev->gw, gateway, sizeof(gateway));
        rt_kprintf("Wi-Fi[%u ms]: connected=%d ready=%d link=%d transitions=%u ip_assigned=%d ip=%s mask=%s gw=%s\n",
                   (unsigned int)now, connected, ready, link_up,
                   (unsigned int)state_transition_count, ip_assigned,
                   ip, mask, gateway);
    }
    else
    {
        rt_kprintf("Wi-Fi[%u ms]: connected=%d ready=%d link=unknown transitions=%u interface=w0-missing\n",
                   (unsigned int)now, connected, ready,
                   (unsigned int)state_transition_count);
    }

    if (initialized && (link_up != last_link_up))
        rt_kprintf("Wi-Fi[%u ms]: link %s\n", (unsigned int)now,
                   link_up ? "UP" : "DOWN");
    if (initialized && (ready != last_ready))
        rt_kprintf("Wi-Fi[%u ms]: network READY %s\n", (unsigned int)now,
                   ready ? "UP" : "DOWN");

    initialized = RT_TRUE;
    last_connected = connected;
    last_ready = ready;
    last_link_up = link_up;
    last_report_ms = now;
}

/* The display is driven from main, never from WLAN event callbacks. */
static void status_screen_update(void)
{
    static int last_sensor = -1;
    static int last_wifi = -1;
    static int last_mqtt = -1;
    int sensor_ready;
    int wifi_ready;
    int mqtt_ready;

    if (!lcd_is_ready())
        return;

    sensor_ready = sensor_diag_is_ready() == RT_TRUE;
    /* READY means that association and IP setup have both completed. */
    wifi_ready = rt_wlan_is_ready() == RT_TRUE;
    mqtt_ready = mqtt_publisher_is_connected() == RT_TRUE;
    lcd_set_color(WHITE, BLACK);

    if (last_sensor < 0)
    {
        lcd_clear(WHITE);
        lcd_show_string(12, 16, 16, "ACTIVITY RECOGNITION");
        lcd_show_string(12, 40, 16, "RT-SPARK");
        lcd_draw_line(12, 66, 228, 66);
        lcd_show_string(12, 156, 16, "MQTT: Not Connected");
    }
    if (last_sensor != sensor_ready)
    {
        lcd_fill(12, 84, 228, 102, WHITE);
        lcd_show_string(12, 84, 16, sensor_ready ? "Sensor: Ready" : "Sensor: Not Ready");
        last_sensor = sensor_ready;
    }
    if (last_wifi != wifi_ready)
    {
        lcd_fill(12, 120, 228, 138, WHITE);
        lcd_show_string(12, 120, 16, wifi_ready ? "Wi-Fi: Connected" : "Wi-Fi: Not Connected");
        last_wifi = wifi_ready;
    }
    if (last_mqtt != mqtt_ready)
    {
        lcd_fill(12, 156, 228, 174, WHITE);
        lcd_show_string(12, 156, 16, mqtt_ready ? "MQTT: Connected" : "MQTT: Not Connected");
        last_mqtt = mqtt_ready;
    }
}

static void scan_done_handler(int event, struct rt_wlan_buff *buff, void *parameter)
{
    (void)event;
    (void)buff;
    (void)parameter;
    rt_sem_release(&scan_done);
}

static void ip_ready_handler(int event, struct rt_wlan_buff *buff, void *parameter)
{
    (void)event;
    (void)buff;
    (void)parameter;
    rt_sem_release(&ip_ready);
}

static void connected_handler(int event, struct rt_wlan_buff *buff, void *parameter)
{
    (void)event;
    (void)buff;
    (void)parameter;
    associated = RT_TRUE;
    rt_sem_release(&association_done);
}

static void connect_failed_handler(int event, struct rt_wlan_buff *buff, void *parameter)
{
    (void)event;
    (void)buff;
    (void)parameter;
    associated = RT_FALSE;
    rt_sem_release(&association_done);
}

static void disconnected_handler(int event, struct rt_wlan_buff *buff, void *parameter)
{
    (void)event;
    (void)buff;
    (void)parameter;
    rt_kprintf("Wi-Fi[%u ms]: station disconnected\n",
               (unsigned int)rt_tick_get_millisecond());
}

int main(void)
{
    rt_err_t result;

    rt_kprintf("\nRT-SPARK RW007 + ICM20608 DIAGNOSTIC\n");
    rt_kprintf("Elapsed time ms; Wi-Fi state sampled every 10 s\n");
    rt_kprintf("Console: uart1 115200; SPI2 RW007 initialization runs before main\n");
    rt_kprintf("LCD: %s\n", lcd_is_ready() ?
               "driver initialization completed; visual check required" :
               "initialization failed");

    if (rt_device_find("wspi") == RT_NULL)
        rt_kprintf("RW007: SPI device missing; inspect module reset and SPI2\n");
    else
        rt_kprintf("RW007: SPI device registered (module response still requires verification)\n");

    if (sensor_diag_init() != RT_EOK)
        rt_kprintf("ICM20608: initialization failed; Wi-Fi diagnostics continue\n");

    status_screen_update();

    rt_sem_init(&scan_done, "scan", 0, RT_IPC_FLAG_FIFO);
    rt_sem_init(&association_done, "assoc", 0, RT_IPC_FLAG_FIFO);
    rt_sem_init(&ip_ready, "ip", 0, RT_IPC_FLAG_FIFO);
    rt_wlan_register_event_handler(RT_WLAN_EVT_SCAN_DONE, scan_done_handler, RT_NULL);
    rt_wlan_register_event_handler(RT_WLAN_EVT_STA_CONNECTED, connected_handler, RT_NULL);
    rt_wlan_register_event_handler(RT_WLAN_EVT_STA_CONNECTED_FAIL, connect_failed_handler, RT_NULL);
    rt_wlan_register_event_handler(RT_WLAN_EVT_READY, ip_ready_handler, RT_NULL);
    rt_wlan_register_event_handler(RT_WLAN_EVT_STA_DISCONNECTED, disconnected_handler, RT_NULL);

    result = rt_wlan_scan();
    if (result == RT_EOK)
    {
        result = rt_sem_take(&scan_done, rt_tick_from_millisecond(12000));
        rt_kprintf("RW007 scan: %s\n", result == RT_EOK ? "completed" : "timeout");
    }
    else
        rt_kprintf("RW007 scan: start failed (%d)\n", result);

#ifdef HAVE_WIFI_LOCAL
    {
        struct rt_wlan_info info;
        rt_size_t ssid_len = rt_strlen(WIFI_LOCAL_SSID);
        rt_size_t password_len = rt_strlen(WIFI_LOCAL_PASSWORD);

        if (ssid_len == 0 || ssid_len > RT_WLAN_SSID_MAX_LENGTH ||
            password_len >= RT_WLAN_PASSWORD_MAX_LENGTH)
        {
            rt_kprintf("Wi-Fi: local SSID/password length unsupported by this driver\n");
            result = -RT_EINVAL;
        }
        else
        {
            rt_memset(&info, 0, sizeof(info));
            rt_memcpy(info.ssid.val, WIFI_LOCAL_SSID, ssid_len);
            info.ssid.len = (rt_uint8_t)ssid_len;
            info.security = WIFI_LOCAL_SECURITY;
            info.band = RT_802_11_BAND_2_4GHZ;
            info.channel = WIFI_LOCAL_CHANNEL;
            info.hidden = WIFI_LOCAL_HIDDEN;
            rt_kprintf("Wi-Fi: direct %s SSID join requested\n",
                       WIFI_LOCAL_HIDDEN ? "hidden" : "visible");
            rt_wlan_config_autoreconnect(RT_TRUE);
            result = rt_wlan_connect_adv(&info, WIFI_LOCAL_PASSWORD);
        }
    }
    if (result == RT_EOK &&
        rt_sem_take(&association_done, rt_tick_from_millisecond(15000)) == RT_EOK &&
        associated == RT_TRUE)
    {
        rt_kprintf("Wi-Fi: associated; waiting up to 20 seconds for DHCP\n");
        result = rt_sem_take(&ip_ready, rt_tick_from_millisecond(20000));
        if (result == RT_EOK)
        {
            rt_kprintf("Wi-Fi: network ready; interface details follow\n");
            msh_exec("ifconfig", 8);
        }
        else
            rt_kprintf("Wi-Fi: DHCP/network-ready timeout\n");
    }
    else
        rt_kprintf("Wi-Fi: association failed or timed out (%d)\n", result);
#else
    rt_kprintf("Wi-Fi: no local credentials file; association skipped\n");
#endif

    result = mqtt_publisher_start();
    rt_kprintf("MQTT publisher task: %s\n", result == RT_EOK ? "started" : "failed to start");

    for (;;)
    {
        rt_int32_t accel_milli_g[3];
        rt_int32_t gyro_milli_dps[3];

        if (sensor_diag_read(accel_milli_g, gyro_milli_dps) == RT_EOK)
            mqtt_publisher_publish(accel_milli_g, gyro_milli_dps);
        else
            mqtt_publisher_note_sensor_read_failure();
        wifi_status_report();
        status_screen_update();
        rt_thread_mdelay(200);
    }
}
