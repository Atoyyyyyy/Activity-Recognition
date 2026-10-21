#include <rtthread.h>
#include <rtdevice.h>
#include <wlan_mgnt.h>
#include <lwip/apps/mqtt.h>
#include <lwip/ip_addr.h>
#include <lwip/tcpip.h>
#include "mqtt_publisher.h"
#include "mqtt_local.h"

#if !LWIP_TCP || !LWIP_CALLBACK_API || !LWIP_TCPIP_CORE_LOCKING
#error "RT-Spark MQTT publisher requires lwIP TCP callback API and core locking"
#endif

#define MQTT_BROKER_PORT          1883U
#define MQTT_BROKER_ADDRESS        "192.168.0.101"
#define MQTT_TOPIC                "activity/sensors"
#define MQTT_CLIENT_ID            MQTT_LOCAL_CLIENT_ID
#define MQTT_SAMPLE_QUEUE_DEPTH   1U
#define MQTT_WORKER_STACK_SIZE    3072U
#define MQTT_WORKER_PRIORITY      18U
#define MQTT_WORKER_TIMESLICE     10U
#define MQTT_RETRY_INITIAL_MS     1000U
#define MQTT_RETRY_MAX_MS         30000U
#define MQTT_CONNECT_TIMEOUT_MS   15000U
#define MQTT_WIFI_WAIT_LOG_MS     10000U
#define MQTT_CLIENT_ALLOC_LOG_MS  10000U
#define MQTT_STATS_PERIOD_MS      10000U


typedef struct
{
    rt_int32_t accel_milli_g[3];
    rt_int32_t gyro_milli_dps[3];
} mqtt_sensor_sample_t;

static rt_mq_t sample_queue;
static struct rt_thread mqtt_worker;
static rt_uint8_t mqtt_worker_stack[MQTT_WORKER_STACK_SIZE];
static mqtt_client_t *mqtt_client;
static volatile rt_bool_t mqtt_connected;
static volatile rt_bool_t mqtt_connecting;
static volatile rt_bool_t disconnect_requested;
static volatile rt_uint32_t callback_sequence;
static volatile int callback_status = MQTT_CONNECT_DISCONNECTED;
static volatile rt_uint32_t sensor_samples_produced;
static volatile rt_uint32_t sensor_samples_queued;
static volatile rt_uint32_t queue_full_drops;
static volatile rt_uint32_t offline_sample_drops;
static volatile rt_uint32_t offline_queue_discards;
static volatile rt_uint32_t payload_format_drops;
static volatile rt_uint32_t sensor_read_failures;
static volatile rt_uint32_t publish_api_calls;
static volatile rt_uint32_t publish_api_ok;
static volatile rt_uint32_t publish_err_mem;
static volatile rt_uint32_t publish_err_other;
static volatile rt_uint32_t mqtt_connect_attempts;
static volatile rt_uint32_t mqtt_reconnects;
static volatile rt_uint32_t sample_interval_total_ms;
static volatile rt_uint32_t sample_interval_count;
static volatile rt_uint32_t sample_interval_min_ms;
static volatile rt_uint32_t sample_interval_max_ms;
static volatile rt_uint32_t sample_interval_last_ms;
static rt_uint32_t last_sample_tick_ms;
static rt_bool_t publisher_started;

static rt_uint32_t prev_samples, prev_queued, prev_queue_drops;
static rt_uint32_t prev_offline_drops, prev_offline_discards;
static rt_uint32_t prev_api_calls, prev_api_ok, prev_api_mem, prev_api_other;
static rt_uint32_t prev_req_mem, prev_ring_mem, prev_tcp_acked;
static rt_uint32_t prev_tcp_write_mem, prev_tcp_write_other, prev_reconnects;

static rt_uint32_t pending_request_count_locked(const mqtt_client_t *client)
{
    const struct mqtt_request_t *request = client->pend_req_queue;
    rt_uint32_t count = 0;

    while (request != RT_NULL && count < MQTT_REQ_MAX_IN_FLIGHT)
    {
        count++;
        request = request->next;
    }
    return count;
}

