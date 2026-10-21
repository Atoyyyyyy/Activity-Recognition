#include <rtthread.h>

#ifdef RW007_USING_STM32_DRIVERS
#include <rtdevice.h>
#include <drv_spi.h>
#include <board.h>
#include <spi_wifi_rw007.h>

extern void spi_wifi_isr(int vector);

static rt_err_t rw007_gpio_init(void)
{
    rt_tick_t start;
    /* Configure IO */
    rt_pin_mode(RW007_RST_PIN, PIN_MODE_OUTPUT);
    rt_pin_mode(RW007_INT_BUSY_PIN, PIN_MODE_INPUT_PULLDOWN);

    /* Reset rw007 and config mode */
    rt_pin_write(RW007_RST_PIN, PIN_LOW);
    rt_thread_delay(rt_tick_from_millisecond(100));
    rt_pin_write(RW007_RST_PIN, PIN_HIGH);

    /* Wait rw007 ready(exit busy stat) */
    start = rt_tick_get();
    while(!rt_pin_read(RW007_INT_BUSY_PIN))
    {
        if ((rt_tick_t)(rt_tick_get() - start) > rt_tick_from_millisecond(3000))
        {
            rt_kprintf("RW007: ready pin PG11 timed out after reset\n");
            return -RT_ETIMEOUT;
        }
        rt_thread_delay(5);
    }

    rt_thread_delay(rt_tick_from_millisecond(200));
    rt_pin_mode(RW007_INT_BUSY_PIN, PIN_MODE_INPUT_PULLUP);
    rt_kprintf("RW007: ready pin asserted\n");
    return RT_EOK;
}

int wifi_spi_device_init(void)
{
    char sn_version[32];
    rt_err_t result;
    
    GPIO_TypeDef *cs_gpiox;
    uint16_t cs_pin;

    cs_gpiox = (GPIO_TypeDef *)((rt_base_t)GPIOA + (rt_base_t)(RW007_CS_PIN / 16) * 0x0400UL);
    cs_pin = (uint16_t)(1 << RW007_CS_PIN % 16);
    
    result = rw007_gpio_init();
    if (result != RT_EOK)
        return result;
    result = rt_hw_spi_device_attach(RW007_SPI_BUS_NAME, "wspi", cs_gpiox, cs_pin);
    if (result != RT_EOK)
    {
        rt_kprintf("RW007: SPI2 device attach failed (%d)\n", result);
        return result;
    }
    result = rt_hw_wifi_init("wspi");
    if (result != RT_EOK)
    {
        rt_kprintf("RW007: driver init failed (%d)\n", result);
        return result;
    }
    rt_kprintf("RW007: driver initialized over SPI2\n");

    rt_wlan_set_mode(RT_WLAN_DEVICE_STA_NAME, RT_WLAN_STATION);
    rt_wlan_set_mode(RT_WLAN_DEVICE_AP_NAME, RT_WLAN_AP);

    result = rw007_version_get(sn_version);
    if (result == RT_EOK)
        rt_kprintf("RW007: firmware version [%s]\n", sn_version);
    else
        rt_kprintf("RW007: firmware version query failed (%d)\n", result);

    return 0;
}
INIT_APP_EXPORT(wifi_spi_device_init);

static void int_wifi_irq(void * p)
{
    ((void)p);
    spi_wifi_isr(0);
}

void spi_wifi_hw_init(void)
{
    rt_pin_attach_irq(RW007_INT_BUSY_PIN, PIN_IRQ_MODE_FALLING, int_wifi_irq, 0);
    rt_pin_irq_enable(RW007_INT_BUSY_PIN, RT_TRUE);
}

#endif /* RW007_USING_STM32_DRIVERS */
