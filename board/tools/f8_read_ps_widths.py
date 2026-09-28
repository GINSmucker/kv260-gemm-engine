"""Read PS-PL fabric widths without writing any hardware register."""
import json
import mmap
import os
import struct


def read_widths():
    values = {}
    with open("/dev/mem", "rb", buffering=0) as dev:
        for name, address in (("FPD_SLCR_AFI_FS", 0xfd615000),
                              ("HP0_RDCTRL", 0xfd380000),
                              ("HP0_WRCTRL", 0xfd380014)):
            page = address & ~4095
            with mmap.mmap(dev.fileno(), 4096, flags=mmap.MAP_SHARED,
                           prot=mmap.PROT_READ, offset=page) as mm:
                values[name] = struct.unpack_from("<I", mm, address-page)[0]
    # UG1087 afi_fs (FPD_SLCR): dw_ss0_sel [9:8], 0/1/2 = 32/64/128.
    # AFIFM2 RDCTRL/WRCTRL FABRIC_WIDTH [1:0], 0/1/2 = 128/64/32.
    return {"raw": {k: hex(v) for k, v in values.items()},
            "hpm0_bits": {0:32, 1:64, 2:128}.get((values["FPD_SLCR_AFI_FS"] >> 8) & 3),
            "hp0_read_bits": {0:128, 1:64, 2:32}.get(values["HP0_RDCTRL"] & 3),
            "hp0_write_bits": {0:128, 1:64, 2:32}.get(values["HP0_WRCTRL"] & 3)}


if __name__ == "__main__":
    assert os.geteuid() == 0
    print(json.dumps(read_widths(), indent=2))
