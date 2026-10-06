// SPDX-License-Identifier: GPL-2.0-only
/* IST8801 pair input driver for the verified kebab KB2000 board.
 * Board-specific runtime module: pending conversion to an OF I2C binding.
 * Register protocol: OnePlusOSS SM8250, 846e71f228ad605c75b53064c4c165a1361a0bfe,
 * drivers/misc/tri_state_key/ist_hall_ic/hall_ist8801{.h,_up.c} (Oplus GPL-2.0).
 * No reset, OTP, voltage changes, GPIO writes or automatic user-space actions.
 */
#include <linux/delay.h>
#include <linux/i2c.h>
#include <linux/input.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/regulator/consumer.h>
#include <linux/workqueue.h>
#include "kebab-alert-slider-state.h"

static DEFINE_MUTEX(lock);
static struct regulator *rail;
static struct i2c_client *sensors[2]; /* down, up */
static const u8 registers[] = { 0x01, 0x08, 0x20, 0x40 };
static u8 saved[2][ARRAY_SIZE(registers)];
static bool have_saved[2], owned, active;
static int raw_down, raw_up, last_error, restore_error;
static u8 st_down, st_up;
static unsigned long count, deadline, last_valid;
static unsigned int seconds = 180;
static bool persistent;
static struct input_dev *input;
static int calibration[6], calibration_count;
static unsigned int tolerance = 12;
static int profile = -1, pending = -1, reported = -1;
static unsigned int streak;
module_param_array(calibration, int, &calibration_count, 0444);
MODULE_PARM_DESC(calibration, "Optional six measured down/up centres: top,middle,bottom");
module_param(tolerance, uint, 0444);
module_param(persistent, bool, 0444);
MODULE_PARM_DESC(persistent, "Explicit persistent recognition mode; requires seconds=0 and calibration");
module_param(seconds, uint, 0444);
MODULE_PARM_DESC(seconds, "Sampling window in seconds, 10..600; supply released at end");
static void poll_sensors(struct work_struct *work);
static DECLARE_DELAYED_WORK(poll_work, poll_sensors);

static int read_bytes(struct i2c_client *client, u8 reg, u8 *data, u16 len)
{
	struct i2c_msg messages[] = {
		{ .addr = client->addr, .len = 1, .buf = &reg },
		{ .addr = client->addr, .flags = I2C_M_RD, .len = len, .buf = data },
	};
	int ret = i2c_transfer(client->adapter, messages, ARRAY_SIZE(messages));

	return ret == ARRAY_SIZE(messages) ? 0 : (ret < 0 ? ret : -EIO);
}

static int write_reg(struct i2c_client *client, u8 reg, u8 value)
{
	u8 data[] = { reg, value };
	int ret = i2c_master_send(client, data, sizeof(data));

	return ret == sizeof(data) ? 0 : (ret < 0 ? ret : -EIO);
}

/* Must be called with lock held, and only while owned. */
static void restore_registers(void)
{
	int i, j, ret;
	u8 value;

	for (i = 0; i < ARRAY_SIZE(sensors); i++) {
		if (!have_saved[i])
			continue;
		ret = write_reg(sensors[i], 0x08, 0);
		if (ret && !restore_error)
			restore_error = ret;
		/* Operation mode last, after restoring the saved configuration. */
		for (j = ARRAY_SIZE(registers) - 1; j >= 0; j--) {
			if (j == 1)
				continue;
			ret = write_reg(sensors[i], registers[j], saved[i][j]);
			if (!ret)
				ret = read_bytes(sensors[i], registers[j], &value, 1);
			if (!ret && value != saved[i][j])
				ret = -EIO;
			if (ret && !restore_error)
				restore_error = ret;
		}
		ret = write_reg(sensors[i], 0x08, saved[i][1]);
		if (!ret)
			ret = read_bytes(sensors[i], 0x08, &value, 1);
		if (!ret && value != saved[i][1])
			ret = -EIO;
		if (ret && !restore_error)
			restore_error = ret;
		have_saved[i] = false;
	}
}

static int stop_sampling(void)
{
	int ret = 0;

	active = false;
	profile = -1;
	if (input) {
		input_unregister_device(input);
		input = NULL;
	}
	if (owned) {
		restore_registers();
		ret = regulator_disable(rail);
		if (!ret)
			owned = false;
	}
	return ret;
}