static void report_publish_diagnostics(rt_uint32_t now_ms)
{
    rt_uint32_t produced, queued, queue_drops, offline_drops, offline_discards;
    rt_uint32_t format_drops, read_errors, api_calls, api_ok, mem_errors, other_errors;
    rt_uint32_t interval_total, interval_count, interval_min, interval_max, interval_last;
    rt_uint32_t pending = 0, lwip_calls = 0, lwip_ok = 0, mem_request = 0;
    rt_uint32_t mem_output = 0, tcp_acked = 0, tcp_write_mem = 0, tcp_write_other = 0;
    rt_uint32_t output_used = 0, output_high = 0, pending_high = 0;
    rt_uint32_t attempts = 0;
    rt_uint32_t callbacks = 0;
    int last_callback_status = MQTT_CONNECT_DISCONNECTED;
    rt_bool_t connected_snapshot = RT_FALSE;

    rt_enter_critical();
    produced = sensor_samples_produced;
    queued = sensor_samples_queued;
    queue_drops = queue_full_drops;
    offline_drops = offline_sample_drops;
    offline_discards = offline_queue_discards;
    format_drops = payload_format_drops;
    read_errors = sensor_read_failures;
    api_calls = publish_api_calls;
    api_ok = publish_api_ok;
    mem_errors = publish_err_mem;
    other_errors = publish_err_other;
    interval_total = sample_interval_total_ms;
    interval_count = sample_interval_count;
    interval_min = sample_interval_min_ms;
    interval_max = sample_interval_max_ms;
    interval_last = sample_interval_last_ms;
    sample_interval_total_ms = 0;
    sample_interval_count = 0;
    sample_interval_min_ms = 0xFFFFFFFFU;
    sample_interval_max_ms = 0;
    rt_exit_critical();

    if (mqtt_client != RT_NULL)
    {
        LOCK_TCPIP_CORE();
        lwip_calls = mqtt_client->diag_publish_calls;
        lwip_ok = mqtt_client->diag_publish_api_ok;
        mem_request = mqtt_client->diag_publish_err_mem_request;
        mem_output = mqtt_client->diag_publish_err_mem_output;
        tcp_acked = mqtt_client->diag_publish_tcp_acked;
        tcp_write_mem = mqtt_client->diag_tcp_write_err_mem;
        tcp_write_other = mqtt_client->diag_tcp_write_err_other;
        output_used = (rt_uint16_t)(mqtt_client->output.put - mqtt_client->output.get);
        output_high = mqtt_client->diag_output_high_water;
        pending = pending_request_count_locked(mqtt_client);
        pending_high = mqtt_client->diag_pending_high_water;
        callbacks = callback_sequence;
        last_callback_status = callback_status;
        connected_snapshot = mqtt_connected;
        UNLOCK_TCPIP_CORE();
    }
    attempts = mqtt_connect_attempts;

    /* I lines are deltas since the preceding 10-second report; T is cumulative. */
    rt_kprintf("I t%u s%u q%u f%u o%u d%u dt%u/%u/%u/%u\n",
               (unsigned int)now_ms, (unsigned int)(produced - prev_samples),
               (unsigned int)(queued - prev_queued),
               (unsigned int)(queue_drops - prev_queue_drops),
               (unsigned int)(offline_drops - prev_offline_drops),
               (unsigned int)(offline_discards - prev_offline_discards),
               (unsigned int)(interval_count ? interval_total / interval_count : 0),
               (unsigned int)(interval_count ? interval_min : 0),
               (unsigned int)(interval_count ? interval_max : 0),
               (unsigned int)(interval_count ? interval_last : 0));
    rt_kprintf("I p%u ok%u e%u r%u g%u a%u w%u/%u x%u\n",
               (unsigned int)(api_calls - prev_api_calls),
               (unsigned int)(api_ok - prev_api_ok),
               (unsigned int)((mem_errors - prev_api_mem) + (other_errors - prev_api_other)),
               (unsigned int)(mem_request - prev_req_mem),
               (unsigned int)(mem_output - prev_ring_mem),
               (unsigned int)(tcp_acked - prev_tcp_acked),
               (unsigned int)(tcp_write_mem - prev_tcp_write_mem),
               (unsigned int)(tcp_write_other - prev_tcp_write_other),
               (unsigned int)(mqtt_reconnects - prev_reconnects));
    rt_kprintf("T s%u q%u f%u o%u d%u rd%u fm%u p%u ok%u e%u\n",
               (unsigned int)produced, (unsigned int)queued, (unsigned int)queue_drops,
               (unsigned int)offline_drops, (unsigned int)offline_discards,
               (unsigned int)read_errors, (unsigned int)format_drops,
               (unsigned int)api_calls, (unsigned int)api_ok,
               (unsigned int)(mem_errors + other_errors));
    rt_kprintf("T r%u g%u a%u w%u/%u ca%u rx%u\n",
               (unsigned int)mem_request, (unsigned int)mem_output,
               (unsigned int)tcp_acked, (unsigned int)tcp_write_mem,
               (unsigned int)tcp_write_other, (unsigned int)attempts,
               (unsigned int)mqtt_reconnects);
    rt_kprintf("N p%u/%u h%u o%u/%u oh%u cb%u st%d c%u\n",
               (unsigned int)pending, (unsigned int)MQTT_REQ_MAX_IN_FLIGHT,
               (unsigned int)pending_high, (unsigned int)output_used,
               (unsigned int)MQTT_OUTPUT_RINGBUF_SIZE, (unsigned int)output_high,
               (unsigned int)callbacks, last_callback_status, connected_snapshot);

    prev_samples = produced; prev_queued = queued; prev_queue_drops = queue_drops;
    prev_offline_drops = offline_drops; prev_offline_discards = offline_discards;
    prev_api_calls = api_calls; prev_api_ok = api_ok;
    prev_api_mem = mem_errors; prev_api_other = other_errors;
    prev_req_mem = mem_request; prev_ring_mem = mem_output;
    prev_tcp_acked = tcp_acked; prev_tcp_write_mem = tcp_write_mem;
    prev_tcp_write_other = tcp_write_other; prev_reconnects = mqtt_reconnects;
}

