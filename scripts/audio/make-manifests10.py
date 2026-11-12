#!/usr/bin/env python3
"""Manifest: same tone through ALSA directly and through PipeWire."""
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
    mic = [{"name": n, "values": [v]} for n, v in (
        ("MultiMedia2 Mixer VA_CODEC_DMA_TX_0", 1),
        ("VA_AIF1_CAP Mixer DEC0", 1), ("VA DMIC MUX0", 2)) if n in controls]
    spk = [{"name": "TERT_MI2S_RX Audio Mixer MultiMedia1", "values": [1]}]

    def probe(aid, label, extra):
        act = {"id": aid, "kind": "probe", "label": label, "channel": "left",
               "ms": 250, "peak_dbfs": -36.0, "freq_hz": 1000.0,
               "fade_ms": 25, "rate": 48000, "capture_seconds": 2.0,
               "requires": ["silence"], "pre_writes": spk + mic,
               "cost": {"playback_nonzero_ms": 250, "playback_attempt": 1,
                        "capture_seconds": 2, "route_variant": 1},
               "timeout_s": 10, "play_pcm": play_pcm, "capture_pcm": cap_pcm,
               "ref_path": "/run/kebab-audio-%s/ref-%s.wav" % (run_id, aid),
               "capture_path": "/run/kebab-audio-%s/cap-%s.wav" % (run_id, aid)}
        act.update(extra)
        return act

    manifest = {
        "schema": 1, "run_id": run_id,
        "phase": "same tone via ALSA and via PipeWire",
        "boot_id": core.read_text("/proc/sys/kernel/random/boot_id", "").strip(),
        "kernel_release": os.uname().release,
        "card_index": card, "capture_pcm": cap_pcm, "play_pcm": play_pcm,
        "stop_actions": mute,
        "accept": {"min_snr_db": 10.0, "min_abs_corr": 0.6,
                   "max_clipped_fraction": 0.0, "require_nonzero": True},
        "notes": [
            "both probes play the same generated 1 kHz burst at -36 dBFS and are "
            "captured the same way, so a difference is the transport, not the signal",
            "the PipeWire probe is still charged and journalled by this executor",
        ],
        "actions": [
            {"id": "load-tfa2", "kind": "modprobe", "module": "snd_soc_tfa2",
             "cost": {"module_load": 1}, "timeout_s": 20},
            {"id": "binding", "kind": "verify", "check": "binding",
             "read_only": True, "timeout_s": 20},
            probe("probe-alsa", "alsa", {}),
            probe("probe-pipewire", "pipewire", {
                "player": ["pw-play"],
                "player_env": {"XDG_RUNTIME_DIR": "/run/user/1000"},
            }),
        ],
    }
    core.dump_json(os.path.join(srv, "trial-manifest.json"), manifest, 0o600)
    print("actions:", [a["id"] for a in manifest["actions"]])


if __name__ == "__main__":
    main()
