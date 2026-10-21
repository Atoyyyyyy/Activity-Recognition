#include "stm32f4xx_hal.h"
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

/* RT-Spark HW1.1: ICM-20608-G, PF0=SDA and PF1=SCL on I2C2. */
#define SENSOR_ID_REG 0x75U
#define POWER_REG 0x6BU
#define POWER2_REG 0x6CU
#define ACCEL_CONFIG_REG 0x1CU
#define GYRO_CONFIG_REG 0x1BU
#define ACCEL_REG 0x3BU
#define I2C_TIMEOUT_MS 100U

static UART_HandleTypeDef huart1;
static I2C_HandleTypeDef hi2c2;
static uint8_t sensor_address = 0x68U;

/* Preserve the working HAL time base, including I2C timeout handling. */
void SysTick_Handler(void)
{
    HAL_IncTick();
}

static void serial_print(const char *message)
{
    HAL_UART_Transmit(&huart1, (uint8_t *)message,
                      (uint16_t)strlen(message), 1000U);
}

static void serial_init(void)
{
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_USART1_CLK_ENABLE();

    GPIO_InitTypeDef gpio = {0};
    gpio.Pin = GPIO_PIN_9 | GPIO_PIN_10;
    gpio.Mode = GPIO_MODE_AF_PP;
    gpio.Pull = GPIO_PULLUP;
    gpio.Speed = GPIO_SPEED_FREQ_HIGH;
    gpio.Alternate = GPIO_AF7_USART1;
    HAL_GPIO_Init(GPIOA, &gpio);

    huart1.Instance = USART1;
    huart1.Init.BaudRate = 115200;
    huart1.Init.WordLength = UART_WORDLENGTH_8B;
    huart1.Init.StopBits = UART_STOPBITS_1;
    huart1.Init.Parity = UART_PARITY_NONE;
    huart1.Init.Mode = UART_MODE_TX_RX;
    huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    huart1.Init.OverSampling = UART_OVERSAMPLING_16;
    HAL_UART_Init(&huart1);
}

static bool i2c_init(void)
{
    __HAL_RCC_GPIOF_CLK_ENABLE();
    __HAL_RCC_I2C2_CLK_ENABLE();

    GPIO_InitTypeDef gpio = {0};
    gpio.Pin = GPIO_PIN_0 | GPIO_PIN_1;
    gpio.Mode = GPIO_MODE_AF_OD;
    gpio.Pull = GPIO_PULLUP;
    gpio.Speed = GPIO_SPEED_FREQ_HIGH;
    gpio.Alternate = GPIO_AF4_I2C2;
    HAL_GPIO_Init(GPIOF, &gpio);

    hi2c2.Instance = I2C2;
    hi2c2.Init.ClockSpeed = 100000U;
    hi2c2.Init.DutyCycle = I2C_DUTYCYCLE_2;
    hi2c2.Init.OwnAddress1 = 0U;
    hi2c2.Init.AddressingMode = I2C_ADDRESSINGMODE_7BIT;
    hi2c2.Init.DualAddressMode = I2C_DUALADDRESS_DISABLE;
    hi2c2.Init.OwnAddress2 = 0U;
    hi2c2.Init.GeneralCallMode = I2C_GENERALCALL_DISABLE;
    hi2c2.Init.NoStretchMode = I2C_NOSTRETCH_DISABLE;
    return HAL_I2C_Init(&hi2c2) == HAL_OK;
}

static HAL_StatusTypeDef sensor_read(uint8_t reg, uint8_t *buffer, uint16_t length)
{
    return HAL_I2C_Mem_Read(&hi2c2, (uint16_t)(sensor_address << 1), reg,
                            I2C_MEMADD_SIZE_8BIT, buffer, length, I2C_TIMEOUT_MS);
}

static HAL_StatusTypeDef sensor_write(uint8_t reg, uint8_t value)
{
    return HAL_I2C_Mem_Write(&hi2c2, (uint16_t)(sensor_address << 1), reg,
                             I2C_MEMADD_SIZE_8BIT, &value, 1U, I2C_TIMEOUT_MS);
}

static void print_i2c_failure(const char *operation, HAL_StatusTypeDef status)
{
    char message[100];
    uint32_t error = HAL_I2C_GetError(&hi2c2);
    const char *reason = (error & HAL_I2C_ERROR_AF) ? "NACK" :
                         (status == HAL_TIMEOUT ? "TIMEOUT" : "BUS/OTHER");
    snprintf(message, sizeof(message), "%s: %s (HAL=%d, error=0x%08lX)\r\n",
             operation, reason, (int)status, (unsigned long)error);
    serial_print(message);
}