static int sample_pair(void)
{
	u8 data[2][3];
	int ret = read_bytes(sensors[0], 0x10, data[0], 3);
	if (!ret)
		ret = read_bytes(sensors[1], 0x10, data[1], 3);
	last_error = ret;
	if (!ret) {
		st_down = data[0][0];
		st_up = data[1][0];
		if ((st_down & 5) && (st_up & 5)) {
			raw_down = (s16)((data[0][2] << 8) | data[0][1]);
			raw_up = (s16)((data[1][2] << 8) | data[1][1]);
			count++;
			last_valid = jiffies;
			return 0;
		}
	}
	return ret ? ret : -EAGAIN;
}

static void poll_sensors(struct work_struct *work)
{
	int ret, stable;

	mutex_lock(&lock);
	if (!active || (!persistent && time_after_eq(jiffies, deadline))) {
		ret = stop_sampling();
		if (ret) {
			last_error = ret;
			schedule_delayed_work(&poll_work, HZ);
		} else {
			pr_info("kebab-slider-raw: stopped, samples=%lu restore_error=%d\n",
				count, restore_error);
		}
		goto out;
	}
	ret = sample_pair();
	if (ret && ret != -EAGAIN)
		pr_warn_ratelimited("kebab-slider: sample failed: %d\n", ret);
	if (calibration_count == 6) {
		profile = ret ? -1 : slider_classify(calibration, tolerance, raw_down, raw_up);
		stable = slider_debounce(profile, &pending, &streak);
		if (input && stable >= 0 && stable != reported) {
			input_report_abs(input, ABS_SND_PROFILE, stable);
			input_sync(input);
			reported = stable;
		}
	}
	schedule_delayed_work(&poll_work, msecs_to_jiffies(100));
out:
	mutex_unlock(&lock);
}

static int snapshot_get(char *buffer, const struct kernel_param *parameter)
{
	int ret;

	mutex_lock(&lock);
	ret = scnprintf(buffer, PAGE_SIZE,
		"{\"active\":%s,\"owned\":%s,\"count\":%lu,\"down\":%d,\"up\":%d,"
		"\"status_down\":%u,\"status_up\":%u,\"age_ms\":%ld,"
		"\"error\":%d,\"restore_error\":%d,\"profile\":%d,\"reported\":%d}\n",
		active ? "true" : "false", owned ? "true" : "false", count,
		raw_down, raw_up, st_down, st_up,
		count ? (long)jiffies_to_msecs(jiffies - last_valid) : -1L,
		last_error, restore_error, profile, reported);
	mutex_unlock(&lock);
	return ret;
}
static const struct kernel_param_ops snapshot_ops = { .get = snapshot_get };
module_param_cb(snapshot, &snapshot_ops, NULL, 0444);

static void free_clients(void)
{
	int i;

	for (i = 0; i < ARRAY_SIZE(sensors); i++)
		if (sensors[i]) {
			i2c_unregister_device(sensors[i]);
			sensors[i] = NULL;
		}
}

