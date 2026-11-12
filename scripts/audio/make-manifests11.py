#!/usr/bin/env python3
"""Manifest: load only, so the script can inspect WirePlumber's view."""
import importlib.util, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("mm2", os.path.join(HERE, "make-manifests2.py"))
mm2 = importlib.util.module_from_spec(spec); spec.loader.exec_module(mm2)
core = mm2.core


def main():
    run_id, srv, card, cap_pcm, play_pcm, seed = sys.argv[1:7]
    card = int(card)
    blob = json.load(open(seed))
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
    manifest = {
        "schema": 1, "run_id": run_id, "phase": "wireplumber inspection",
        "boot_id": core.read_text("/proc/sys/kernel/random/boot_id", "").strip(),
        "kernel_release": os.uname().release,
        "card_index": card, "capture_pcm": cap_pcm, "play_pcm": play_pcm,
        "stop_actions": mute,
        "accept": {"min_snr_db": 10.0, "min_abs_corr": 0.6,
                   "max_clipped_fraction": 0.0, "require_nonzero": True},
        "notes": ["no playback and no capture: read WirePlumber's view only"],
        "actions": [
            {"id": "load-tfa2", "kind": "modprobe", "module": "snd_soc_tfa2",
             "cost": {"module_load": 1}, "timeout_s": 20},
            {"id": "binding", "kind": "verify", "check": "binding",
             "read_only": True, "timeout_s": 20},
        ],
    }
    core.dump_json(os.path.join(srv, "trial-manifest.json"), manifest, 0o600)
    print("actions:", [a["id"] for a in manifest["actions"]])


if __name__ == "__main__":
    main()
