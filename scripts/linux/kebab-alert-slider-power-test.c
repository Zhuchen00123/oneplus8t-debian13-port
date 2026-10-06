// SPDX-License-Identifier: GPL-2.0-only
/* Diagnostic only: hold the existing L10C voltage for at most 30 seconds.
 * Does not alter voltage, GPIO, sensor registers or persistent configuration.
 */
#include <linux/delay.h>
#include <linux/module.h>
#include <linux/regulator/consumer.h>
#include <linux/workqueue.h>

static struct regulator *rail;
static bool owned;
static void release_rail(struct work_struct *work);
static DECLARE_DELAYED_WORK(release_work, release_rail);

static void release_rail(struct work_struct *work)
{
	int ret;

	if (!owned)
		return;
	ret = regulator_disable(rail);
	if (ret) {
		pr_err("kebab-slider-power-test: release failed %d, retrying\n", ret);
		schedule_delayed_work(&release_work, HZ);
		return;
	}
	owned = false;
	pr_info("kebab-slider-power-test: released L10C enable reference\n");
}

static int __init slider_power_init(void)
{
	int ret, voltage;

	rail = regulator_get_optional(NULL, "vreg_l10c_3p0");
	if (IS_ERR(rail))
		return PTR_ERR(rail);
	voltage = regulator_get_voltage(rail);
	if (voltage != 3000000) {
		pr_err("kebab-slider-power-test: unexpected voltage %d\n", voltage);
		ret = -EINVAL;
		goto put;
	}
	ret = regulator_enable(rail);
	if (ret)
		goto put;
	owned = true;
	msleep(20);
	pr_info("kebab-slider-power-test: L10C 3000000uV enabled for 30s\n");
	schedule_delayed_work(&release_work, 30 * HZ);
	return 0;
put:
	regulator_put(rail);
	return ret;
}

static void __exit slider_power_exit(void)
{
	int ret;

	cancel_delayed_work_sync(&release_work);
	if (owned) {
		ret = regulator_disable(rail);
		if (ret)
			pr_err("kebab-slider-power-test: unload release failed %d\n", ret);
		else
			owned = false;
	}
	regulator_put(rail);
}

module_init(slider_power_init);
module_exit(slider_power_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Bounded kebab Hall supply diagnostic at existing voltage");
