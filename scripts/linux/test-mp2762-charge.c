/* SPDX-License-Identifier: GPL-2.0 */
/* Compile against the header from the candidate kernel, not a copied model. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "mp2762-charge-test.h"

struct fake_bus {
	unsigned int ctrl, wdt, status, writes, calls;
	int fail_call;
	bool failed_write_applies, corrupt_readback;
};

static int fake_read(void *context, unsigned int reg)
{
	struct fake_bus *bus = context;
	if ((int)bus->calls++ == bus->fail_call)
		return -EIO;
	if (reg == MP2762_CHARGE_CTRL)
		return bus->ctrl ^ (bus->corrupt_readback && bus->writes ? 1 : 0);
	if (reg == MP2762_CHARGE_WDT)
		return bus->wdt;
	assert(reg == MP2762_CHARGE_STATUS);
	return bus->status;
}

static int fake_write(void *context, unsigned int reg, unsigned int value)
{
	struct fake_bus *bus = context;
	bool fails = (int)bus->calls++ == bus->fail_call;
	assert(reg == MP2762_CHARGE_CTRL);
	assert(value <= 255);
	assert(!(value & MP2762_CHARGE_COMMANDS));
	/* The actual helper must preserve every other configuration bit. */
	assert(((value ^ bus->ctrl) & ~MP2762_CHARGE_ENABLE) == 0);
	bus->writes++;
	if (!fails || bus->failed_write_applies)
		bus->ctrl = value;
	return fails ? -EIO : 0;
}

static struct fake_bus initial(void)
{
	return (struct fake_bus){.ctrl = 0x16, .wdt = 0x4e,
		.status = MP2762_CHARGE_POWER_GOOD, .fail_call = -1};
}

