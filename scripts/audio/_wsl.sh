#!/bin/bash
set -u
T=/home/zhuchen/oneplus8t-venus-20261105/linux
echo "== tfa2 dai =="
sed -n '660,700p' $T/sound/soc/codecs/tfa2.c
echo "== tfa2 tdm/bringup constants =="
sed -n '360,430p' $T/sound/soc/codecs/tfa2.c
