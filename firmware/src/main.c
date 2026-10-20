
#include "stm32f4xx_hal.h"

int main(void)
{
    // Initialize the STM32 HAL library
    HAL_Init();

    // Main program loop
    while (1)
    {
        // Initial test: keep the firmware running
        HAL_Delay(1000);
    }
}
