#!/usr/bin/env python3
"""Manifest for the UCM-only run.

Every UCM transition charges route_variant 1 -- including the disables: they write
routing controls, and calling them free would be an accounting game. That keeps
the executor invariant "an action that can route must declare a cost" intact.
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("mm2", os.path.join(HERE, "make-manifests2.py"))
mm2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mm2)
core = mm2.core


def main():
    run_id, srv, card, cap_pcm, play_pcm, ucm_root, seed = sys.argv[1:8]
    card = int(card)
    with open(seed) as fh:
        blob = json.load(fh)
        controls = blob.get("controls") or blob.get("live") or {}
    core.dump_json(os.path.join(srv, "control-map.json"),
                   {"schema": 1, "run_id": run_id, "card_index": card,
                    "provenance": "live:%s" % os.path.basename(seed),
                    "collected_wall_utc": __import__("time").strftime(
                        "%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime()),
                    "controls": controls}, 0o600)

    mute = [{"name": n, "values": [0]} for n in (
        "TERT_MI2S_RX Audio Mixer MultiMedia1",
        "MultiMedia2 Mixer VA_CODEC_DMA_TX_0",
        "VA_AIF1_CAP Mixer DEC0") if n in controls]
    if "VA DMIC MUX0" in controls:
        mute.append({"name": "VA DMIC MUX0", "values": [0]})

    def ucm(aid, op, arg):
        # alsaucm keeps no verb between processes, so a bare "_disdev" is a
        # no-op; the disable half has to enable first inside the same batch.
        if op == "_disdev":
            seq = ["_verb HiFi", "_enadev %s" % arg, "_disdev %s" % arg]
        else:
            seq = ["_verb HiFi", "%s %s" % (op, arg)]
        return {"id": aid, "kind": "ucm", "op": op, "arg": arg, "card": "O8T",
                "verb": "HiFi", "seq": seq,
                "timeout_s": 20, "cost": {"route_variant": 1}}

    manifest = {
        "schema": 1, "run_id": run_id, "phase": "UCM closure only",
        "boot_id": core.read_text("/proc/sys/kernel/random/boot_id", "").strip(),
        "kernel_release": os.uname().release,
        "card_index": card, "capture_pcm": cap_pcm, "play_pcm": play_pcm,
        "stop_actions": mute,
        "accept": {"min_snr_db": 10.0, "min_abs_corr": 0.6,
                   "max_clipped_fraction": 0.0, "require_nonzero": True},
        "notes": [
            "no playback and no capture: this run only exercises the staged UCM",
            "each UCM transition charges route_variant 1, disables included",
        ],
        "actions": [
            {"id": "load-tfa2", "kind": "modprobe", "module": "snd_soc_tfa2",
             "cost": {"module_load": 1}, "timeout_s": 20},
            {"id": "binding", "kind": "verify", "check": "binding",
             "read_only": True, "timeout_s": 20},
            ucm("ucm-spk-on", "_enadev", "Speaker"),
            ucm("ucm-spk-off", "_disdev", "Speaker"),
            ucm("ucm-mic-on", "_enadev", "Mic"),
            ucm("ucm-mic-off", "_disdev", "Mic"),
            {"id": "ucm-pair", "kind": "verify", "check": "ucm-pair",
             "card": card, "verb": "HiFi", "device": "Speaker",
             "readback_control": "TERT_MI2S_RX Audio Mixer MultiMedia1",
             "timeout_s": 45, "cost": {"route_variant": 2}},
            {"id": "route-off", "kind": "control", "cost": {"route_variant": 1},
             "timeout_s": 10, "writes": mute},
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
