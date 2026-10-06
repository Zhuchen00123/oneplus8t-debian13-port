// SPDX-License-Identifier: GPL-2.0-only
#include <assert.h>
#include <limits.h>
#include <stdio.h>
#include "kebab-alert-slider-state.h"

int main(void)
{
	/* Synthetic centres; never publish a handset's calibration. */
	const int centres[6] = { 50, 150, 100, 100, 150, 50 };
	const int overlap[6] = { 50, 150, 51, 151, 150, 50 };
	const int extreme[6] = { INT_MIN, 0, 100, 100, 200, 200 };
	int pending = -1, i, down, up, result;
	unsigned int streak = 0, checks = 0;

	assert(slider_calibration_valid(centres, 12));
	assert(!slider_calibration_valid(overlap, 12));
	assert(!slider_calibration_valid(extreme, 12));
	assert(!slider_calibration_valid(centres, 0));
	assert(!slider_calibration_valid(centres, INT_MAX));
	for (i = 0; i < 3; i++) {
		for (down = -12; down <= 12; down++)
			for (up = -12; up <= 12; up++) {
				assert(slider_classify(centres, 12, centres[2*i] + down,
					centres[2*i+1] + up) == i);
				checks++;
			}
		assert(slider_classify(centres, 12, centres[2*i] + 13,
			centres[2*i+1]) == -1);
	}
	/* Every point in a broad sweep is either in exactly one window or rejected. */
	for (down = -180; down <= 330; down++)
		for (up = -180; up <= 330; up++) {
			int matches = 0;
			for (i = 0; i < 3; i++)
				matches += slider_abs(down-centres[2*i]) <= 12 &&
					slider_abs(up-centres[2*i+1]) <= 12;
			result = slider_classify(centres, 12, down, up);
			assert(matches <= 1);
			assert((result >= 0) == (matches == 1));
			checks++;
		}
	assert(slider_classify(centres, 12, INT_MIN, INT_MAX) == -1);
	assert(slider_classify(centres, INT_MAX, 0, 0) == -1);
	assert(slider_debounce(0, &pending, &streak) == -1);
	assert(slider_debounce(0, &pending, &streak) == -1);
	assert(slider_debounce(0, &pending, &streak) == 0);
	assert(slider_debounce(1, &pending, &streak) == -1);
	assert(slider_debounce(-1, &pending, &streak) == -1);
	assert(streak == 0 && pending == -1);
	assert(slider_debounce(1, &pending, &streak) == -1);
	assert(slider_debounce(1, &pending, &streak) == -1);
	assert(slider_debounce(1, &pending, &streak) == 1);
	for (i = 0; i < 100000; i++)
		assert(slider_debounce(1, &pending, &streak) == 1);
	assert(streak == 3);
	assert(slider_debounce(3, &pending, &streak) == -1);
	printf("PASS: %u classification points, bounds, overlap and debounce checks\n", checks);
	return 0;
}