static const char *connection_status_name(int status)
{
    switch (status)
    {
    case MQTT_CONNECT_ACCEPTED: return "accepted";
    case MQTT_CONNECT_REFUSED_PROTOCOL_VERSION: return "protocol-refused";
    case MQTT_CONNECT_REFUSED_IDENTIFIER: return "client-id-refused";
    case MQTT_CONNECT_REFUSED_SERVER: return "server-refused";
    case MQTT_CONNECT_REFUSED_USERNAME_PASS: return "credentials-refused";
    case MQTT_CONNECT_REFUSED_NOT_AUTHORIZED_: return "authorization-refused";
    case MQTT_CONNECT_DISCONNECTED: return "disconnected";
    default: return "unknown";
    }
}

static const struct mqtt_connect_client_info_t connect_info = {
    MQTT_CLIENT_ID,
    MQTT_LOCAL_USERNAME,
    MQTT_LOCAL_PASSWORD,
    30,
    RT_NULL,
    RT_NULL,
    0,
    0
};

static void connection_callback(mqtt_client_t *client, void *arg,
                                mqtt_connection_status_t status)
{
    (void)client;
    (void)arg;

    if (disconnect_requested)
    {
        mqtt_connected = RT_FALSE;
        mqtt_connecting = RT_FALSE;
        callback_status = MQTT_CONNECT_DISCONNECTED;
    }
    else
    {
        mqtt_connected = (status == MQTT_CONNECT_ACCEPTED) ? RT_TRUE : RT_FALSE;
        mqtt_connecting = RT_FALSE;
        callback_status = status;
    }
    callback_sequence++;
}

static void format_milli(char *out, rt_size_t out_size, rt_int32_t value)
{
    rt_uint32_t magnitude;
    rt_int64_t wide_value = value;

    if (wide_value < 0)
        wide_value = -wide_value;
    magnitude = (rt_uint32_t)wide_value;
    rt_snprintf(out, out_size, "%s%u.%03u", value < 0 ? "-" : "",
                (unsigned int)(magnitude / 1000U),
                (unsigned int)(magnitude % 1000U));
}

