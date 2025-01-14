#include "shared-bindings/dotclockframebuffer/__init__.h"

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#define DELAY (0x80)

static void pin_change(dotclockframebuffer_ioexpander_spi_bus *bus, uint32_t set_bits, uint32_t clear_bits) {
    uint32_t data = (bus->addr_reg_shadow.u32 & ~clear_bits) | set_bits;
    // no way to signal failure to caller!
    (void)common_hal_busio_i2c_write(bus->bus, bus->i2c_device_address, (uint8_t *)&data, bus->i2c_write_size);
    bus->addr_reg_shadow.u32 = data;
}

static void ioexpander_bus_send(dotclockframebuffer_ioexpander_spi_bus *bus,
    bool is_command,
    const uint8_t *data, uint32_t data_length) {

    int dc_mask = is_command ? 0 : 0x100;
    for (uint32_t i = 0; i < data_length; i++) {
        int bits = data[i] | dc_mask;

        for (uint32_t j = 0; j < 9; j++) {
            // CPOL=CPHA=0: output fresh data on falling edge of clk or cs
            if (bits & 0x100) {
                pin_change(bus, /* set */ bus->mosi_mask, /* clear */ bus->clk_mask | bus->cs_mask);
            } else {
                pin_change(bus, /* set */ 0, /* clear */ bus->mosi_mask | bus->clk_mask | bus->cs_mask);
            }
            // Display latches bit on rising edge of CLK
            pin_change(bus, /* set */ bus->clk_mask, /* clear */ 0);

            // next bit
            bits <<= 1;
        }
    }
}

// Send a circuitpython-style display initialization sequence over an i2c-attached bus expander
// This always assumes
//  * 9-bit SPI (no DC pin)
//  * CPOL=CPHA=0
//  * CS deasserted after each init sequence step, but not otherwise just like
//    displayio fourwire bus without data_as_commands
void dotclockframebuffer_ioexpander_send_init_sequence(dotclockframebuffer_ioexpander_spi_bus *bus, const uint8_t *init_sequence, uint16_t init_sequence_len) {
    while (!common_hal_busio_i2c_try_lock(bus->bus)) {
        RUN_BACKGROUND_TASKS;
    }

    // ensure deasserted CS and idle CLK
    pin_change(bus, /* set */ bus->cs_mask, /* clear */ bus->clk_mask);

    for (uint32_t i = 0; i < init_sequence_len; /* NO INCREMENT */) {
        const uint8_t *cmd = init_sequence + i;
        uint8_t data_size = *(cmd + 1);
        bool delay = (data_size & DELAY) != 0;
        data_size &= ~DELAY;
        const uint8_t *data = cmd + 2;

        ioexpander_bus_send(bus, true, cmd, 1);
        ioexpander_bus_send(bus, false, data, data_size);

        // idle CLK
        pin_change(bus, 0, /* clear */ bus->clk_mask);
        // deassert CS
        pin_change(bus, /* set */ bus->cs_mask, 0);

        if (delay) {
            data_size++;
            uint16_t delay_length_ms = *(cmd + 1 + data_size);
            if (delay_length_ms == 255) {
                delay_length_ms = 500;
            }
            mp_hal_delay_ms(delay_length_ms);
        }
        i += 2 + data_size;
    }
    common_hal_busio_i2c_unlock(bus->bus);
}
