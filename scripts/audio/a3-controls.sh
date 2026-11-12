#!/bin/bash
set -u
C=${1:-0}
mapfile -t NAMES < <(amixer -c $C scontrols | sed -n "s/^Simple mixer control '\(.*\)',[0-9]*\$/\1/p")
echo "== VA* names =="
printf '%s\n' "${NAMES[@]}" | grep -E '^VA' || echo none
echo
echo "== targeted sget =="
for n in "VA DMIC MUX0" "VA DMIC MUX1" "VA DEC0 MUX" "VA_AIF1_CAP Mixer DEC0" "VA_DEC0"; do
  echo "---- $n"
  amixer -c $C sget "$n" 2>&1 | sed -n '1,8p'
done