static int __init slider_raw_init(void)
{
	struct i2c_adapter *adapter;
	u8 id, cntl2;
	int i, j, ret;

	if (!of_machine_is_compatible("oneplus,kebab"))
		return -ENODEV;
	if ((persistent && (seconds != 0 || calibration_count != 6)) ||
	    (!persistent && (seconds < 10 || seconds > 600)))
		return -EINVAL;
	if (calibration_count && (calibration_count != 6 ||
	    !slider_calibration_valid(calibration, tolerance)))
		return -EINVAL;
	adapter = i2c_get_adapter(1);
	if (!adapter)
		return -ENODEV;
	if (!strstr(dev_name(adapter->dev.parent), "984000.i2c") ||
	    !i2c_check_functionality(adapter, I2C_FUNC_I2C)) {
		ret = -ENODEV;
		goto adapter_put;
	}
	for (i = 0; i < ARRAY_SIZE(sensors); i++) {
		sensors[i] = i2c_new_dummy_device(adapter, 0x18 + i);
		if (IS_ERR(sensors[i])) {
			ret = PTR_ERR(sensors[i]);
			sensors[i] = NULL;
			goto clients_free;
		}
	}
	rail = regulator_get_optional(NULL, "vreg_l10c_3p0");
	if (IS_ERR(rail)) {
		ret = PTR_ERR(rail);
		goto clients_free;
	}
	if (regulator_get_voltage(rail) != 3000000 || regulator_is_enabled(rail) != 0) {
		ret = -EBUSY;
		goto rail_put;
	}
	ret = regulator_enable(rail);
	if (ret)
		goto rail_put;
	owned = true;
	msleep(20);
	/* Verify both chips and save all state before any register write. */
	for (i = 0; i < ARRAY_SIZE(sensors); i++) {
		ret = read_bytes(sensors[i], 0x09, &id, 1);
		if (ret || id != 0x81) {
			ret = ret ? ret : -ENODEV;
			goto stop;
		}
		for (j = 0; j < ARRAY_SIZE(registers); j++) {
			ret = read_bytes(sensors[i], registers[j], &saved[i][j], 1);
			if (ret)
				goto stop;
		}
		ret = read_bytes(sensors[i], 0x0d, &cntl2, 1);
		if (ret)
			goto stop;
		/* Calibration is specific to this observed ADC/gain configuration. */
		if (calibration_count == 6 && cntl2 != 0x28) {
			ret = -EINVAL;
			goto stop;
		}
		pr_info("kebab-slider-raw: addr=%02x id=%02x INTSRS=%02x OPF=%02x ACTION=%02x IFCNTL=%02x CNTL2=%02x\n",
			sensors[i]->addr, id, saved[i][0], saved[i][1], saved[i][2], saved[i][3], cntl2);
	}
	for (i = 0; i < ARRAY_SIZE(sensors); i++) {
		have_saved[i] = true; /* even a failed write may have reached the chip */
		ret = write_reg(sensors[i], 0x08, 0);
		if (!ret)
			ret = write_reg(sensors[i], 0x01, saved[i][0] & ~0x80);
		if (!ret)
			ret = write_reg(sensors[i], 0x20, 0);
		if (!ret)
			ret = write_reg(sensors[i], 0x40, saved[i][3] | 0x04);
		if (!ret)
			ret = write_reg(sensors[i], 0x08, 0x70); /* documented 20Hz */
		if (ret)
			goto stop;
	}
	if (calibration_count == 6) {
		/* Initialise the axis to a verified stable position, never default silent. */
		for (i = 0; i < 10; i++) {
			msleep(100);
			ret = sample_pair();
			profile = ret ? -1 : slider_classify(calibration, tolerance, raw_down, raw_up);
			reported = slider_debounce(profile, &pending, &streak);
			if (reported >= 0)
				break;
		}
		if (reported < 0) {
			ret = -ENODATA;
			goto stop;
		}
		input = input_allocate_device();
		if (!input) {
			ret = -ENOMEM;
			goto stop;
		}
		input->name = "OnePlus 8T Alert Slider";
		input->phys = "i2c-1/ist8801-pair";
		input->id.bustype = BUS_I2C;
		input->dev.parent = &sensors[0]->dev;
		input_set_abs_params(input, ABS_SND_PROFILE, 0, 2, 0, 0);
		input_abs_set_val(input, ABS_SND_PROFILE, reported);
		ret = input_register_device(input);
		if (ret) {
			input_free_device(input);
			input = NULL;
			goto stop;
		}
	}
	active = true;
	deadline = jiffies + seconds * HZ;
	schedule_delayed_work(&poll_work, msecs_to_jiffies(100));
	i2c_put_adapter(adapter);
	return 0;
stop:
	mutex_lock(&lock);
	if (stop_sampling())
		pr_err("kebab-slider-raw: init cleanup could not release supply\n");
	mutex_unlock(&lock);
rail_put:
	regulator_put(rail);
clients_free:
	free_clients();
adapter_put:
	i2c_put_adapter(adapter);
	return ret;
}

static void __exit slider_raw_exit(void)
{
	cancel_delayed_work_sync(&poll_work);
	mutex_lock(&lock);
	if (stop_sampling())
		pr_err("kebab-slider-raw: unload supply release failed\n");
	pr_info("kebab-slider-raw: unload restore_error=%d owned=%d\n", restore_error, owned);
	mutex_unlock(&lock);
	regulator_put(rail);
	free_clients();
}

module_init(slider_raw_init);
module_exit(slider_raw_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Verified kebab IST8801 pair ABS_SND_PROFILE driver");
