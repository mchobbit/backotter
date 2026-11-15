#!/usr/bin/env python3
"""luksfacts.py DEV OUT.json: what inner.sh checks of a LUKS2 header,
read from the device itself (no key, nothing printed that unlocks it):
the binary header's magic, version, size, label, UUID and sequence id;
the JSON metadata's keyslots (type, priority, key size, KDF type and
memory, area offset and size: never salts, digests or key material),
tokens (whole: they hold no key here), segments and the config's sizes;
and the SHA-256 of the whole header area (2 x the metadata size plus the
keyslots area, what luksHeaderBackup writes).  Exits 1 on no LUKS2."""
import hashlib, json, struct, sys

dev, out = sys.argv[1], sys.argv[2]
with open(dev, "rb") as f:
    h = f.read(4096)
    if h[:6] != b"LUKS\xba\xbe":
        sys.exit(f"luksfacts.py: no LUKS header on {dev}")
    version, hdr_size, seqid = struct.unpack_from(">HQQ", h, 6)
    label = h[24:72].rstrip(b"\0").decode("ascii", "replace")
    uuid = h[168:208].rstrip(b"\0").decode("ascii", "replace")
    f.seek(4096)
    meta = json.loads(f.read(hdr_size - 4096).rstrip(b"\0").decode())
    cfg = meta.get("config", {})
    area = 2 * hdr_size + int(cfg.get("keyslots_size", 0))
    f.seek(0)
    sha = hashlib.sha256(f.read(area)).hexdigest()
slots = {}
for n, k in sorted(meta.get("keyslots", {}).items()):
    kdf = k.get("kdf", {})
    slots[n] = {"type": k.get("type"), "priority": k.get("priority", 1), "key_size": k.get("key_size"),
                "kdf": kdf.get("type"), "memory": kdf.get("memory"), "cpus": kdf.get("cpus"),
                "area": [int(k.get("area", {}).get("offset", 0)), int(k.get("area", {}).get("size", 0))]}
segs = {n: {"type": s.get("type"), "offset": s.get("offset"), "size": s.get("size"), "encryption": s.get("encryption"),
            "sector_size": s.get("sector_size")} for n, s in sorted(meta.get("segments", {}).items())}
json.dump({"version": version, "hdr_size": hdr_size, "seqid": seqid, "label": label, "uuid": uuid,
           "keyslots": slots, "tokens": meta.get("tokens", {}), "segments": segs,
           "config": {"json_size": cfg.get("json_size"), "keyslots_size": cfg.get("keyslots_size")},
           "digests": len(meta.get("digests", {})), "header_area_bytes": area, "header_area_sha256": sha},
          open(out, "w"), indent=1, sort_keys=True)
