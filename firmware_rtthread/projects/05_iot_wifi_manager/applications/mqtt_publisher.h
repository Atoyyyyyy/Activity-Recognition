#ifndef MQTT_PUBLISHER_H
#define MQTT_PUBLISHER_H

#include <rtthread.h>

rt_err_t mqtt_publisher_start(void);
rt_err_t mqtt_publisher_publish(const rt_int32_t accel_milli_g[3],
                                const rt_int32_t gyro_milli_dps[3]);
void mqtt_publisher_note_sensor_read_failure(void);
rt_bool_t mqtt_publisher_is_connected(void);

#endif