static rt_bool_t make_payload(const mqtt_sensor_sample_t *sample,
                              char *payload, rt_size_t payload_size,
                              rt_uint16_t *payload_length)
{
    char values[6][16];
    int length;
    unsigned int i;

    for (i = 0; i < 3; ++i)
    {
        format_milli(values[i], sizeof(values[i]), sample->accel_milli_g[i]);
        format_milli(values[i + 3], sizeof(values[i + 3]), sample->gyro_milli_dps[i]);
    }

    length = rt_snprintf(payload, payload_size,
                         "{\"acc_x\":%s,\"acc_y\":%s,\"acc_z\":%s,"
                         "\"gyro_x\":%s,\"gyro_y\":%s,\"gyro_z\":%s}",
                         values[0], values[1], values[2],
                         values[3], values[4], values[5]);
    if (length <= 0 || (rt_size_t)length >= payload_size || length > 0xFFFF)
        return RT_FALSE;

    *payload_length = (rt_uint16_t)length;
    return RT_TRUE;
}

static void drain_sample_queue(void)
{
    mqtt_sensor_sample_t discarded;
    while (rt_mq_recv(sample_queue, &discarded, sizeof(discarded), 0) == RT_EOK)
    {
        /* Stale readings are discarded while offline; no backlog is replayed. */
        offline_queue_discards++;
    }
}

static rt_uint32_t next_retry_delay(rt_uint32_t current_ms)
{
    if (current_ms >= MQTT_RETRY_MAX_MS / 2U)
        return MQTT_RETRY_MAX_MS;
    return current_ms * 2U;
}

