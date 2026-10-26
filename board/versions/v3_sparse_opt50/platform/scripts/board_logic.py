"""Read-only HWH reset/constant/clock semantics; never starts tools or hardware.

Call the existing verify_system.check_hwh separately for registers, AXI widths
and addresses. This module intentionally does not import that checker or copy
its implementation, so a future system verifier can import us without cycles.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import hashlib
import re
import xml.etree.ElementTree as ET


CLOCK_SINKS = (("ctrl", "aclk"), ("data", "aclk"), ("gemm", "ap_clk"),
               ("status", "aclk"), ("reset", "slowest_sync_clk"),
               ("ps", "maxihpm0_fpd_aclk"), ("ps", "saxihp0_fpd_aclk"))
RESET_SINKS = (("ctrl", "aresetn"), ("data", "aresetn"),
               ("gemm", "ap_rst_n"), ("status", "aresetn"))
# These are static Vivado representations seen for this unchanged PS recipe,
# not permissible measured clock rates on the physical board. In particular,
# HWH 124998749 Hz must not weaken runtime's 125000000/124999999 Hz check.
STATIC_CLOCK_RATES = {125000000, 124999999, 124998749}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def integer(value, label):
    require(isinstance(value, str) and re.fullmatch(r"(?:0x[0-9a-fA-F]+|[0-9]+)", value) is not None,
            "missing/noninteger HWH value: " + label)
    return int(value, 16) if value.lower().startswith("0x") else int(value, 10)


def one_bit(port, label):
    if "LEFT" in port.attrib or "RIGHT" in port.attrib:
        require("LEFT" in port.attrib and "RIGHT" in port.attrib and
                integer(port.get("LEFT"), label) == integer(port.get("RIGHT"), label),
                "reset/clock must be a single bit: " + label)


class HwhGraph:
    """Use physical ports, signal identity and reciprocal CONNECTION records."""

    def __init__(self, raw):
        require(isinstance(raw, bytes) and len(raw) <= 32 * 1024 * 1024, "invalid/oversized HWH input")
        require(re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", raw, re.I) is None, "XML declarations refused")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as error:
            raise ValueError("invalid HWH XML") from error
        require(root.tag == "EDKSYSTEM", "expected actual EDKSYSTEM HWH")
        self.modules, self.ports, self.parameters, self.nets = {}, {}, {}, {}
        for module in root.findall(".//MODULE"):
            instance = module.get("INSTANCE", "")
            require(instance and instance not in self.modules, "missing/duplicate HWH module")
            self.modules[instance] = module
            params = {}
            for item in module.findall("./PARAMETERS/PARAMETER"):
                name = item.get("NAME", "")
                require(name and name not in params, "duplicate HWH parameter: " + instance)
                params[name] = item.get("VALUE")
            self.parameters[instance] = params
            for port in module.findall("./PORTS/PORT"):
                key = (instance, port.get("NAME", ""))
                require(key[1] and key not in self.ports, "duplicate HWH port: " + instance)
                self.ports[key] = port
                signal = port.get("SIGNAME")
                if signal:
                    self.nets.setdefault(signal, set()).add(key)
        required = {"ps", "reset", "ctrl", "data", "gemm", "status"}
        require(required.issubset(self.modules), "missing required system modules")
        expected_vlnv = {"ps": "xilinx.com:ip:zynq_ultra_ps_e:3.5",
                         "reset": "xilinx.com:ip:proc_sys_reset:5.0",
                         "ctrl": "xilinx.com:ip:smartconnect:1.0", "data": "xilinx.com:ip:smartconnect:1.0",
                         "gemm": "xilinx.com:hls:model_gemm_top:1.0", "status": "xilinx.com:module_ref:f8_status:1.0"}
        for name, target in expected_vlnv.items():
            require(self.modules[name].get("VLNV") == target, "unexpected system module type: " + name)

    def port(self, key, direction=None, signal_type=None, polarity=None):
        require(key in self.ports, "missing HWH port: " + "/".join(key))
        p = self.ports[key]
        label = "/".join(key)
        require(direction is None or p.get("DIR") == direction, "wrong port direction: " + label)
        require(signal_type is None or p.get("SIGIS") == signal_type, "wrong port type: " + label)
        require(polarity is None or p.get("POLARITY") == polarity, "wrong reset polarity declaration: " + label)
        one_bit(p, label)
        return p

    def parameter(self, instance, name):
        require(instance in self.parameters and name in self.parameters[instance], "missing HWH parameter: " + name)
        return self.parameters[instance][name]

    def connections(self, key):
        result = []
        for item in self.ports[key].findall("./CONNECTIONS/CONNECTION"):
            endpoint = (item.get("INSTANCE", ""), item.get("PORT", ""))
            require(endpoint in self.ports, "dangling HWH connection: " + "/".join(endpoint))
            require(endpoint not in result, "duplicate HWH connection: " + "/".join(key))
            result.append(endpoint)
        return set(result)

    def driven_net(self, source, sinks=None):
        output = self.port(source, direction="O")
        signal = output.get("SIGNAME", "")
        require(bool(signal), "source net has no signal identity: " + "/".join(source))
        members = self.nets[signal]
        drivers = {key for key in members if self.ports[key].get("DIR") == "O"}
        require(drivers == {source}, "reset/clock/constant net has multiple or wrong drivers: " + signal)
        actual_sinks = members - {source}
        require(actual_sinks and all(self.ports[key].get("DIR") == "I" for key in actual_sinks),
                "missing/unknown net consumers: " + signal)
        if sinks is not None:
            require(actual_sinks == set(sinks), "missing/unexpected reset/clock consumers: " + signal)
        require(self.connections(source) == actual_sinks, "source CONNECTION list differs from signal net: " + signal)
        for sink in actual_sinks:
            self.port(sink, direction="I")
            require(self.connections(sink) == {source}, "consumer CONNECTION does not resolve unique source: " + "/".join(sink))
        return {"signal": signal, "source": "/".join(source),
                "consumers": sorted("/".join(key) for key in actual_sinks)}

    def constant_input(self, sink, expected, label):
        p = self.port(sink, direction="I")
        sources = self.connections(sink)
        require(len(sources) == 1, "constant input has no unique source: " + label)
        source = next(iter(sources))
        module = self.modules[source[0]]
        require(module.get("VLNV") == "xilinx.com:ip:xlconstant:1.1" and source[1] == "dout",
                "input is not directly driven by reviewed xlconstant: " + label)
        width = integer(self.parameter(source[0], "CONST_WIDTH"), label)
        value = integer(self.parameter(source[0], "CONST_VAL"), label)
        require(width == 1 and value in (0, 1), "nonboolean constant: " + label)
        self.driven_net(source)
        require(p.get("SIGNAME") == self.port(source).get("SIGNAME"), "constant signal mismatch: " + label)
        require(value == expected,
                label + ": constant level " + str(value) + " is active/invalid; expected inactive/valid level " + str(expected))
        return {"source": "/".join(source), "value": value, "required_level": expected}


def check_board_logic(hwh_raw: bytes, *, reset_policy="peripheral_shared") -> dict:
    """Verify actual HWH semantics before synthesis/implementation.

    Default ``peripheral_shared`` preserves this design's existing synchronous
    common-reset topology. Optional ``split`` requires ctrl/data on the
    interconnect reset and gemm/status on the peripheral reset. Neither policy
    substitutes for reset sequencing simulation or physical-board acceptance.
    """
    require(reset_policy in ("peripheral_shared", "split"), "unknown reset policy")
    graph = HwhGraph(hwh_raw)
    ext_high = integer(graph.parameter("reset", "C_EXT_RESET_HIGH"), "C_EXT_RESET_HIGH")
    aux_high = integer(graph.parameter("reset", "C_AUX_RESET_HIGH"), "C_AUX_RESET_HIGH")
    require(ext_high == 0, "PS pl_resetn0 requires low-active external reset")
    require(aux_high in (0, 1), "invalid auxiliary reset polarity parameter")
    graph.port(("ps", "pl_resetn0"), "O", "rst", "ACTIVE_LOW")
    graph.port(("reset", "ext_reset_in"), "I", "rst", "ACTIVE_LOW")
    external = graph.driven_net(("ps", "pl_resetn0"), {("reset", "ext_reset_in")})
    graph.port(("reset", "aux_reset_in"), "I", "rst", "ACTIVE_HIGH" if aux_high else "ACTIVE_LOW")
    # Validate auxiliary inactivity before checking topology so the actual
    # original aux-low/tie-low defect is reported as the primary failure.
    auxiliary = graph.constant_input(("reset", "aux_reset_in"), 0 if aux_high else 1, "aux_reset_in")
    auxiliary.update(active_high=bool(aux_high))
    graph.port(("reset", "mb_debug_sys_rst"), "I", "rst", "ACTIVE_HIGH")
    debug = graph.constant_input(("reset", "mb_debug_sys_rst"), 0, "mb_debug_sys_rst")
    locked = graph.constant_input(("reset", "dcm_locked"), 1, "dcm_locked")
    for sink in RESET_SINKS:
        graph.port(sink, "I", "rst", "ACTIVE_LOW")
    graph.port(("reset", "peripheral_aresetn"), "O", "rst", "ACTIVE_LOW")
    reset_nets = []
    if reset_policy == "peripheral_shared":
        reset_nets.append(graph.driven_net(("reset", "peripheral_aresetn"), RESET_SINKS))
    else:
        graph.port(("reset", "interconnect_aresetn"), "O", "rst", "ACTIVE_LOW")
        reset_nets.append(graph.driven_net(("reset", "interconnect_aresetn"), RESET_SINKS[:2]))
        reset_nets.append(graph.driven_net(("reset", "peripheral_aresetn"), RESET_SINKS[2:]))
    ps = graph.parameters["ps"]
    require(integer(ps.get("PSU__FPGA_PL0_ENABLE"), "PL0 enable") == 1, "PS PL0 clock disabled")
    try:
        target_mhz = Decimal(ps.get("PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ", "missing"))
    except (InvalidOperation, ValueError):
        raise ValueError("missing/invalid PS PL0 requested clock") from None
    require(target_mhz == Decimal(125), "requested PS PL0 clock must be 125MHz")
    graph.port(("ps", "pl_clk0"), "O", "clk")
    for sink in CLOCK_SINKS:
        graph.port(sink, "I", "clk")
    clock_net = graph.driven_net(("ps", "pl_clk0"), CLOCK_SINKS)
    declared = {integer(graph.ports[key].get("CLKFREQUENCY"), "/".join(key))
                for key in (("ps", "pl_clk0"), *CLOCK_SINKS)}
    require(len(declared) == 1, "inconsistent static HWH clock frequency declarations")
    declared_hz = next(iter(declared))
    require(declared_hz in STATIC_CLOCK_RATES, "unreviewed static HWH clock estimate for 125MHz recipe")
    return {"schema": "opt50_board_logic_v1", "status": "PASS_HWH_LOGIC_NOT_BOARD_TEST",
            "hwh_sha256": hashlib.sha256(hwh_raw).hexdigest(), "reset_policy": reset_policy,
            "reset_inputs": {"external": external, "auxiliary": auxiliary, "debug": debug, "dcm_locked": locked},
            "reset_domains": reset_nets,
            "clock": {**clock_net, "requested_hz": 125000000, "static_hwh_declared_hz": declared_hz,
                      "actual_board_clock_measured": False},
            "interface_checks": "Caller must separately run existing check_hwh for AXI/register/address checks",
            "scope": "Actual HWH structural semantics only; NO simulation, implementation, MMIO or board execution",
            "board": "NOT_RUN"}
