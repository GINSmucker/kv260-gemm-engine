"""Pure-software tests; no Vivado executable or device is used."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import prepare_system as prep
import verify_system as verify


def component(values=("0", "0", "0")):
    ET.register_namespace("spirit", prep.NS["s"])
    tag = lambda name: "{" + prep.NS["s"] + "}" + name
    root = ET.Element(tag("component"))
    buses = ET.SubElement(root, tag("busInterfaces"))
    for index, value in enumerate(values):
        bus = ET.SubElement(buses, tag("busInterface"))
        ET.SubElement(bus, tag("name")).text = f"m_axi_gmem{index}"
        params = ET.SubElement(bus, tag("parameters"))
        for name, content in (("HAS_BURST", value), ("ADDR_WIDTH", "64")):
            p = ET.SubElement(params, tag("parameter"))
            ET.SubElement(p, tag("name")).text = name
            ET.SubElement(p, tag("value"), {tag("id"): f"BUSIFPARAM_VALUE.M_AXI_GMEM{index}.{name}"}).text = content
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


class SystemTests(unittest.TestCase):
    def test_burst_correction_only(self):
        raw = component()
        result, before = prep.patch_burst_xml(raw)
        self.assertEqual(set(before.values()), {"0"})
        self.assertEqual(result, raw.replace(b'HAS_BURST">0<', b'HAS_BURST">1<'))

    def test_already_correct_burst(self):
        raw = component(("1", "1", "1"))
        self.assertEqual(prep.patch_burst_xml(raw)[0], raw)

    def test_unknown_burst_refused(self):
        with self.assertRaises(ValueError):
            prep.patch_burst_xml(component(("2", "0", "0")))

    def test_missing_master_refused(self):
        raw = component()
        root = ET.fromstring(raw)
        buses = root.find("s:busInterfaces", prep.NS)
        buses.remove(list(buses)[0])
        with self.assertRaises(ValueError):
            prep.patch_burst_xml(ET.tostring(root))

    def test_safe_relative_path(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as folder:
            path = Path(folder)
            (path / "ok").write_bytes(b"ok")
            self.assertEqual(prep.relative_file(path, "ok"), path / "ok")
            for value in ("../ok", "C:/ok", "/ok", "a\\ok", "missing"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    prep.relative_file(path, value)

    def test_new_register_count(self):
        abi = {"registers": {}}
        for name, offset, bits in (("X", 0x18, 64), ("W_data", 0x24, 64), ("W_meta", 0x30, 64),
                                   ("Y", 0x3c, 64), ("M", 0x48, 32), ("K", 0x50, 32),
                                   ("N", 0x58, 32), ("mode", 0x60, 32)):
            abi["registers"][name] = dict(offset=hex(offset), bits=bits)
        regs = verify.expected_registers(abi)
        self.assertEqual(len(regs), 17)
        self.assertEqual((regs["W_meta_LOW"], regs["Y_LOW"], regs["mode"]), (0x30, 0x3c, 0x60))

    def test_missing_actual_build_refused(self):
        (ROOT / "build").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as folder:
            with self.assertRaises(OSError):
                verify.verify(ROOT, Path(folder))


if __name__ == "__main__":
    unittest.main()
