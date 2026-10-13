"""Extract ABI offsets from this build, and cross-check header/component/RTL."""
from pathlib import Path
import argparse
import datetime as dt
import hashlib
import json
import re
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def children(node):
    return {c.tag:c.text for c in node}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--run",type=Path,required=True)
    args=ap.parse_args()
    run=args.run.resolve()
    sol=run/"model_gemm_hls/solution1"
    assert (run/"csynth_pass.txt").exists() and (run/"export_pass.txt").exists()
    headers=list((sol/"impl/ip").rglob("xmodel_gemm_top_hw.h"))
    assert len(headers)==1, "Exported hardware driver header not unique"
    head=headers[0]
    control=sol/"syn/verilog/model_gemm_top_control_s_axi.v"
    top=sol/"syn/verilog/model_gemm_top.v"
    component=sol/"impl/ip/component.xml"
    ht=head.read_text(encoding="utf-8")
    ct=control.read_text(encoding="utf-8")
    rt=top.read_text(encoding="utf-8")
    macros={name:int(value,0) for name,value in re.findall(r"#define\s+(\w+)\s+(0x[0-9a-fA-F]+|[0-9]+)\b",ht)}
    rtl_params={key:int(value,16) for key,value in re.findall(r"\b(ADDR_[A-Z0-9_]+)\s*=\s*\d+'h([0-9a-fA-F]+)",ct)}
    fields={"CTRL":("AP_CTRL","AP_CTRL",0),"GIE":("GIE","GIE",0),"IER":("IER","IER",0),"ISR":("ISR","ISR",0),"ERROR":("AP_RETURN","AP_RETURN_0",0),"X_LOW":("X_DATA","X_DATA_0",0),"X_HIGH":("X_DATA","X_DATA_1",4),"W_LOW":("W_DATA","W_DATA_0",0),"W_HIGH":("W_DATA","W_DATA_1",4),"Y_LOW":("Y_DATA","Y_DATA_0",0),"Y_HIGH":("Y_DATA","Y_DATA_1",4),"M":("M_DATA","M_DATA_0",0),"K":("K_DATA","K_DATA_0",0),"N":("N_DATA","N_DATA_0",0)}
    registers={}
    for name,(driver_key,rtl_key,delta) in fields.items():
        candidates=[v for k,v in macros.items() if k.endswith("ADDR_"+driver_key)]
        assert len(candidates)==1, "Hardware header field missing "+driver_key
        if delta:
            assert [v for k,v in macros.items() if k.endswith("BITS_"+driver_key)]==[64], "High word requires generated 64-bit type"
        actual=candidates[0]+delta
        assert rtl_params["ADDR_"+rtl_key]==actual, "RTL/header disagreement "+rtl_key
        registers[name]=actual
    xml=ET.parse(component).getroot()
    def local(tag): return tag.rsplit("}",1)[-1]
    xml_regs={}
    for el in xml.iter():
        if local(el.tag)=="register":
            data={local(c.tag):c.text for c in el}
            if "name" in data and "addressOffset" in data:
                assert data["name"] not in xml_regs, "Duplicate component register "+data["name"]
                xml_regs[data["name"]]={"offset":int(data["addressOffset"],0),"bits":int(data["size"]),"access":data["access"]}
    # IP-XACT emits each pointer as two 32-bit registers (X_1/X_2), while
    # the generated driver exposes one 64-bit X_DATA base plus its high word.
    component_fields={"CTRL":"CTRL","GIE":"GIER","IER":"IP_IER","ISR":"IP_ISR","ERROR":"ap_return","X_LOW":"X_1","X_HIGH":"X_2","W_LOW":"W_1","W_HIGH":"W_2","Y_LOW":"Y_1","Y_HIGH":"Y_2","M":"M","K":"K","N":"N"}
    assert set(xml_regs)==set(component_fields.values()), "Unexpected component register set"
    for field,reg in component_fields.items():
        expected_access="read-only" if field=="ERROR" else "write-only" if field in ("X_LOW","X_HIGH","W_LOW","W_HIGH","Y_LOW","Y_HIGH","M","K","N") else "read-write"
        assert xml_regs[reg]=={"offset":registers[field],"bits":32,"access":expected_access}, "component map disagreement "+reg
    width={}
    for port in range(3):
        addr=int(re.search(r"C_M_AXI_GMEM%d_ADDR_WIDTH\s*=\s*(\d+)"%port,rt).group(1))
        data=int(re.search(r"C_M_AXI_GMEM%d_DATA_WIDTH\s*=\s*(\d+)"%port,rt).group(1))
        assert addr==64 and data==32
        width["gmem%d"%port]={"address_bits":addr,"data_bits":data}
    assert "ap_continue" not in ct and "bit 1  - ap_done (Read/COR)" in ct
    assert re.search(r"else if \(ar_hs && raddr == ADDR_AP_CTRL\)\s+int_ap_done <= 1'b0",ct)
    assert "bit 0  - ap_start (Read/Write/COH)" in ct
    xr=ET.parse(sol/"syn/report/model_gemm_top_csynth.xml").getroot()
    perf=children(xr.find("./PerformanceEstimates/SummaryOfOverallLatency"))
    summary={"schema":"model_gemm_hls_export_inspection_v1","status":"PASS","part":xr.findtext("./UserAssignments/Part"),"tool_version":xr.findtext("./ReportVersion/Version"),"clock_constraint_ns":float(xr.findtext("./UserAssignments/TargetClockPeriod")),"clock_uncertainty_ns":float(xr.findtext("./UserAssignments/ClockUncertainty")),"hls_estimated_clock_ns":float(xr.findtext("./PerformanceEstimates/SummaryOfTimingAnalysis/EstimatedClockPeriod")),"top_latency_and_interval_estimates":perf,"resources":{k:int(v) for k,v in children(xr.find("./AreaEstimates/Resources")).items()},"outer_k_pipeline_achieved_II":1,"outer_k_II_evidence":"dense_layer_csynth.rpt actual loop row; not top Interval","axi":width,"registers":registers,"control":"ap_ctrl_hs","done":"clear_on_read_by_exclusive_owner","start":"clear_on_handshake","hardware_cycles_counter":"UNAVAILABLE","board_test":"NOT_RUN","provenance":{str(p.relative_to(run)).replace("\\","/"):sha(p) for p in (head,control,top,component,sol/"impl/export.zip")}}
    assert summary["part"].lower()=="xck26-sfvc784-2lv-c" and summary["tool_version"]=="2020.2.2"
    assert summary["clock_constraint_ns"]==10 and summary["clock_uncertainty_ns"]==1
    core_report=(sol/"syn/report/dense_layer_csynth.rpt").read_text(encoding="utf-8")
    loop_rows=[line for line in core_report.splitlines() if re.search(r"\bouter_k\b",line)]
    assert len(loop_rows)==1
    loop_cells=[cell.strip() for cell in loop_rows[0].split('|')[1:-1]]
    assert loop_cells[4:6]==["1","1"] and loop_cells[-1]=="yes", "Actual outer K II must be inspected, not inferred from pragma"
    (run/"hls_export_inspection.json").write_text(json.dumps(summary,indent=2)+"\n",encoding="utf-8")
    regfile={"schema":"model_gemm_register_map_v1","build_id":run.name,"control":"ap_ctrl_hs","registers":registers,"axi":width,"done_semantics":"read_CTRL_clears_done_exclusive_owner_only","error_semantics":"read_only_uint32_return_after_done","source_sha256":summary["provenance"]}
    (ROOT/"contracts/register_map.json").write_text(json.dumps(regfile,indent=2)+"\n",encoding="utf-8")
    regmd="# model_gemm_v1 生成寄存器记录\n\n三方核验日期："+dt.datetime.now().astimezone().date().isoformat()+"，build `"+run.name+"`。导出驱动头、component.xml、Verilog 三方地址一致，status PASS。\n\n| 寄存器 | 实际 offset | 语义 |\n|---|---:|---|\n"
    for name,off in registers.items():
        semantics="只读，0 OK /1 INVALID_SHAPE" if name=="ERROR" else "Read/COR，Done由唯一 owner读清；start COH，auto_restart关闭" if name=="CTRL" else "32-bit参数/地址字，运行中保持稳定" if name not in ("GIE","IER","ISR") else "未启用中断"
        regmd+="| %s | 0x%02x | %s |\n"%(name,off,semantics)
    regmd+="\n三路 m_axi 地址64-bit、数据32-bit。X/W/Y各两个32-bit字；顶层M/K/N为uint32。CTRL bit0=start、bit1=done、bit2=idle、bit3=ready、bit7=auto_restart；没有 Continue。初版没有 kernel cycles。\n\n完整头/RTL/component 与 hash 在运行目录 `hls_export_inspection.json` 和交付包。地址资料核对不等于新平台或实板通过。\n"
    (ROOT/"contracts/register_map.md").write_text(regmd,encoding="utf-8")
    hpp="// Generated from this HLS driver/header/component/RTL, not copied from F8.\n#ifndef MODEL_GEMM_REGISTERS_HPP\n#define MODEL_GEMM_REGISTERS_HPP\n#include <stdint.h>\nnamespace model_gemm_regs {\n"+"".join("static const uint32_t %s=0x%02xu;\n"%(k,v) for k,v in registers.items())+"}\n#endif\n"
    (ROOT/"include/model_gemm_registers.hpp").write_text(hpp,encoding="utf-8")
    print("EXPORT_DRIVER_COMPONENT_RTL_CROSSCHECK_PASS",json.dumps(registers))


if __name__=="__main__":
    main()
