// SPDX-License-Identifier: GPL-2.0-only
/* Verified KB2000 IST8801 pair; register protocol from OnePlusOSS SM8250
 * 846e71f228ad605c75b53064c4c165a1361a0bfe, drivers/misc/tri_state_key/
 * ist_hall_ic/hall_ist8801{.h,_up.c} (Oplus GPL-2.0).
 * Device-tree supply dependencies replace runtime adapter/rail discovery.
 * No reset, OTP, gain, voltage, GPIO or charger register writes.
 */
#include <linux/delay.h>
#include <linux/i2c.h>
#include <linux/input.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/property.h>
#include <linux/regulator/consumer.h>
#include <linux/workqueue.h>
#include "kebab-alert-slider-state.h"

struct kebab_slider {
	struct device *dev;
	struct i2c_client *sensors[2]; /* down, up */
	struct regulator *rail;
	struct input_dev *input;
	struct mutex lock;
	struct delayed_work poll_work;
	u8 saved[2][4];
	bool have_saved[2], owned, active, registered;
	int centres[6], tolerance;
	int down, up, error, restore_error;
	u8 status_down, status_up;
	unsigned long count, last_valid;
	int profile, pending, reported;
	unsigned int streak;
};

static const u8 saved_registers[] = { 0x01, 0x08, 0x20, 0x40 };
/* Compatibility with the deployed read-only UI snapshot. Protect its lifetime. */
static DEFINE_MUTEX(snapshot_lock);
static struct kebab_slider *snapshot_device;

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

static void restore_registers(struct kebab_slider *s)
{
	int i, j, ret;
	u8 value;

	for (i = 0; i < ARRAY_SIZE(s->sensors); i++) {
		if (!s->have_saved[i])
			continue;
		ret = write_reg(s->sensors[i], 0x08, 0);
		if (ret && !s->restore_error)
			s->restore_error = ret;
		for (j = ARRAY_SIZE(saved_registers) - 1; j >= 0; j--) {
			if (j == 1)
				continue;
			ret = write_reg(s->sensors[i], saved_registers[j], s->saved[i][j]);
			if (!ret)
				ret = read_bytes(s->sensors[i], saved_registers[j], &value, 1);
			if (!ret && value != s->saved[i][j])
				ret = -EIO;
			if (ret && !s->restore_error)
				s->restore_error = ret;
		}
		ret = write_reg(s->sensors[i], 0x08, s->saved[i][1]);
		if (!ret)
			ret = read_bytes(s->sensors[i], 0x08, &value, 1);
		if (!ret && value != s->saved[i][1])
			ret = -EIO;
		if (ret && !s->restore_error)
			s->restore_error = ret;
		s->have_saved[i] = false;
	}
}

/* Called with the device lock held. */
static void slider_release_power(struct kebab_slider *s)
{
	int ret;

	if (s->owned) {
		restore_registers(s);
		ret = regulator_disable(s->rail);
		if (ret)
			dev_err(s->dev, "supply release failed: %d\n", ret);
		else
			s->owned = false;
	}
	if (s->restore_error)
		dev_err(s->dev, "saved-state restore failed: %d\n", s->restore_error);
}

static void slider_cleanup(void *data)
{
	struct kebab_slider *s = data;

	mutex_lock(&snapshot_lock);
	if (snapshot_device == s)
		snapshot_device = NULL;
	mutex_unlock(&snapshot_lock);
	mutex_lock(&s->lock);
	s->active = false;
	s->profile = -1;
	mutex_unlock(&s->lock);
	cancel_delayed_work_sync(&s->poll_work);
	mutex_lock(&s->lock);
	slider_release_power(s);
	mutex_unlock(&s->lock);
}

static int sample_pair(struct kebab_slider *s)
{
	u8 data[2][3];
	int ret = read_bytes(s->sensors[0], 0x10, data[0], 3);

	if (!ret)
		ret = read_bytes(s->sensors[1], 0x10, data[1], 3);
	if (ret)
		return ret;
	s->status_down = data[0][0];
	s->status_up = data[1][0];
	if (!(s->status_down & 5) || !(s->status_up & 5))
		return -EAGAIN;
	s->down = (s16)((data[0][2] << 8) | data[0][1]);
	s->up = (s16)((data[1][2] << 8) | data[1][1]);
	s->count++;
	s->last_valid = jiffies;
	return 0;
}

