"""Synthetic HWH graph semantics; no source-text matching or hardware access."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("_board_logic_test_module", ROOT / "scripts/board_logic.py")
logic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(logic)


def fixture(policy="peripheral_shared", aux_high=1, aux_value=0):
    root = ET.Element("EDKSYSTEM", SYNTHETIC_TEST_ONLY="true")
    modules = ET.SubElement(root, "MODULES")
    types = {"ps": "zynq_ultra_ps_e:3.5", "reset": "proc_sys_reset:5.0", "ctrl": "smartconnect:1.0",
             "data": "smartconnect:1.0", "one": "xlconstant:1.1", "zero": "xlconstant:1.1"}
    for name, target in types.items():
        m = ET.SubElement(modules, "MODULE", INSTANCE=name, VLNV="xilinx.com:ip:" + target)
        ET.SubElement(m, "PARAMETERS"); ET.SubElement(m, "PORTS")
    for name, target in (("gemm", "xilinx.com:hls:model_gemm_top:1.0"),
                         ("status", "xilinx.com:module_ref:f8_status:1.0")):
        m = ET.SubElement(modules, "MODULE", INSTANCE=name, VLNV=target)
        ET.SubElement(m, "PARAMETERS"); ET.SubElement(m, "PORTS")
    modules_by_name = {m.get("INSTANCE"): m for m in modules}
    def param(module, name, value):
        ET.SubElement(modules_by_name[module].find("PARAMETERS"), "PARAMETER", NAME=name, VALUE=str(value))
    for name, value in (("C_EXT_RESET_HIGH", 0), ("C_AUX_RESET_HIGH", aux_high)):
        param("reset", name, value)
    param("ps", "PSU__FPGA_PL0_ENABLE", 1)
    param("ps", "PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ", 125)
    for module, value in (("zero", 0), ("one", 1)):
        param(module, "CONST_WIDTH", 1); param(module, "CONST_VAL", value)

    def connect(source, sinks, signal, sigis="rst", polarities=None):
        for key, direction, peers in [(source, "O", sinks)] + [(key, "I", [source]) for key in sinks]:
            attrs = dict(NAME=key[1], DIR=direction, SIGNAME=signal, SIGIS=sigis)
            if sigis == "clk":
                attrs["CLKFREQUENCY"] = "124998749"
            if polarities and key in polarities:
                attrs["POLARITY"] = polarities[key]
            port = ET.SubElement(modules_by_name[key[0]].find("PORTS"), "PORT", **attrs)
            connections = ET.SubElement(port, "CONNECTIONS")
            for peer in peers:
                ET.SubElement(connections, "CONNECTION", INSTANCE=peer[0], PORT=peer[1])

    reset_output = ("reset", "peripheral_aresetn")
    if policy == "peripheral_shared":
        group = [reset_output, *logic.RESET_SINKS]
        connect(reset_output, list(logic.RESET_SINKS), "peripheral_reset_n",
                polarities={key: "ACTIVE_LOW" for key in group})
    else:
        for output, sinks in ((("reset", "interconnect_aresetn"), logic.RESET_SINKS[:2]),
                              (reset_output, logic.RESET_SINKS[2:])):
            connect(output, list(sinks), output[1], polarities={key: "ACTIVE_LOW" for key in (output, *sinks)})
    connect(("ps", "pl_resetn0"), [("reset", "ext_reset_in")], "ps_reset_n",
            polarities={("ps", "pl_resetn0"): "ACTIVE_LOW", ("reset", "ext_reset_in"): "ACTIVE_LOW"})
    connect(("ps", "pl_clk0"), list(logic.CLOCK_SINKS), "pl_clock", sigis="clk")
    aux_source = "one" if aux_value else "zero"
    constants = {"zero": [], "one": []}
    constants[aux_source].append(("reset", "aux_reset_in"))
    constants["zero"].append(("reset", "mb_debug_sys_rst"))
    constants["one"].append(("reset", "dcm_locked"))
    for source, sinks in constants.items():
        connect((source, "dout"), sinks, source + "_constant", sigis="undef")
    port(root, "reset", "aux_reset_in").set("SIGIS", "rst")
    port(root, "reset", "aux_reset_in").set("POLARITY", "ACTIVE_HIGH" if aux_high else "ACTIVE_LOW")
    port(root, "reset", "mb_debug_sys_rst").set("SIGIS", "rst")
    port(root, "reset", "mb_debug_sys_rst").set("POLARITY", "ACTIVE_HIGH")
    return root


def module(root, name):
    return root.find("./MODULES/MODULE[@INSTANCE='" + name + "']")


def port(root, instance, name):
    return module(root, instance).find("./PORTS/PORT[@NAME='" + name + "']")


def param(root, instance, name):
    return module(root, instance).find("./PARAMETERS/PARAMETER[@NAME='" + name + "']")


def raw(root):
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def add_logic_fixture(root: ET.Element) -> None:
    """Merge SYNTHETIC reset/clock evidence without replacing ABI content.

    Shared by pipeline tests only. Existing registers, parameters, addresses
    and SmartConnect scoped-BD attributes stay intact. Repeating this helper
    is allowed only when the previously inserted evidence is identical.
    """
    root.tag = "EDKSYSTEM"
    target_modules = root.find("MODULES")
    if target_modules is None:
        target_modules = ET.SubElement(root, "MODULES")
    for incoming in fixture().findall("./MODULES/MODULE"):
        name = incoming.get("INSTANCE")
        matches = target_modules.findall("./MODULE[@INSTANCE='" + name + "']")
        if len(matches) > 1:
            raise ValueError("duplicate synthetic target module: " + name)
        if not matches:
            target_modules.append(copy.deepcopy(incoming))
            continue
        target = matches[0]
        if target.get("VLNV") not in (None, incoming.get("VLNV")):
            raise ValueError("conflicting synthetic module type: " + name)
        target.set("VLNV", incoming.get("VLNV"))
        for section, item_tag, key in (("PARAMETERS", "PARAMETER", "NAME"), ("PORTS", "PORT", "NAME")):
            destination = target.find(section)
            if destination is None:
                destination = ET.SubElement(target, section)
            for item in incoming.findall("./" + section + "/" + item_tag):
                previous = destination.findall("./" + item_tag + "[@" + key + "='" + item.get(key) + "']")
                if previous:
                    if len(previous) != 1 or ET.tostring(previous[0]) != ET.tostring(item):
                        raise ValueError("conflicting synthetic logic evidence: " + name + "/" + item.get(key))
                else:
                    destination.append(copy.deepcopy(item))


class BoardLogicTests(unittest.TestCase):
    def test_fixed_aux_high_tie_zero_shared_sync_passes(self):
        result = logic.check_board_logic(raw(fixture()))
        self.assertEqual(result["status"], "PASS_HWH_LOGIC_NOT_BOARD_TEST")
        self.assertEqual(result["reset_policy"], "peripheral_shared")
        self.assertEqual(result["reset_inputs"]["auxiliary"]["value"], 0)
        self.assertTrue(result["reset_inputs"]["auxiliary"]["active_high"])
        self.assertEqual(len(result["reset_domains"][0]["consumers"]), 4)
        self.assertEqual(result["clock"]["static_hwh_declared_hz"], 124998749)
        self.assertFalse(result["clock"]["actual_board_clock_measured"])
        self.assertEqual(result["board"], "NOT_RUN")

    def test_original_aux_low_tie_zero_is_explicitly_rejected(self):
        with self.assertRaisesRegex(ValueError, "aux_reset_in: constant level 0 is active"):
            logic.check_board_logic(raw(fixture(aux_high=0, aux_value=0)))

    def test_low_active_aux_tied_high_is_semantically_inactive(self):
        result = logic.check_board_logic(raw(fixture(aux_high=0, aux_value=1)))
        self.assertFalse(result["reset_inputs"]["auxiliary"]["active_high"])

    def test_optional_split_policy_passes_only_split_consumers(self):
        result = logic.check_board_logic(raw(fixture(policy="split")), reset_policy="split")
        self.assertEqual(len(result["reset_domains"]), 2)
        self.assertEqual(result["reset_domains"][0]["consumers"], ["ctrl/aresetn", "data/aresetn"])
        with self.assertRaisesRegex(ValueError, "consumers|missing HWH port"):
            logic.check_board_logic(raw(fixture()), reset_policy="split")
        with self.assertRaisesRegex(ValueError, "consumers"):
            logic.check_board_logic(raw(fixture(policy="split")))

    def test_aux_high_tied_high_rejected(self):
        with self.assertRaisesRegex(ValueError, "aux_reset_in"):
            logic.check_board_logic(raw(fixture(aux_high=1, aux_value=1)))

    def test_pipeline_helper_preserves_existing_abi_and_scoped_bd(self):
        tree = ET.Element("SYSTEM")
        modules = ET.SubElement(tree, "MODULES")
        gemm = ET.SubElement(modules, "MODULE", INSTANCE="gemm", VLNV="xilinx.com:hls:model_gemm_top:1.0")
        registers = ET.SubElement(gemm, "REGISTERS")
        ET.SubElement(registers, "REGISTER", NAME="mode", ADDRESS_OFFSET="0x64")
        parameters = ET.SubElement(gemm, "PARAMETERS")
        ET.SubElement(parameters, "PARAMETER", NAME="C_M_AXI_GMEM0_DATA_WIDTH", VALUE="128")
        ctrl = ET.SubElement(modules, "MODULE", INSTANCE="ctrl", VLNV="xilinx.com:ip:smartconnect:1.0", BDTYPE="SBD", BD="kv260_opt50_ctrl_0")
        ET.SubElement(modules, "MODULE", INSTANCE="status", MODTYPE="f8_status")
        ET.SubElement(modules, "MODULE", INSTANCE="ps")
        add_logic_fixture(tree)
        self.assertEqual(tree.tag, "EDKSYSTEM")
        self.assertIs(module(tree, "gemm").find("REGISTERS"), registers)
        self.assertEqual(param(tree, "gemm", "C_M_AXI_GMEM0_DATA_WIDTH").get("VALUE"), "128")
        self.assertEqual(ctrl.get("BDTYPE"), "SBD")
        self.assertEqual(ctrl.get("BD"), "kv260_opt50_ctrl_0")
        self.assertEqual(logic.check_board_logic(raw(tree))["board"], "NOT_RUN")
        before = raw(tree)
        add_logic_fixture(tree)
        self.assertEqual(raw(tree), before)

    def test_pipeline_helper_does_not_overwrite_conflicting_parameters(self):
        tree = fixture()
        param(tree, "reset", "C_AUX_RESET_HIGH").set("VALUE", "0")
        with self.assertRaisesRegex(ValueError, "conflicting synthetic logic evidence"):
            add_logic_fixture(tree)

    def test_aux_parameter_and_port_polarity_disagreement_rejected(self):
        tree = fixture(); port(tree, "reset", "aux_reset_in").set("POLARITY", "ACTIVE_LOW")
        with self.assertRaisesRegex(ValueError, "polarity declaration"):
            logic.check_board_logic(raw(tree))

    def test_external_active_high_parameter_rejected(self):
        tree = fixture(); param(tree, "reset", "C_EXT_RESET_HIGH").set("VALUE", "1")
        with self.assertRaisesRegex(ValueError, "external reset"):
            logic.check_board_logic(raw(tree))

    def test_debug_reset_active_low_declaration_rejected(self):
        tree = fixture(); port(tree, "reset", "mb_debug_sys_rst").set("POLARITY", "ACTIVE_LOW")
        with self.assertRaisesRegex(ValueError, "polarity declaration"):
            logic.check_board_logic(raw(tree))

    def test_debug_constant_high_rejected(self):
        tree = fixture()
        # Use a valid inactive low-active aux so changing the shared zero
        # constant reaches debug's semantic check rather than aux first.
        param(tree, "reset", "C_AUX_RESET_HIGH").set("VALUE", "0")
        port(tree, "reset", "aux_reset_in").set("POLARITY", "ACTIVE_LOW")
        param(tree, "zero", "CONST_VAL").set("VALUE", "1")
        with self.assertRaisesRegex(ValueError, "mb_debug_sys_rst: constant level 1"):
            logic.check_board_logic(raw(tree))

    def test_dcm_locked_tied_low_rejected(self):
        tree = fixture(); param(tree, "one", "CONST_VAL").set("VALUE", "0")
        with self.assertRaisesRegex(ValueError, "dcm_locked: constant level 0"):
            logic.check_board_logic(raw(tree))

    def test_nonboolean_constant_width_rejected(self):
        tree = fixture(); param(tree, "zero", "CONST_WIDTH").set("VALUE", "2")
        with self.assertRaisesRegex(ValueError, "nonboolean constant"):
            logic.check_board_logic(raw(tree))

    def test_reset_sink_missing_connection_rejected(self):
        tree = fixture(); port(tree, "gemm", "ap_rst_n").find("CONNECTIONS").clear()
        with self.assertRaisesRegex(ValueError, "unique source"):
            logic.check_board_logic(raw(tree))

    def test_reset_output_missing_reverse_connection_rejected(self):
        tree = fixture(); connections = port(tree, "reset", "peripheral_aresetn").find("CONNECTIONS")
        connections.remove(connections[0])
        with self.assertRaisesRegex(ValueError, "CONNECTION list"):
            logic.check_board_logic(raw(tree))

    def test_reset_sink_on_different_signal_rejected(self):
        tree = fixture(); port(tree, "data", "aresetn").set("SIGNAME", "other_reset")
        with self.assertRaisesRegex(ValueError, "consumers"):
            logic.check_board_logic(raw(tree))

    def test_multiple_reset_drivers_rejected(self):
        tree = fixture()
        target = port(tree, "reset", "peripheral_aresetn").get("SIGNAME")
        ET.SubElement(module(tree, "one").find("PORTS"), "PORT", NAME="bogus_output", DIR="O", SIGNAME=target)
        with self.assertRaisesRegex(ValueError, "multiple or wrong drivers"):
            logic.check_board_logic(raw(tree))

    def test_active_high_reset_consumer_rejected(self):
        tree = fixture(); port(tree, "status", "aresetn").set("POLARITY", "ACTIVE_HIGH")
        with self.assertRaisesRegex(ValueError, "polarity declaration"):
            logic.check_board_logic(raw(tree))

    def test_clock_sink_mixed_source_rejected(self):
        tree = fixture(); port(tree, "status", "aclk").set("SIGNAME", "unrelated_clock")
        with self.assertRaisesRegex(ValueError, "consumers"):
            logic.check_board_logic(raw(tree))

    def test_clock_sink_bad_frequency_rejected(self):
        tree = fixture(); port(tree, "data", "aclk").set("CLKFREQUENCY", "100000000")
        with self.assertRaisesRegex(ValueError, "inconsistent static"):
            logic.check_board_logic(raw(tree))

    def test_all_static_clock_100mhz_rejected(self):
        tree = fixture()
        for p in tree.findall(".//PORT[@SIGIS='clk']"):
            p.set("CLKFREQUENCY", "100000000")
        with self.assertRaisesRegex(ValueError, "unreviewed static"):
            logic.check_board_logic(raw(tree))

    def test_actual_clock_measurement_not_inferred_from_static_hwh(self):
        for hz in logic.STATIC_CLOCK_RATES:
            tree = fixture()
            for p in tree.findall(".//PORT[@SIGIS='clk']"):
                p.set("CLKFREQUENCY", str(hz))
            self.assertFalse(logic.check_board_logic(raw(tree))["clock"]["actual_board_clock_measured"])

    def test_requested_clock_wrong_or_disabled_rejected(self):
        for name, value in (("PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ", "100"), ("PSU__FPGA_PL0_ENABLE", "0")):
            tree = fixture(); param(tree, "ps", name).set("VALUE", value)
            with self.subTest(name=name), self.assertRaises(ValueError):
                logic.check_board_logic(raw(tree))

    def test_unknown_policy_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown reset policy"):
            logic.check_board_logic(raw(fixture()), reset_policy="assume_safe")

    def test_duplicate_module_rejected(self):
        tree = fixture(); tree.find("MODULES").append(module(fixture(), "ps"))
        with self.assertRaisesRegex(ValueError, "duplicate HWH module"):
            logic.check_board_logic(raw(tree))

    def test_duplicate_port_rejected(self):
        tree = fixture(); module(tree, "reset").find("PORTS").append(port(fixture(), "reset", "aux_reset_in"))
        with self.assertRaisesRegex(ValueError, "duplicate HWH port"):
            logic.check_board_logic(raw(tree))

    def test_unexpected_input_driver_direction_rejected(self):
        tree = fixture(); port(tree, "ps", "pl_clk0").set("DIR", "I")
        with self.assertRaisesRegex(ValueError, "port direction"):
            logic.check_board_logic(raw(tree))

    def test_xml_entities_rejected(self):
        with self.assertRaisesRegex(ValueError, "XML declarations"):
            logic.check_board_logic(b'<!DOCTYPE EDKSYSTEM [<!ENTITY x "value">]><EDKSYSTEM/>')

    def test_non_hwh_xml_rejected(self):
        with self.assertRaisesRegex(ValueError, "EDKSYSTEM"):
            logic.check_board_logic(b"<source_tcl_matches_but_is_not_HWH/>")


if __name__ == "__main__":
    unittest.main()