static bool sensor_init(void)
{
    char message[96];
    bool found = false;
    uint8_t found_address = 0U;
    serial_print("Scanning sensor addresses...\r\n");

    for (uint8_t address = 0x68U; address <= 0x69U; ++address)
    {
        sensor_address = address;
        HAL_StatusTypeDef status = HAL_I2C_IsDeviceReady(
            &hi2c2, (uint16_t)(address << 1), 1U, I2C_TIMEOUT_MS);
        if (status != HAL_OK)
        {
            /* HAL_I2C_IsDeviceReady clears AF and returns HAL_ERROR with
               ErrorCode == NONE when its address phase receives a NACK. */
            if (status == HAL_ERROR && HAL_I2C_GetError(&hi2c2) == HAL_I2C_ERROR_NONE)
            {
                snprintf(message, sizeof(message), "Address 0x%02X: NACK\r\n", address);
                serial_print(message);
            }
            else
            {
                snprintf(message, sizeof(message), "Address 0x%02X", address);
                print_i2c_failure(message, status);
            }
            continue;
        }

        snprintf(message, sizeof(message), "Address 0x%02X: ACK\r\n", address);
        serial_print(message);

        uint8_t id = 0U;
        status = sensor_read(SENSOR_ID_REG, &id, 1U);
        if (status != HAL_OK)
        {
            print_i2c_failure("WHO_AM_I read", status);
            continue;
        }
        snprintf(message, sizeof(message), "Address 0x%02X WHO_AM_I: 0x%02X\r\n",
                 address, id);
        serial_print(message);
        if (id != 0xAFU && id != 0xAEU)
        {
            serial_print("Unexpected sensor ID; initialization skipped.\r\n");
            continue;
        }
        if (!found)
        {
            found = true;
            found_address = address;
        }
    }

    if (!found)
    {
        serial_print("Sensor initialization: FAILED (no supported sensor ID)\r\n");
        return false;
    }

    sensor_address = found_address;
    HAL_StatusTypeDef status = sensor_write(POWER_REG, 0x01U);
    if (status != HAL_OK)
    {
        print_i2c_failure("Wake PWR_MGMT_1", status);
        return false;
    }
    HAL_Delay(100U);
    if ((status = sensor_write(POWER2_REG, 0x00U)) != HAL_OK)
    {
        print_i2c_failure("PWR_MGMT_2", status);
        return false;
    }
    if ((status = sensor_write(ACCEL_CONFIG_REG, 0x00U)) != HAL_OK)
    {
        print_i2c_failure("ACCEL_CONFIG", status);
        return false;
    }
    if ((status = sensor_write(GYRO_CONFIG_REG, 0x00U)) != HAL_OK)
    {
        print_i2c_failure("GYRO_CONFIG", status);
        return false;
    }

    serial_print("Sensor initialization: SUCCESS (+/-2 g, +/-250 dps)\r\n");
    return true;
}

static int16_t to_int16(uint8_t high, uint8_t low)
{
    return (int16_t)(((uint16_t)high << 8) | low);
}

static void format_scaled(char *out, size_t size, int32_t milli)
{
    const char *sign = milli < 0 ? "-" : "";
    uint32_t magnitude = (uint32_t)(milli < 0 ? -milli : milli);
    snprintf(out, size, "%s%lu.%03lu", sign,
             (unsigned long)(magnitude / 1000U),
             (unsigned long)(magnitude % 1000U));
}

static bool print_motion_data(void)
{
    uint8_t data[14];
    HAL_StatusTypeDef status = sensor_read(ACCEL_REG, data, sizeof(data));
    if (status != HAL_OK)
    {
        print_i2c_failure("Motion burst read", status);
        return false;
    }

    int16_t raw[6] = {
        to_int16(data[0], data[1]), to_int16(data[2], data[3]),
        to_int16(data[4], data[5]), to_int16(data[8], data[9]),
        to_int16(data[10], data[11]), to_int16(data[12], data[13])
    };
    char value[6][16];
    for (unsigned i = 0; i < 3; ++i)
        format_scaled(value[i], sizeof(value[i]), (int32_t)raw[i] * 1000 / 16384);
    for (unsigned i = 3; i < 6; ++i)
        format_scaled(value[i], sizeof(value[i]), (int32_t)raw[i] * 1000 / 131);

    char message[180];
    snprintf(message, sizeof(message),
             "ACC: X=%s Y=%s Z=%s g | GYRO: X=%s Y=%s Z=%s dps\r\n",
             value[0], value[1], value[2], value[3], value[4], value[5]);
    serial_print(message);
    return true;
}

int main(void)
{
    HAL_Init();
    serial_init();

    /* Onboard red LED (PF12), retained for a visible heartbeat. */
    __HAL_RCC_GPIOF_CLK_ENABLE();
    GPIO_InitTypeDef led = {0};
    led.Pin = GPIO_PIN_12;
    led.Mode = GPIO_MODE_OUTPUT_PP;
    led.Pull = GPIO_NOPULL;
    led.Speed = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(GPIOF, &led);
    HAL_GPIO_WritePin(GPIOF, GPIO_PIN_12, GPIO_PIN_SET);

    HAL_Delay(200U);
    serial_print("\r\nRT-SPARK SENSOR DIAGNOSTIC\r\n");
    if (!i2c_init())
    {
        serial_print("I2C2 initialization: FAILED\r\n");
        while (1) HAL_Delay(1000U);
    }
    serial_print("I2C2 initialization: SUCCESS (PF1=SCL, PF0=SDA, 100 kHz)\r\n");

    bool ready = false;
    while (1)
    {
        if (!ready)
            ready = sensor_init();
        else if (!print_motion_data())
            ready = false;

        HAL_GPIO_TogglePin(GPIOF, GPIO_PIN_12);
        HAL_Delay(ready ? 200U : 2000U);
    }
}
