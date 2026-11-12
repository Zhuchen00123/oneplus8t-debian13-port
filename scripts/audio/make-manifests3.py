#!/usr/bin/env python3
"""Freeze the manifest for the UCM-closing run.

Budget accounting is stated here, before anything runs:
  * an ENABLE action introduces a route and charges route_variant 1
  * a DISABLE action returns to the audited baseline and charges nothing
  * selecting a verb writes no control and charges nothing
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("mm2", os.path.join(HERE, "make-manifests2.py"))
mm2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mm2)
mm = mm2.mm
core = mm2.core


def main():
    run_id, srv, card, cap_pcm, play_pcm, ucm_root, seed = sys.argv[1:8]
    card = int(card)
    with open(seed) as fh:
        blob = json.load(fh)
        controls = blob.get("controls") or blob.get("live") or {}
    print("seeded %d controls from %s" % (len(controls), seed))
    core.dump_json(os.path.join(srv, "control-map.json"),
                   {"schema": 1, "run_id": run_id, "card_index": card,
                    "provenance": "live:%s" % os.path.basename(seed),
                    "collected_wall_utc": __import__("time").strftime(
                        "%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
                    "controls": controls}, 0o600)

    def val(name):
        return controls[name]["value"]

    mute = [{"name": n, "values": [0]} for n in (
        "TERT_MI2S_RX Audio Mixer MultiMedia1",
        "MultiMedia2 Mixer VA_CODEC_DMA_TX_0",
        "VA_AIF1_CAP Mixer DEC0") if n in controls]
    if "VA DMIC MUX0" in controls:
        mute.append({"name": "VA DMIC MUX0", "values": [0]})
    mic_route = [{"name": n, "values": [v]} for n, v in (
        ("MultiMedia2 Mixer VA_CODEC_DMA_TX_0", 1),
        ("VA_AIF1_CAP Mixer DEC0", 1),
        ("VA DMIC MUX0", 2)) if n in controls]
    spk_route = [{"name": "TERT_MI2S_RX Audio Mixer MultiMedia1", "values": [1]}]

    def ucm(aid, op, arg, cost=None):
        a = {"id": aid, "kind": "ucm", "op": op, "arg": arg, "card": "O8T",
             "env": {"ALSA_CONFIG_UCM2": ucm_root}, "timeout_s": 20}
        if cost:
            a["cost"] = cost
        return a

    manifest = {
        "schema": 1, "run_id": run_id, "phase": "UCM closure + right-channel replication",
        "boot_id": core.read_text("/proc/sys/kernel/random/boot_id", "").strip(),
        "kernel_release": os.uname().release,
        "card_index": card, "capture_pcm": cap_pcm, "play_pcm": play_pcm,
        "stop_actions": mute,
        "budget_notes": [
            "enable charges route_variant 1; disable returns to baseline and charges 0;",
            "selecting a verb writes no control and charges 0",
        ],
        "accept": {"min_snr_db": 10.0, "min_abs_corr": 0.6,
                   "max_clipped_fraction": 0.0, "require_nonzero": True},
        "notes": [
            "same frozen thresholds; nothing retuned",
            "the left channel is not probed again: run 20261112t1225z already spent "
            "its two attempts on that hypothesis",
        ],
        "actions": [
            {"id": "load-tfa2", "kind": "modprobe", "module": "snd_soc_tfa2",
             "cost": {"module_load": 1}, "timeout_s": 20},
            {"id": "binding", "kind": "verify", "check": "binding",
             "read_only": True, "timeout_s": 20},
            {"id": "noise-cap", "kind": "capture", "role": "noise", "seconds": 4,
             "min_continuous_s": 1.0, "pcm": cap_pcm,
             "path": "/run/kebab-audio-%s/noise.wav" % run_id,
             "pre_writes": mic_route, "timeout_s": 12,
             "cost": {"capture_seconds": 4}},
            {"id": "probe-right-48", "kind": "probe", "label": "right",
             "channel": "right", "ms": 250, "peak_dbfs": -48.0, "freq_hz": 1000.0,
             "fade_ms": 25, "rate": 48000, "capture_seconds": 2.0,
             "requires": ["silence"], "pre_writes": spk_route + mic_route,
             "cost": {"playback_nonzero_ms": 250, "playback_attempt": 1,
                      "capture_seconds": 2},
             "timeout_s": 8, "play_pcm": play_pcm, "capture_pcm": cap_pcm,
             "ref_path": "/run/kebab-audio-%s/ref-r48.wav" % run_id,
             "capture_path": "/run/kebab-audio-%s/cap-r48.wav" % run_id},
            ucm("ucm-verb", "_verb", "HiFi"),
            ucm("ucm-spk-on", "_enadev", "Speaker", {"route_variant": 1}),
            ucm("ucm-spk-off", "_disdev", "Speaker"),
            ucm("ucm-mic-on", "_enadev", "Mic", {"route_variant": 1}),
            ucm("ucm-mic-off", "_disdev", "Mic"),
            {"id": "route-off", "kind": "control", "cost": {"route_variant": 1},
             "timeout_s": 10, "writes": mute},
            {"id": "acoustic", "kind": "verify", "check": "acoustic",
             "read_only": True, "timeout_s": 20},
            {"id": "idle-safety", "kind": "verify", "check": "idle-safety",
             "read_only": True, "timeout_s": 20},
            {"id": "transport", "kind": "verify", "check": "transport",
             "read_only": True, "timeout_s": 20},
            {"id": "server-regression", "kind": "verify",
             "check": "server-regression", "read_only": True, "timeout_s": 30},
            {"id": "pipewire", "kind": "verify", "check": "pipewire",
             "read_only": True, "timeout_s": 20},
        ],
    }
    core.dump_json(os.path.join(srv, "trial-manifest.json"), manifest, 0o600)
    print("actions:", [a["id"] for a in manifest["actions"]])


if __name__ == "__main__":
    main()
