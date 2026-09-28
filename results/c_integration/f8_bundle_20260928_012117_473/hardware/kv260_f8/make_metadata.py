"""Create XRT metadata inputs for the separately loaded F8 platform.

The two CUs are user-managed (AP_CTRL_NONE in XRT metadata). The GEMM's
physical control register still uses ap_ctrl_chain, driven only by our host.
This tool does not include a bitstream and does not access a device.
"""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def generate(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    def save(name, value):
        (output / name).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    save("ip_layout.json", {"ip_layout": {"m_count": "2", "m_ip_data": [
        {"m_type": "IP_KERNEL", "m_int_enable": "0", "m_interrupt_id": "0",
         "m_ip_control": "AP_CTRL_NONE", "m_base_address": base, "m_name": name}
        for base, name in [("0xA0000000", "gemm_top:gemm_1"),
                           ("0xA0010000", "f8_status:status_1")]]}})
    # Both address windows are mapped to PS HP0 in the Vivado design. ZOCL uses
    # its CMA allocator for these unreserved PS RAM entries; bank 0 is a logical
    # allocation index, not a promise that CMA returns a physical low address.
    save("mem_topology.json", {"mem_topology": {"m_count": "2", "m_mem_data": [
        {"m_type": "MEM_DDR4", "m_used": "1", "m_sizeKB": "0x200000",
         "m_tag": tag, "m_base_address": base}
        for tag, base in [("HP0_LOW", "0x0"), ("HP0_HIGH", "0x800000000")]]}})
    save("connectivity.json", {"connectivity": {"m_count": "6", "m_connection": [
        {"arg_index": str(arg), "m_ip_layout_index": "0", "mem_data_index": str(bank)}
        for arg in range(3) for bank in range(2)]}})
    project = ET.Element("project", name="kv260_f8")
    platform = ET.SubElement(project, "platform", vendor="xilinx", boardid="kv260",
                             name="kv260_f8", featureRomTime="0")
    ET.SubElement(platform, "version", major="1", minor="0")
    device = ET.SubElement(platform, "device", name="fpga0",
                           fpgaDevice="xck26-sfvc784-2LV-c", addrWidth="64")
    core = ET.SubElement(device, "core", name="OCL_REGION_0", target="hw",
                         type="clc_region", clockFreq="100MHz", numComputeUnits="2")
    for name, instance, base in [("gemm_top", "gemm_1", "0xA0000000"),
                                 ("f8_status", "status_1", "0xA0010000")]:
        kernel = ET.SubElement(core, "kernel", name=name, language="ip_c",
                               vlnv=f"local:user:{name}:1.0", attributes="",
                               preferredWorkGroupSizeMultiple="0", workGroupSize="1",
                               debug="false", interrupt="false", hwControlProtocol="ap_ctrl_none")
        ET.SubElement(kernel, "port", name="S_AXI_CONTROL", mode="slave", range="0x10000",
                      dataWidth="32", portType="addressable", base="0x0")
        if name == "gemm_top":
            for idx, (arg, offset) in enumerate([("X", "0x10"), ("W", "0x1c"), ("Y", "0x28")]):
                port = f"M_AXI_GMEM{idx}"
                ET.SubElement(kernel, "port", name=port, mode="master", range="0xFFFFFFFFFFFFFFFF",
                              dataWidth="32", portType="addressable", base="0x0")
                ET.SubElement(kernel, "arg", name=arg, addressQualifier="1", id=str(idx),
                              port=port, size="0x8", offset=offset, hostOffset="0x0",
                              hostSize="0x8", type="int*" if idx == 2 else "char*")
        ET.SubElement(kernel, "compileWorkGroupSize", x="1", y="1", z="1")
        ET.SubElement(kernel, "maxWorkGroupSize", x="1", y="1", z="1")
        node = ET.SubElement(kernel, "instance", name=instance)
        ET.SubElement(node, "addrRemap", base=base, range="0x10000", port="S_AXI_CONTROL")
    ET.indent(project)
    ET.ElementTree(project).write(output / "embedded_metadata.xml", encoding="utf-8", xml_declaration=True)
    (output / "xrt.ini").write_text(
        "[Runtime]\nenable_flat=false\nenable_pr=false\nert_polling=true\nrw_shared=false\n",
        encoding="ascii")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    generate(parser.parse_args().output)