static void slider_poll(struct work_struct *work)
{
	struct kebab_slider *s = container_of(to_delayed_work(work),
					    struct kebab_slider, poll_work);
	int ret, stable;

	mutex_lock(&s->lock);
	if (!s->active)
		goto out;
	ret = sample_pair(s);
	s->error = ret == -EAGAIN ? 0 : ret;
	if (s->error)
		dev_warn_ratelimited(s->dev, "sample failed: %d\n", s->error);
	s->profile = ret ? -1 : slider_classify(s->centres, s->tolerance,
						 s->down, s->up);
	stable = slider_debounce(s->profile, &s->pending, &s->streak);
	if (stable >= 0) {
		/* Never publish a guessed initial axis. Out-of-range readings keep
		 * polling and may recover; they do not permanently fail cold probe.
		 */
		if (!s->registered) {
			input_abs_set_val(s->input, ABS_SND_PROFILE, stable);
			ret = input_register_device(s->input);
			if (ret) {
				s->error = ret;
				s->active = false;
				dev_err(s->dev, "input registration failed: %d\n", ret);
				slider_release_power(s);
				goto out;
			}
			s->registered = true;
			s->reported = stable;
		} else if (stable != s->reported) {
			input_report_abs(s->input, ABS_SND_PROFILE, stable);
			input_sync(s->input);
			s->reported = stable;
		}
	}
	schedule_delayed_work(&s->poll_work, msecs_to_jiffies(100));
out:
	mutex_unlock(&s->lock);
}

static int snapshot_get(char *buffer, const struct kernel_param *parameter)
{
	struct kebab_slider *s;
	int ret;

	mutex_lock(&snapshot_lock);
	s = snapshot_device;
	if (!s) {
		ret = scnprintf(buffer, PAGE_SIZE,
			"{\"active\":false,\"owned\":false,\"count\":0,\"profile\":-1,"
			"\"reported\":-1,\"age_ms\":-1,\"error\":0,\"restore_error\":0}\n");
		goto out;
	}
	mutex_lock(&s->lock);
	ret = scnprintf(buffer, PAGE_SIZE,
		"{\"active\":%s,\"owned\":%s,\"count\":%lu,\"down\":%d,\"up\":%d,"
		"\"status_down\":%u,\"status_up\":%u,\"age_ms\":%ld,"
		"\"error\":%d,\"restore_error\":%d,\"profile\":%d,\"reported\":%d}\n",
		s->active ? "true" : "false", s->owned ? "true" : "false", s->count,
		s->down, s->up, s->status_down, s->status_up,
		s->count ? (long)jiffies_to_msecs(jiffies - s->last_valid) : -1L,
		s->error, s->restore_error, s->profile, s->reported);
	mutex_unlock(&s->lock);
out:
	mutex_unlock(&snapshot_lock);
	return ret;
}
static const struct kernel_param_ops snapshot_ops = { .get = snapshot_get };
module_param_cb(snapshot, &snapshot_ops, NULL, 0444);

