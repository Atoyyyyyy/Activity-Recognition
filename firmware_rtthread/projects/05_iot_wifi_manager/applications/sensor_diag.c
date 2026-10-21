#include "sensor_diag.h"
#include <stm32f4xx_hal.h>

/* Adapted from the physically verified firmware/src/main.c sensor path.
 * RT-Thread owns SysTick and USART1; this file initializes only I2C2. */
#define SENSOR_ADDR         (0x68U << 1)
#define WHO_AM_I_REG        0x75U
#define POWER1_REG         0x6BU
#define POWER2_REG         0x6CU
#define ACCEL_CONFIG_REG   0x1CU
#define GYRO_CONFIG_REG    0x1BU
#define DATA_REG           0x3BU
#define I2C_TIMEOUT_MS     100U

static I2C_HandleTypeDef sensor_i2c;
static rt_bool_t sensor_ready = RT_FALSE;

rt_bool_t sensor_diag_is_ready(void)
{
    return sensor_ready;
}

void HAL_I2C_MspInit(I2C_HandleTypeDef *handle)
{
    GPIO_InitTypeDef gpio = {0};
    if (handle->Instance != I2C2)
        return;

    __HAL_RCC_GPIOF_CLK_ENABLE();
    __HAL_RCC_I2C2_CLK_ENABLE();
    gpio.Pin = GPIO_PIN_0 | GPIO_PIN_1;
    gpio.Mode = GPIO_MODE_AF_OD;
    gpio.Pull = GPIO_PULLUP;
    gpio.Speed = GPIO_SPEED_FREQ_HIGH;
    gpio.Alternate = GPIO_AF4_I2C2;
    HAL_GPIO_Init(GPIOF, &gpio);
}

static rt_err_t write_reg(uint8_t reg, uint8_t value)
{
    return HAL_I2C_Mem_Write(&sensor_i2c, SENSOR_ADDR, reg,
                             I2C_MEMADD_SIZE_8BIT, &value, 1, I2C_TIMEOUT_MS) == HAL_OK
           ? RT_EOK : -RT_ERROR;
}

rt_err_t sensor_diag_init(void)
{
    uint8_t id = 0;
    HAL_StatusTypeDef status;

    sensor_i2c.Instance = I2C2;
    sensor_i2c.Init.ClockSpeed = 100000U;
    sensor_i2c.Init.DutyCycle = I2C_DUTYCYCLE_2;
    sensor_i2c.Init.OwnAddress1 = 0;
    sensor_i2c.Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
    sensor_i2c.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
    sensor_i2c.Init.OwnAddress2 = 0;
    sensor_i2c.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
    sensor_i2c.Init.NoStretchMode = I2C_NOSTRETCH_DISABLE;
    if (HAL_I2C_Init(&sensor_i2c) != HAL_OK)
    {
        rt_kprintf("ICM20608: I2C2 init failed\n");
        return -RT_ERROR;
    }

    status = HAL_I2C_Mem_Read(&sensor_i2c, SENSOR_ADDR, WHO_AM_I_REG,
                              I2C_MEMADD_SIZE_8BIT, &id, 1, I2C_TIMEOUT_MS);
    if (status != HAL_OK)
    {
        rt_kprintf("ICM20608: WHO_AM_I read failed (HAL=%d, error=0x%08x)\n",
                   status, HAL_I2C_GetError(&sensor_i2c));
        return -RT_ERROR;
    }
    rt_kprintf("ICM20608: address 0x68, WHO_AM_I=0x%02x\n", id);
    if (id != 0xAFU)
    {
        rt_kprintf("ICM20608: unexpected ID; sensor reads disabled\n");
        return -RT_ERROR;
    }

    if (write_reg(POWER1_REG, 0x01U) != RT_EOK)
        return -RT_ERROR;
    rt_thread_mdelay(100);
    if (write_reg(POWER2_REG, 0x00U) != RT_EOK ||
        write_reg(ACCEL_CONFIG_REG, 0x00U) != RT_EOK ||
        write_reg(GYRO_CONFIG_REG, 0x00U) != RT_EOK)
    {
        rt_kprintf("ICM20608: configuration failed\n");
        return -RT_ERROR;
    }
    sensor_ready = RT_TRUE;
    rt_kprintf("ICM20608: ready (+/-2 g, +/-250 dps)\n");
    return RT_EOK;
}

static int16_t signed_word(uint8_t high, uint8_t low)
{
    return (int16_t)(((uint16_t)high << 8) | low);
}

static void print_fixed(int32_t milli)
{
    uint32_t mag = (uint32_t)(milli < 0 ? -milli : milli);
    rt_kprintf("%s%u.%03u", milli < 0 ? "-" : "", mag / 1000U, mag % 1000U);
}

rt_err_t sensor_diag_read(rt_int32_t accel_milli_g[3], rt_int32_t gyro_milli_dps[3])
{
    uint8_t data[14];
    int16_t raw[6];
    unsigned int i;

    if (!sensor_ready || accel_milli_g == RT_NULL || gyro_milli_dps == RT_NULL)
        return -RT_ERROR;
    if (HAL_I2C_Mem_Read(&sensor_i2c, SENSOR_ADDR, DATA_REG,
                         I2C_MEMADD_SIZE_8BIT, data, sizeof(data), I2C_TIMEOUT_MS) != HAL_OK)
    {
        rt_kprintf("ICM20608: motion read failed (error=0x%08x)\n",
                   HAL_I2C_GetError(&sensor_i2c));
        sensor_ready = RT_FALSE;
        return -RT_ERROR;
    }

    raw[0] = signed_word(data[0], data[1]);
    raw[1] = signed_word(data[2], data[3]);
    raw[2] = signed_word(data[4], data[5]);
    raw[3] = signed_word(data[8], data[9]);
    raw[4] = signed_word(data[10], data[11]);
    raw[5] = signed_word(data[12], data[13]);
    for (i = 0; i < 3; ++i)
        accel_milli_g[i] = (rt_int32_t)raw[i] * 1000 / 16384;
    for (i = 3; i < 6; ++i)
        gyro_milli_dps[i - 3] = (rt_int32_t)raw[i] * 1000 / 131;
    return RT_EOK;
}

void sensor_diag_poll(void)
{
    rt_int32_t accel_milli_g[3];
    rt_int32_t gyro_milli_dps[3];
    unsigned int i;

    if (sensor_diag_read(accel_milli_g, gyro_milli_dps) != RT_EOK)
        return;
    rt_kprintf("ACC g:");
    for (i = 0; i < 3; ++i)
    {
        rt_kprintf(" %c=", 'X' + i);
        print_fixed(accel_milli_g[i]);
    }
    rt_kprintf(" | GYRO dps:");
    for (i = 0; i < 3; ++i)
    {
        rt_kprintf(" %c=", 'X' + i);
        print_fixed(gyro_milli_dps[i]);
    }
    rt_kprintf("\n");
}