static void mqtt_worker_entry(void *parameter)
{
    ip_addr_t broker_address;
    rt_bool_t wifi_was_ready = RT_FALSE;
    rt_bool_t retry_scheduled = RT_FALSE;
    rt_uint32_t retry_delay_ms = MQTT_RETRY_INITIAL_MS;
    rt_tick_t retry_at = 0;
    rt_tick_t connect_started_at = 0;
    rt_tick_t last_wifi_wait_log = 0;
    rt_tick_t last_alloc_log = 0;
    rt_tick_t last_stats_log = 0;
    rt_uint32_t seen_callback_sequence = 0;
    rt_uint32_t connect_attempt = 0;
    err_t error;
    mqtt_sensor_sample_t sample;
    char payload[160];
    rt_uint16_t payload_length;

    (void)parameter;

    ipaddr_aton(MQTT_BROKER_ADDRESS, &broker_address);
    rt_kprintf("MQTT[%u ms]: worker started; waiting for WLAN READY/DHCP\n",
               (unsigned int)rt_tick_get_millisecond());

    for (;;)
    {
        rt_tick_t now = rt_tick_get();
        rt_bool_t wifi_ready = (rt_wlan_is_ready() == RT_TRUE);

        if (callback_sequence != seen_callback_sequence)
        {
            int status;
            seen_callback_sequence = callback_sequence;
            status = callback_status;
            if (status == MQTT_CONNECT_ACCEPTED && mqtt_connected)
            {
                rt_kprintf("MQTT[%u ms]: callback status=%d (%s); authenticated session established\n",
                           (unsigned int)rt_tick_get_millisecond(), status,
                           connection_status_name(status));
                retry_delay_ms = MQTT_RETRY_INITIAL_MS;
                retry_scheduled = RT_FALSE;
            }
            else
            {
                rt_kprintf("MQTT[%u ms]: callback status=%d (%s)\n",
                           (unsigned int)rt_tick_get_millisecond(), status,
                           connection_status_name(status));
                if (status == MQTT_CONNECT_REFUSED_USERNAME_PASS ||
                    status == MQTT_CONNECT_REFUSED_NOT_AUTHORIZED_)
                    rt_kprintf("MQTT[%u ms]: broker rejected credentials/authorization\n",
                               (unsigned int)rt_tick_get_millisecond());
                mqtt_connected = RT_FALSE;
                mqtt_connecting = RT_FALSE;
                retry_at = now + rt_tick_from_millisecond(retry_delay_ms);
                rt_kprintf("MQTT[%u ms]: retry in %u ms\n",
                           (unsigned int)rt_tick_get_millisecond(),
                           (unsigned int)retry_delay_ms);
                retry_delay_ms = next_retry_delay(retry_delay_ms);
                retry_scheduled = RT_TRUE;
                drain_sample_queue();
            }
        }

        if (!wifi_ready)
        {
            if (last_wifi_wait_log == 0 ||
                (rt_tick_t)(now - last_wifi_wait_log) >=
                    rt_tick_from_millisecond(MQTT_WIFI_WAIT_LOG_MS))
            {
                rt_kprintf("MQTT[%u ms]: waiting for Wi-Fi/DHCP; connected=%d ready=%d\n",
                           (unsigned int)rt_tick_get_millisecond(),
                           rt_wlan_is_connected() == RT_TRUE, wifi_ready);
                last_wifi_wait_log = now;
            }
            if (wifi_was_ready && mqtt_client != RT_NULL)
            {
                rt_kprintf("MQTT[%u ms]: WLAN READY lost; closing MQTT session\n",
                           (unsigned int)rt_tick_get_millisecond());
                disconnect_requested = RT_TRUE;
                LOCK_TCPIP_CORE();
                mqtt_disconnect(mqtt_client);
                UNLOCK_TCPIP_CORE();
                disconnect_requested = RT_FALSE;
                mqtt_connected = RT_FALSE;
                mqtt_connecting = RT_FALSE;
            }
            wifi_was_ready = RT_FALSE;
            drain_sample_queue();
            rt_thread_mdelay(250);
            continue;
        }

        if (!wifi_was_ready)
        {
            wifi_was_ready = RT_TRUE;
            retry_scheduled = RT_FALSE;
        }

        if (mqtt_client == RT_NULL)
        {
            LOCK_TCPIP_CORE();
            mqtt_client = mqtt_client_new();
            UNLOCK_TCPIP_CORE();
            if (mqtt_client == RT_NULL)
            {
                if (last_alloc_log == 0 ||
                    (rt_tick_t)(now - last_alloc_log) >=
                        rt_tick_from_millisecond(MQTT_CLIENT_ALLOC_LOG_MS))
                {
                    rt_kprintf("MQTT[%u ms]: client allocation failed; retrying\n",
                               (unsigned int)rt_tick_get_millisecond());
                    last_alloc_log = now;
                }
                rt_thread_mdelay(1000);
                continue;
            }
        }

        if (!mqtt_connected && !mqtt_connecting &&
            (!retry_scheduled || (rt_int32_t)(now - retry_at) >= 0))
        {
            if (connect_attempt > 0)
                mqtt_reconnects++;
            connect_attempt++;
            mqtt_connect_attempts++;
            mqtt_connecting = RT_TRUE;
            connect_started_at = now;
            rt_kprintf("MQTT[%u ms]: connect attempt %u to %s:1883\n",
                       (unsigned int)rt_tick_get_millisecond(),
                       (unsigned int)connect_attempt, MQTT_BROKER_ADDRESS);
            LOCK_TCPIP_CORE();
            error = mqtt_client_connect(mqtt_client, &broker_address,
                                        MQTT_BROKER_PORT, connection_callback,
                                        RT_NULL, &connect_info);
            UNLOCK_TCPIP_CORE();
            rt_kprintf("MQTT[%u ms]: connect request lwIP result=%d\n",
                       (unsigned int)rt_tick_get_millisecond(), error);
            if (error != ERR_OK)
            {
                mqtt_connecting = RT_FALSE;
                retry_at = rt_tick_get() + rt_tick_from_millisecond(retry_delay_ms);
                rt_kprintf("MQTT[%u ms]: immediate connect error=%d; retry in %u ms\n",
                           (unsigned int)rt_tick_get_millisecond(), error,
                           (unsigned int)retry_delay_ms);
                retry_delay_ms = next_retry_delay(retry_delay_ms);
                retry_scheduled = RT_TRUE;
            }
            else
                retry_scheduled = RT_FALSE;
        }

        if (mqtt_connecting &&
            (rt_int32_t)(now - (connect_started_at +
             rt_tick_from_millisecond(MQTT_CONNECT_TIMEOUT_MS))) >= 0)
        {
            disconnect_requested = RT_TRUE;
            LOCK_TCPIP_CORE();
            mqtt_disconnect(mqtt_client);
            UNLOCK_TCPIP_CORE();
            disconnect_requested = RT_FALSE;
            mqtt_connecting = RT_FALSE;
            mqtt_connected = RT_FALSE;
            retry_at = rt_tick_get() + rt_tick_from_millisecond(retry_delay_ms);
            rt_kprintf("MQTT[%u ms]: connection timed out after %u ms; retry in %u ms\n",
                       (unsigned int)rt_tick_get_millisecond(),
                       MQTT_CONNECT_TIMEOUT_MS, (unsigned int)retry_delay_ms);
            retry_delay_ms = next_retry_delay(retry_delay_ms);
            retry_scheduled = RT_TRUE;
            rt_kprintf("MQTT: connect timed out; retry scheduled\n");
        }

        if (mqtt_connected)
        {
            if (rt_mq_recv(sample_queue, &sample, sizeof(sample),
                           rt_tick_from_millisecond(100)) == RT_EOK)
            {
                if (make_payload(&sample, payload, sizeof(payload), &payload_length))
                {
                    LOCK_TCPIP_CORE();
                    publish_api_calls++;
                    error = mqtt_client_is_connected(mqtt_client) ?
                            mqtt_publish(mqtt_client, MQTT_TOPIC, payload,
                                         payload_length, 0, 0, RT_NULL, RT_NULL) : ERR_CONN;
                    UNLOCK_TCPIP_CORE();
                    if (error != ERR_OK)
                    {
                        if (error == ERR_MEM)
                            publish_err_mem++;
                        else
                            publish_err_other++;
                    }
                    else
                        publish_api_ok++;
                }
                else
                    payload_format_drops++;
            }
        }
        else
        {
            drain_sample_queue();
            rt_thread_mdelay(100);
        }

        if (last_stats_log == 0 ||
            (rt_tick_t)(now - last_stats_log) >=
                rt_tick_from_millisecond(MQTT_STATS_PERIOD_MS))
        {
            report_publish_diagnostics(rt_tick_get_millisecond());
            last_stats_log = now;
        }
    }
}

