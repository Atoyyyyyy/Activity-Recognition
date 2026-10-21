# RT-Spark RT-Thread RW007 bring-up target

This target is isolated from the verified STM32Cube firmware in `../firmware/`.
It starts from the official RT-Spark `projects/05_iot_wifi_manager` BSP example
at upstream commit `4c284fc3efb2b4c64d16973b032bcab5518a4523` and RW007 package
v2.0.1. It uses the example's UART1 console (115200), SPI2 RW007 support,
RT-Thread WLAN manager, and lwIP DHCP configuration. The application adds
bounded scan/DHCP waits, the ICM20608 I2C2 driver, and an authenticated MQTT
publisher. The earlier Step 75 RT-Thread image was uploaded and verified on
the RT-Spark. The current optimized 173,356-byte image (SHA-256
`031CBE6772C2126F4EDE1230CE8D1F63F8EC4A4AFB80E0A2BC4C02427DC395A8`) has
not been flashed. A live flash-preservation review and separate approval are
required before programming.

## Pinned upstream directories

The local sparse BSP checkout contained the Wi-Fi manager project, RW007
package, and `libraries/HAL_Drivers`. A native SCons build also requires these
exact upstream directories at this target's root:

- `rt-thread/`
- `libraries/STM32F4xx_HAL/`

Both directories were restored from the same pinned official BSP commit and
the isolated target now builds. Do not substitute an unrelated RT-Thread or
HAL release. This target retains its copied source trees and RW007 package
as local source files. Generated outputs and local credential headers are
ignored by Git.

## Local Wi-Fi configuration

Run `projects/05_iot_wifi_manager/applications/configure_wifi.ps1` locally in
PowerShell. It prompts for SSID and password with input hidden, asks for the
actual 2.4 GHz channel, security type, and whether the access point hides its
name, then writes the ignored `wifi_local.h`. It does not print the SSID or
password and refuses to overwrite an existing local header. Do not put a
password into chat, logs, screenshots, or a tracked file. Without
`wifi_local.h`, the image can run module/sensor/scan
diagnostics but deliberately skips association. The credential will still be
embedded in the firmware binary; keep the binary and device secure and do not
publish build artifacts. The intended AP must offer a 2.4 GHz mode supported
by RW007. Set `WIFI_LOCAL_SECURITY` to the actual AP mode and
`WIFI_LOCAL_CHANNEL` to its actual 2.4 GHz channel in the ignored header;
the template's WPA2/AES value is an example. This bundled RW007 protocol
limits the password to at most 31 characters; longer credentials are rejected
before the driver sends a join request. WPA3-only networks are unsupported.

The application sets `rt_wlan_info.hidden` from `WIFI_LOCAL_HIDDEN` and calls
`rt_wlan_connect_adv()` with the configured SSID, band, security, and channel.
Visible networks use `hidden=0` by default. The preliminary scan is only a
module diagnostic. The module firmware's interpretation of the hidden flag
remains unverified until a hidden-network test.
If association fails, investigate the module response before considering a
temporarily visible SSID or a separate test AP; do not silently change the
router.

## Local MQTT credentials

Run `projects/05_iot_wifi_manager/applications/configure_mqtt.ps1` in
PowerShell to enter the existing broker password at a hidden prompt. The
script writes the ignored `mqtt_local.h` for the existing `rt_spark` account
and refuses to overwrite a local file. Coordinate password rotation or account
changes with the broker administrator; do not commit this header or share a
credential-bearing firmware image. The publisher connects to the configured
broker and publishes the existing six-field sensor JSON to `activity/sensors`.

## Build on the existing Windows toolchain

Install SCons and an ARM GNU toolchain, then set `RTT_EXEC_PATH` to that
toolchain's `bin` directory. Run from the repository root in PowerShell:

```powershell
$env:RTT_EXEC_PATH = 'C:\path\to\arm-none-eabi\bin'
scons -C 'firmware_rtthread\projects\05_iot_wifi_manager' -Q
```

This uses the already installed ARM GCC and SCons bundled with PlatformIO;
PlatformIO itself does not build this RT-Thread target. The expected outputs
are `rt-thread.elf` and `rtthread.bin` inside the project directory. A
credential-free build completed successfully on 2026-10-20; the resulting
ELF is ARM32 hard-float and links at STM32 flash address `0x08000000`.
The latest local optimized binary is 173,356 bytes with SHA-256
`031CBE6772C2126F4EDE1230CE8D1F63F8EC4A4AFB80E0A2BC4C02427DC395A8`.
It was built with `RTT_BUILD=size` and includes the current local network
configuration, so this hash is specific to that private build. Do not publish
the binary: it embeds Wi-Fi/MQTT credentials. No current-image flash or live
MCU preservation verification has been performed.



## Serial verification sequence

At COM3/115200, require an RW007 ready-pin result, SPI2 driver initialization,
module firmware-version response, scan completion, association, and a valid
DHCP IP/gateway. A registered `wspi` device alone is insufficient evidence of
module communication. Sensor diagnostics should report WHO_AM_I `0xAF` and
changing six-axis values. RT-Thread owns SysTick; the fallback firmware's
bare-metal `SysTick_Handler()` must not be copied here. After Wi-Fi and DHCP
are confirmed, the included authenticated MQTT publisher should connect to
the broker and publish sensor windows. The current optimized image is not
flashed, and its live network path has not been validated.

## Onboard display

The official ST7789 V3 LCD driver is enabled in this target. It uses the
8-bit FSMC Bank 3 bus, PD3 reset, and PF9 GPIO backlight. The FSMC MSP setup
configures only the LCD's required address and data pins, leaving PF0/PF1
for the ICM-20608-G I2C2 bus. The application shows sensor and network status
from runtime state. Wi-Fi reports Connected only after RT-Thread WLAN is READY
with an IP address. The firmware includes an MQTT client; live broker
connectivity depends on the active firmware image and permitted network path.
The LCD driver initialized on the board and returned ID 0x81B3. Text visibility was not independently confirmed because no camera capture was available. Sensor identity and repeated motion readings were confirmed over COM3.
