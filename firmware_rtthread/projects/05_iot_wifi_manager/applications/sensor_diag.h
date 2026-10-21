#ifndef SENSOR_DIAG_H
#define SENSOR_DIAG_H

#include <rtthread.h>

rt_err_t sensor_diag_init(void);
rt_err_t sensor_diag_read(rt_int32_t accel_milli_g[3], rt_int32_t gyro_milli_dps[3]);
void sensor_diag_poll(void);
rt_bool_t sensor_diag_is_ready(void);

#endif