static int slider_probe(struct i2c_client *client)
{
	struct device *dev = &client->dev;
	struct kebab_slider *s;
	u32 centres[6], tolerance, companion;
	u8 id, cntl2;
	int i, j, ret;

	if (!of_machine_is_compatible("oneplus,kebab") || client->addr != 0x18 ||
	    !i2c_check_functionality(client->adapter, I2C_FUNC_I2C))
		return -ENODEV;
	s = devm_kzalloc(dev, sizeof(*s), GFP_KERNEL);
	if (!s)
		return -ENOMEM;
	s->dev = dev;
	s->profile = s->pending = s->reported = -1;
	mutex_init(&s->lock);
	INIT_DELAYED_WORK(&s->poll_work, slider_poll);
	ret = device_property_read_u32_array(dev, "oneplus,calibration", centres, 6);
	if (ret)
		return dev_err_probe(dev, ret, "six measured calibration values required\n");
	for (i = 0; i < ARRAY_SIZE(centres); i++)
		s->centres[i] = (s32)centres[i];
	ret = device_property_read_u32(dev, "oneplus,tolerance", &tolerance);
	if (ret || tolerance > 1000)
		return -EINVAL;
	s->tolerance = tolerance;
	if (!slider_calibration_valid(s->centres, s->tolerance))
		return -EINVAL;
	ret = device_property_read_u32(dev, "oneplus,companion-address", &companion);
	if (ret || companion != 0x19)
		return -EINVAL;
	s->rail = devm_regulator_get(dev, "vdd");
	if (IS_ERR(s->rail))
		return dev_err_probe(dev, PTR_ERR(s->rail), "vdd supply unavailable\n");
	ret = regulator_get_voltage(s->rail);
	if (ret < 0)
		return dev_err_probe(dev, ret, "vdd voltage unavailable\n");
	if (ret != 3000000)
		return -EINVAL;
	s->sensors[0] = client;
	s->sensors[1] = devm_i2c_new_dummy_device(dev, client->adapter, companion);
	if (IS_ERR(s->sensors[1]))
		return dev_err_probe(dev, PTR_ERR(s->sensors[1]), "companion address busy\n");
	s->input = devm_input_allocate_device(dev);
	if (!s->input)
		return -ENOMEM;
	s->input->name = "OnePlus 8T Alert Slider";
	s->input->phys = "ist8801-pair/input0";
	s->input->id.bustype = BUS_I2C;
	input_set_abs_params(s->input, ABS_SND_PROFILE, 0, 2, 0, 0);
	/* Cleanup runs before input, companion and regulator devres release. */
	ret = devm_add_action_or_reset(dev, slider_cleanup, s);
	if (ret)
		return ret;
	/* RPMh enabled state may be unknown during cold boot. A regular supply
	 * reference is sufficient; other consumers retain their own references.
	 */
	ret = regulator_enable(s->rail);
	if (ret)
		return dev_err_probe(dev, ret, "vdd enable failed\n");
	s->owned = true;
	msleep(20);
	for (i = 0; i < ARRAY_SIZE(s->sensors); i++) {
		ret = read_bytes(s->sensors[i], 0x09, &id, 1);
		if (ret || id != 0x81)
			return dev_err_probe(dev, ret ? ret : -ENODEV, "IST8801 ID mismatch\n");
		for (j = 0; j < ARRAY_SIZE(saved_registers); j++) {
			ret = read_bytes(s->sensors[i], saved_registers[j], &s->saved[i][j], 1);
			if (ret)
				return dev_err_probe(dev, ret, "state read failed\n");
		}
		ret = read_bytes(s->sensors[i], 0x0d, &cntl2, 1);
		if (ret || cntl2 != 0x28)
			return dev_err_probe(dev, ret ? ret : -EINVAL, "ADC/gain mismatch\n");
	}
	for (i = 0; i < ARRAY_SIZE(s->sensors); i++) {
		s->have_saved[i] = true;
		ret = write_reg(s->sensors[i], 0x08, 0);
		if (!ret)
			ret = write_reg(s->sensors[i], 0x01, s->saved[i][0] & ~0x80);
		if (!ret)
			ret = write_reg(s->sensors[i], 0x20, 0);
		if (!ret)
			ret = write_reg(s->sensors[i], 0x40, s->saved[i][3] | 0x04);
		if (!ret)
			ret = write_reg(s->sensors[i], 0x08, 0x70); /* 20 Hz */
		if (ret)
			return dev_err_probe(dev, ret, "measurement setup failed\n");
	}
	mutex_lock(&snapshot_lock);
	if (snapshot_device) {
		mutex_unlock(&snapshot_lock);
		return -EBUSY;
	}
	snapshot_device = s;
	mutex_lock(&s->lock);
	s->active = true;
	mutex_unlock(&s->lock);
	mutex_unlock(&snapshot_lock);
	i2c_set_clientdata(client, s);
	schedule_delayed_work(&s->poll_work, msecs_to_jiffies(100));
	return 0;
}

static void slider_remove(struct i2c_client *client)
{
	slider_cleanup(i2c_get_clientdata(client));
}

static const struct of_device_id slider_of_match[] = {
	{ .compatible = "oneplus,kebab-alert-slider" },
	{ }
};
MODULE_DEVICE_TABLE(of, slider_of_match);

static struct i2c_driver slider_driver = {
	.driver = {
		.name = "kebab-alert-slider",
		.of_match_table = slider_of_match,
	},
	.probe = slider_probe,
	.remove = slider_remove,
	.shutdown = slider_remove,
};
module_i2c_driver(slider_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("KB2000 IST8801 pair with dependency-aware cold probe");
