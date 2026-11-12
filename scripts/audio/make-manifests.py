#!/usr/bin/env python3
"""Freeze control-map.json and the phase-2 trial manifest from the LIVE card.

Every control written by this run must appear here with its audited type, count,
range/enum items and current value. Anything not read back from the device is
absent, and therefore unwritable by the executor.

Usage: make-manifests.py <run_id> <srv_dir> <card_index> <capture_pcm> <play_pcm>
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kebab_audio_core as core

AUDIT = [
    "TERT_MI2S_RX Audio Mixer MultiMedia1",
    "MultiMedia2 Mixer VA_CODEC_DMA_TX_0",
    "VA_AIF1_CAP Mixer DEC0",
    "VA DMIC MUX0",
    "VA DEC0 MUX",
    "VA_DEC0",
]

TYPE_MAP = {
    "BOOLEAN": "BOOL",
    "INTEGER": "INT",
    "ENUMERATED": "ENUM",
}


def load_all_controls(card):
    """One read of the whole card: names, types, ranges and enum item names."""
    p = subprocess.run(["amixer", "-c", str(card), "contents"],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        raise SystemExit("amixer contents failed: %s" % p.stderr.strip())
    blocks = {}
    cur = []
    for line in p.stdout.splitlines():
        if line.startswith("numid=") and cur:
            info = core.parse_amixer_cget("\n".join(cur))
            if info.get("name"):
                blocks[info["name"]] = info
            cur = []
        cur.append(line)
    if cur:
        info = core.parse_amixer_cget("\n".join(cur))
        if info.get("name"):
            blocks[info["name"]] = info
    return blocks


def read_control(card, name, all_controls=None):
    if all_controls is not None:
        info = all_controls.get(name)
        if info is None:
            return None
    else:
        p = subprocess.run(["amixer", "-c", str(card), "cget", "name=" + name],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if p.returncode != 0:
            return None
        info = core.parse_amixer_cget(p.stdout)
    out = {
        "type": TYPE_MAP.get(info.get("type") or "", info.get("type")),
        "count": info.get("count"),
        "value": info.get("values") or [],
        "name": name,
        "numid": info.get("numid"),
    }
    if out["type"] == "INT":
        out["min"] = info.get("min")
        out["max"] = info.get("max")
    if out["type"] == "ENUM":
        out["items"] = info.get("items") or []
    return out


def main():
    run_id, srv, card, cap_pcm, play_pcm = sys.argv[1:6]
    card = int(card)
    all_controls = load_all_controls(card)
    controls = {}
    for name in AUDIT:
        entry = read_control(card, name, all_controls)
        if entry is None:
            print("MISSING control: %s" % name, file=sys.stderr)
            continue
        controls[name] = entry
    cmap = {"schema": 1, "run_id": run_id, "card_index": card,
            "collected_wall_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "device_uptime_s": core.read_text("/proc/uptime", "").strip(),
            "note": "audited from the live card with amixer cget; nothing here is "
                    "copied from a community UCM value",
            "controls": controls}
    core.dump_json(os.path.join(srv, "control-map.json"), cmap, 0o600)

    def val(name):
        return controls[name]["value"]

    def items_index(name, want):
        items = controls[name]["items"]
        return items.index(want)

    mute = []
    for name in ("TERT_MI2S_RX Audio Mixer MultiMedia1",
                 "MultiMedia2 Mixer VA_CODEC_DMA_TX_0",
                 "VA_AIF1_CAP Mixer DEC0"):
        if name in controls:
            mute.append({"name": name, "values": [0]})
    if "VA DMIC MUX0" in controls:
        mute.append({"name": "VA DMIC MUX0",
                     "values": [items_index("VA DMIC MUX0", "ZERO")]})

    mic_route = []
    if "MultiMedia2 Mixer VA_CODEC_DMA_TX_0" in controls:
        mic_route.append({"name": "MultiMedia2 Mixer VA_CODEC_DMA_TX_0", "values": [1]})
    if "VA_AIF1_CAP Mixer DEC0" in controls:
        mic_route.append({"name": "VA_AIF1_CAP Mixer DEC0", "values": [1]})
    if "VA DMIC MUX0" in controls:
        mic_route.append({"name": "VA DMIC MUX0",
                          "values": [items_index("VA DMIC MUX0", "DMIC1")]})
    # VA_DEC0 (volume) and VA DEC0 MUX are deliberately NOT written: the mux is
    # already VA_DMIC and the audited range is 0..124 with the current value in
    # range, so the community UCM value of 100 is neither needed nor copied.

    spk_route = [{"name": "TERT_MI2S_RX Audio Mixer MultiMedia1", "values": [1]}]

    probe_cost = {"playback_nonzero_ms": 250, "playback_attempt": 1,
                  "capture_seconds": 2}
    manifest = {
        "schema": 1,
        "run_id": run_id,
        "boot_id": core.read_text("/proc/sys/kernel/random/boot_id", "").strip(),
        "kernel_release": os.uname().release,
        "phase": "A3/A4 route + mic + bounded acoustic probe",
        "card_index": card,
        "capture_pcm": cap_pcm,
        "play_pcm": play_pcm,
        "stop_actions": mute,
        "accept": {"min_snr_db": 10.0, "min_abs_corr": 0.6,
                   "max_clipped_fraction": 0.0, "require_nonzero": True},
        "notes": [
            "acceptance thresholds are frozen here, before any probe runs",
            "pre_writes re-enable exactly one audited route after the mute gate",
            "VA_DEC0 volume is left untouched (community value 100 not adopted)",
        ],
        "actions": [
            {"id": "binding", "kind": "verify", "check": "binding",
             "read_only": True, "timeout_s": 20},
            {"id": "route-check", "kind": "control", "requires": ["silence"],
             "cost": {"route_variant": 1}, "timeout_s": 10,
             "writes": spk_route},
            {"id": "noise-cap", "kind": "capture", "role": "noise",
             "pre_writes": mic_route,
             "cost": {"capture_seconds": 2, "capture_count": 1}, "timeout_s": 8,
             "seconds": 2, "min_continuous_s": 1.0, "pcm": cap_pcm,
             "path": os.path.join("/run/kebab-audio-%s" % run_id, "noise.wav")},
            {"id": "mic-3s", "kind": "capture", "pre_writes": mic_route,
             "cost": {"capture_seconds": 4, "capture_count": 1}, "timeout_s": 9,
             "seconds": 4, "min_continuous_s": 3.0, "pcm": cap_pcm,
             "path": os.path.join("/run/kebab-audio-%s" % run_id, "mic.wav")},
            {"id": "probe-left", "kind": "probe", "label": "left",
             "channel": "left", "ms": 250, "peak_dbfs": -48.0, "freq_hz": 1000.0,
             "fade_ms": 25, "rate": 48000, "capture_seconds": 2.0,
             "requires": ["silence"], "pre_writes": spk_route + mic_route,
             "cost": probe_cost, "timeout_s": 8,
             "play_pcm": play_pcm, "capture_pcm": cap_pcm,
             "ref_path": os.path.join("/run/kebab-audio-%s" % run_id, "ref-l.wav"),
             "capture_path": os.path.join("/run/kebab-audio-%s" % run_id, "cap-l.wav")},
            {"id": "probe-right", "kind": "probe", "label": "right",
             "channel": "right", "ms": 250, "peak_dbfs": -48.0, "freq_hz": 1000.0,
             "fade_ms": 25, "rate": 48000, "capture_seconds": 2.0,
             "requires": ["silence"], "pre_writes": spk_route + mic_route,
             "cost": probe_cost, "timeout_s": 8,
             "play_pcm": play_pcm, "capture_pcm": cap_pcm,
             "ref_path": os.path.join("/run/kebab-audio-%s" % run_id, "ref-r.wav"),
             "capture_path": os.path.join("/run/kebab-audio-%s" % run_id, "cap-r.wav")},
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
    print("control-map controls:", sorted(controls))
    print("mute actions:", len(mute), "mic_route:", len(mic_route))
    print("manifest actions:", [a["id"] for a in manifest["actions"]])


if __name__ == "__main__":
    main()
