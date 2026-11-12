#!/usr/bin/env python3
"""Freeze the control map and manifest for the left-channel + UCM run.

Reuses the audited builders from make-manifests.py by loading it from its path,
so the two runs cannot drift apart in how a control is described.
"""
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "make_manifests", os.path.join(HERE, "make-manifests.py"))
mm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mm)

import kebab_audio_core as core  # noqa: E402


def main():
    run_id, srv, card, cap_pcm, play_pcm, ucm_root = sys.argv[1:7]
    card = int(card)
    seed = sys.argv[7] if len(sys.argv) > 7 else ""
    controls = {}
    provenance = "live"
    card_text = open(os.path.join("/proc/asound/cards")).read() if os.path.exists(
        "/proc/asound/cards") else ""
    card_bound = "no soundcards" not in card_text
    if card_bound:
        try:
            all_controls = mm.load_all_controls(card)
            for name in mm.AUDIT:
                entry = mm.read_control(card, name, all_controls)
                if entry is None:
                    print("MISSING control: %s" % name, file=sys.stderr)
                    continue
                controls[name] = entry
        except (Exception, SystemExit) as exc:  # noqa: BLE001
            print("live control read failed: %s" % exc, file=sys.stderr)
            controls = {}
    if not controls and seed:
        # The card is not bound yet: the guardian has to be armed before the codec
        # is loaded, so the map is seeded from the previously audited run and then
        # checked against the live card the moment it appears.
        with open(seed) as fh:
            controls = json.load(fh)["controls"]
        provenance = "seed:%s" % os.path.basename(seed)
        print("card not bound; seeded %d controls from %s" % (len(controls), seed),
              file=sys.stderr)
    if not controls:
        raise SystemExit("no controls: card absent and no seed given")
    cmap = {"schema": 1, "run_id": run_id, "card_index": card,
            "provenance": provenance,
            "collected_wall_utc": __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ",
                                                             __import__("time").gmtime()),
            "device_uptime_s": core.read_text("/proc/uptime", "").strip(),
            "note": "audited from the live card with amixer contents",
            "controls": controls}
    core.dump_json(os.path.join(srv, "control-map.json"), cmap, 0o600)

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

    def probe(pid, peak, ref, cap):
        return {
            "id": pid, "kind": "probe", "label": "left",
            "channel": "left", "ms": 250, "peak_dbfs": peak, "freq_hz": 1000.0,
            "fade_ms": 25, "rate": 48000, "capture_seconds": 2.0,
            "requires": ["silence"], "pre_writes": spk_route + mic_route,
            "cost": {"playback_nonzero_ms": 250, "playback_attempt": 1,
                     "capture_seconds": 2},
            "timeout_s": 8, "play_pcm": play_pcm, "capture_pcm": cap_pcm,
            "ref_path": os.path.join("/run/kebab-audio-%s" % run_id, ref),
            "capture_path": os.path.join("/run/kebab-audio-%s" % run_id, cap),
        }

    def ucm(aid, op, arg, cost=1):
        return {"id": aid, "kind": "ucm", "op": op, "arg": arg, "card": "O8T",
                "env": {"ALSA_CONFIG_UCM2": ucm_root}, "timeout_s": 15,
                "cost": {"route_variant": cost}}

    manifest = {
        "schema": 1, "run_id": run_id, "phase": "left channel + UCM closure",
        "boot_id": core.read_text("/proc/sys/kernel/random/boot_id", "").strip(),
        "kernel_release": os.uname().release,
        "card_index": card, "capture_pcm": cap_pcm, "play_pcm": play_pcm,
        "stop_actions": mute,
        "accept": {"min_snr_db": 10.0, "min_abs_corr": 0.6,
                   "max_clipped_fraction": 0.0, "require_nonzero": True},
        "notes": [
            "thresholds are the same frozen ones as run 20261112t1115z; nothing was "
            "retuned after the first run's partial result",
            "the left channel gets two attempts on one hypothesis: -42 then -36 dBFS",
            "UCM is staged privately via ALSA_CONFIG_UCM2; no system file is touched",
        ],
        "actions": [
            {"id": "load-tfa2", "kind": "modprobe", "module": "snd_soc_tfa2",
             "cost": {"module_load": 1}, "timeout_s": 20},
            {"id": "binding", "kind": "verify", "check": "binding",
             "read_only": True, "timeout_s": 20},
            probe("probe-left-42", -42.0, "ref-l42.wav", "cap-l42.wav"),
            probe("probe-left-36", -36.0, "ref-l36.wav", "cap-l36.wav"),
            ucm("ucm-verb", "_verb", "HiFi", cost=0),
            ucm("ucm-spk-on", "_enadev", "Speaker"),
            ucm("ucm-spk-off", "_disdev", "Speaker"),
            ucm("ucm-mic-on", "_enadev", "Mic"),
            ucm("ucm-mic-off", "_disdev", "Mic"),
            {"id": "route-off", "kind": "control", "cost": {"route_variant": 1},
             "timeout_s": 10, "writes": mute},
            {"id": "transport", "kind": "verify", "check": "transport",
             "read_only": True, "timeout_s": 20},
            {"id": "acoustic", "kind": "verify", "check": "acoustic",
             "read_only": True, "timeout_s": 20},
            {"id": "idle-safety", "kind": "verify", "check": "idle-safety",
             "read_only": True, "timeout_s": 20},
            {"id": "server-regression", "kind": "verify",
             "check": "server-regression", "read_only": True, "timeout_s": 30},
            {"id": "pipewire", "kind": "verify", "check": "pipewire",
             "read_only": True, "timeout_s": 20},
        ],
    }
    core.dump_json(os.path.join(srv, "trial-manifest.json"), manifest, 0o600)
    print("controls:", sorted(controls))
    print("actions:", [a["id"] for a in manifest["actions"]])


if __name__ == "__main__":
    main()