rt_err_t mqtt_publisher_start(void)
{
    rt_err_t result;

    if (publisher_started)
        return RT_EOK;

    rt_kprintf("MQTT[%u ms]: publisher startup requested\n",
               (unsigned int)rt_tick_get_millisecond());

    sample_queue = rt_mq_create("mqdata", sizeof(mqtt_sensor_sample_t),
                                MQTT_SAMPLE_QUEUE_DEPTH, RT_IPC_FLAG_FIFO);
    if (sample_queue == RT_NULL)
        return -RT_ENOMEM;

    result = rt_thread_init(&mqtt_worker, "mqtt", mqtt_worker_entry, RT_NULL,
                            mqtt_worker_stack, sizeof(mqtt_worker_stack),
                            MQTT_WORKER_PRIORITY, MQTT_WORKER_TIMESLICE);
    if (result != RT_EOK)
        return result;

    result = rt_thread_startup(&mqtt_worker);
    if (result == RT_EOK)
        publisher_started = RT_TRUE;
    return result;
}

rt_err_t mqtt_publisher_publish(const rt_int32_t accel_milli_g[3],
                                const rt_int32_t gyro_milli_dps[3])
{
    mqtt_sensor_sample_t sample;
    unsigned int i;

    if (!publisher_started || accel_milli_g == RT_NULL || gyro_milli_dps == RT_NULL)
        return -RT_ERROR;
    sensor_samples_produced++;
    {
        rt_uint32_t now_ms = rt_tick_get_millisecond();
        if (last_sample_tick_ms != 0)
        {
            rt_uint32_t interval_ms = now_ms - last_sample_tick_ms;
            if (sample_interval_count == 0 || interval_ms < sample_interval_min_ms)
                sample_interval_min_ms = interval_ms;
            if (interval_ms > sample_interval_max_ms)
                sample_interval_max_ms = interval_ms;
            sample_interval_total_ms += interval_ms;
            sample_interval_count++;
            sample_interval_last_ms = interval_ms;
        }
        last_sample_tick_ms = now_ms;
    }
    if (!mqtt_connected)
    {
        offline_sample_drops++;
        return -RT_ERROR;
    }

    for (i = 0; i < 3; ++i)
    {
        sample.accel_milli_g[i] = accel_milli_g[i];
        sample.gyro_milli_dps[i] = gyro_milli_dps[i];
    }

    if (rt_mq_send(sample_queue, &sample, sizeof(sample)) != RT_EOK)
    {
        queue_full_drops++;
        return -RT_EFULL;
    }
    sensor_samples_queued++;
    return RT_EOK;
}

void mqtt_publisher_note_sensor_read_failure(void)
{
    sensor_read_failures++;
}

rt_bool_t mqtt_publisher_is_connected(void)
{
    return mqtt_connected;
}