int main(void)
{
	unsigned int ctrl, wdt, status, cases = 0;
	struct fake_bus bus;
	struct mp2762_charge_test test;
	struct mp2762_charge_io io = {.context = &bus,
		.read = fake_read, .write = fake_write};
	int ret, fault;

	for (ctrl = 0; ctrl < 256; ctrl++) {
		for (wdt = 0; wdt < 256; wdt++) {
			bus = initial(); bus.ctrl = ctrl; bus.wdt = wdt;
			test = (struct mp2762_charge_test){0};
			ret = mp2762_charge_inhibit(&io, &test);
			bool expected = !(ctrl & 0xe8) && (ctrl & 2) &&
				(ctrl & 0x10) && !(wdt & 0x30);
			assert((ret == 0) == expected);
			assert(bus.writes == (expected ? 1U : 0U));
			assert(test.owned == expected);
			if (expected) {
				assert(bus.ctrl == (ctrl & ~0x10U));
				assert(mp2762_charge_inhibit(&io, &test) == -EBUSY);
				assert(bus.writes == 1);
				assert(mp2762_charge_restore(&io, &test) == 0);
				assert(!test.owned && bus.ctrl == ctrl);
			}
			cases++;
		}
	}
	for (status = 0; status < 256; status++) {
		bus = initial(); bus.status = status;
		test = (struct mp2762_charge_test){0};
		ret = mp2762_charge_inhibit(&io, &test);
		assert((ret == 0) == ((status & 2) && !(status & 0xc0)));
		cases++;
	}
	/* Fault each read, the stop write, and stop readback. */
	for (fault = 0; fault < 5; fault++) {
		for (int applies = 0; applies < 2; applies++) {
			bus = initial(); bus.fail_call = fault;
			bus.failed_write_applies = applies;
			test = (struct mp2762_charge_test){0};
			assert(mp2762_charge_inhibit(&io, &test) == -EIO);
			assert(test.owned == (fault >= 3));
			bus.fail_call = -1;
			assert(mp2762_charge_restore(&io, &test) == 0);
			assert(!test.owned && bus.ctrl == 0x16);
			cases++;
		}
	}
	/* A restoration error must retain ownership, then allow a safe retry. */
	for (fault = 0; fault < 3; fault++) {
		for (int applies = 0; applies < 2; applies++) {
			bus = initial(); test = (struct mp2762_charge_test){0};
			assert(mp2762_charge_inhibit(&io, &test) == 0);
			bus.calls = 0; bus.fail_call = fault;
			bus.failed_write_applies = applies;
			assert(mp2762_charge_restore(&io, &test) == -EIO);
			assert(test.owned);
			bus.fail_call = -1;
			assert(mp2762_charge_restore(&io, &test) == 0);
			assert(!test.owned && bus.ctrl == 0x16);
			cases++;
		}
	}
	bus = initial(); test = (struct mp2762_charge_test){0};
	bus.corrupt_readback = true;
	assert(mp2762_charge_inhibit(&io, &test) == -EIO && test.owned);
	bus.corrupt_readback = false;
	assert(mp2762_charge_restore(&io, &test) == 0 && !test.owned);
	bus = initial(); bus.ctrl &= ~0x10U;
	test = (struct mp2762_charge_test){0};
	assert(mp2762_charge_auto(&io, &test) == -EOPNOTSUPP);
	assert(bus.writes == 0);
	bus.ctrl |= MP2762_CHARGE_ENABLE;
	assert(mp2762_charge_auto(&io, &test) == 0 && bus.writes == 0);
	/* Do not overwrite newly asserted reset/OTG/suspend/BATTFET state. */
	for (unsigned int change = 1; change <= 0x80; change <<= 1) {
		if (!(change & 0xea)) continue;
		bus = initial(); test = (struct mp2762_charge_test){0};
		assert(mp2762_charge_inhibit(&io, &test) == 0);
		bus.ctrl ^= change;
		assert(mp2762_charge_restore(&io, &test) < 0 && test.owned);
		assert(bus.writes == 1);
		cases++;
	}
	/* No ownership means no write, even if firmware already disabled charge. */
	bus = initial(); bus.ctrl &= ~0x10U;
	test = (struct mp2762_charge_test){0};
	assert(mp2762_charge_restore(&io, &test) == 0 && bus.calls == 0);
	assert(mp2762_charge_inhibit(&io, &test) == -EALREADY);
	assert(bus.writes == 0);
	/* Renewal validates the actual shared core without a single register write. */
	for (ctrl = 0; ctrl < 256; ctrl++) {
		for (wdt = 0; wdt < 256; wdt++) {
			bus = initial(); bus.ctrl = ctrl; bus.wdt = wdt;
			test = (struct mp2762_charge_test){.owned = true, .saved_enable = 0x10};
			ret = mp2762_charge_validate_lease(&io, &test);
			assert((ret == 0) == (!(ctrl & 0xf8) && (ctrl & 2) && !(wdt & 0x30)));
			assert(bus.writes == 0 && test.owned);
			cases++;
		}
	}
	for (status = 0; status < 256; status++) {
		bus = initial(); bus.ctrl = 0x06; bus.status = status;
		test = (struct mp2762_charge_test){.owned = true, .saved_enable = 0x10};
		ret = mp2762_charge_validate_lease(&io, &test);
		assert((ret == 0) == ((status & 2) && !(status & 0xc0)));
		assert(bus.writes == 0);
		cases++;
	}
	for (fault = 0; fault < 3; fault++) {
		bus = initial(); bus.ctrl = 0x06; bus.fail_call = fault;
		test = (struct mp2762_charge_test){.owned = true, .saved_enable = 0x10};
		assert(mp2762_charge_validate_lease(&io, &test) == -EIO);
		assert(bus.writes == 0);
		cases++;
	}
	bus = initial(); test = (struct mp2762_charge_test){0};
	assert(mp2762_charge_validate_lease(&io, &test) == -EPERM);
	assert(bus.calls == 0 && bus.writes == 0);
	puts("PASS: exhaustive stop/renew configurations, input status, I2C failures, uncertain writes, restoration retries and conflicts");
	printf("Cases: %u\n", cases);
	return 0;
}
