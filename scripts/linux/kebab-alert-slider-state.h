/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef KEBAB_ALERT_SLIDER_STATE_H
#define KEBAB_ALERT_SLIDER_STATE_H

/* Reject overlapping calibration windows and magnetic readings outside them.
 * The three centres are ordered silent, vibrate, ring; each is down, up.
 * Bounds avoid overflow for untrusted module parameters and host fuzz inputs.
 */
static inline int slider_abs(int value)
{
	return value < 0 ? -value : value;
}

static inline int slider_calibration_valid(const int centres[6], int tolerance)
{
	int i, j;

	if (tolerance < 1 || tolerance > 1000)
		return 0;
	for (i = 0; i < 6; i++)
		if (centres[i] < -32768 || centres[i] > 32767)
			return 0;
	for (i = 0; i < 3; i++)
		for (j = i + 1; j < 3; j++)
			if (slider_abs(centres[2 * i] - centres[2 * j]) <= 2 * tolerance &&
			    slider_abs(centres[2 * i + 1] - centres[2 * j + 1]) <= 2 * tolerance)
				return 0;
	return 1;
}

static inline int slider_classify(const int centres[6], int tolerance, int down, int up)
{
	int i;

	if (tolerance < 1 || tolerance > 1000 ||
	    down < -32768 || down > 32767 || up < -32768 || up > 32767)
		return -1;
	for (i = 0; i < 3; i++)
		if (slider_abs(down - centres[2 * i]) <= tolerance &&
		    slider_abs(up - centres[2 * i + 1]) <= tolerance)
			return i;
	return -1;
}

static inline int slider_debounce(int value, int *pending, unsigned int *streak)
{
	if (value < 0 || value > 2) {
		*pending = -1;
		*streak = 0;
		return -1;
	}
	if (*pending != value) {
		*pending = value;
		*streak = 1;
	} else if (*streak < 3) {
		(*streak)++;
	}
	return *streak >= 3 ? value : -1;
}
#endif
