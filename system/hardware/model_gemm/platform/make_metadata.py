"""Metadata only for the separately loaded model platform; no device access."""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[3]

def generate(out, register_map):
    regs=json.loads(register_map.read_text(encoding='utf-8'))
    assert regs['control']=='ap_ctrl_hs'
    r=regs['registers']
    for name in ('X','W','Y'):assert r[name+'_HIGH']==r[name+'_LOW']+4
    out.mkdir(parents=True,exist_ok=True)
    def save(name,value):(out/name).write_text(json.dumps(value,indent=2)+'\n',encoding='utf-8')
    ips=[('model_gemm_top','gemm_1','0xA0000000'),('axi_observer','status_1','0xA0010000')]
    save('ip_layout.json',{'ip_layout':{'m_count':'2','m_ip_data':[
        {'m_type':'IP_KERNEL','m_int_enable':'0','m_interrupt_id':'0','m_ip_control':'AP_CTRL_NONE',
         'm_base_address':base,'m_name':name+':'+instance} for name,instance,base in ips]}})
    save('mem_topology.json',{'mem_topology':{'m_count':'2','m_mem_data':[
        {'m_type':'MEM_DDR4','m_used':'1','m_sizeKB':'0x200000','m_tag':tag,'m_base_address':base}
        for tag,base in [('HP0_LOW','0x0'),('HP0_HIGH','0x800000000')]]}})
    save('connectivity.json',{'connectivity':{'m_count':'6','m_connection':[
        {'arg_index':str(arg),'m_ip_layout_index':'0','mem_data_index':str(bank)}
        for arg in range(3) for bank in range(2)]}})
    project=ET.Element('project',name='kv260_model_gemm')
    platform=ET.SubElement(project,'platform',vendor='xilinx',boardid='kv260',name='kv260_model_gemm',featureRomTime='0')
    ET.SubElement(platform,'version',major='1',minor='0')
    device=ET.SubElement(platform,'device',name='fpga0',fpgaDevice='xck26-sfvc784-2LV-c',addrWidth='64')
    core=ET.SubElement(device,'core',name='OCL_REGION_0',target='hw',type='clc_region',clockFreq='100MHz',numComputeUnits='2')
    for name,instance,base in ips:
        # AP_CTRL_NONE is the XRT user-managed protocol. Physical HLS is hs.
        kernel=ET.SubElement(core,'kernel',name=name,language='ip_c',vlnv='local:user:'+name+':1.0',attributes='',
            preferredWorkGroupSizeMultiple='0',workGroupSize='1',debug='false',interrupt='false',hwControlProtocol='ap_ctrl_none')
        ET.SubElement(kernel,'port',name='S_AXI_CONTROL',mode='slave',range='0x10000',dataWidth='32',portType='addressable',base='0x0')
        if name=='model_gemm_top':
            for idx,arg in enumerate(('X','W','Y')):
                port='M_AXI_GMEM'+str(idx)
                ET.SubElement(kernel,'port',name=port,mode='master',range='0xFFFFFFFFFFFFFFFF',dataWidth='32',portType='addressable',base='0x0')
                ET.SubElement(kernel,'arg',name=arg,addressQualifier='1',id=str(idx),port=port,size='0x8',offset=hex(r[arg+'_LOW']),
                    hostOffset='0x0',hostSize='0x8',type='int*' if arg=='Y' else 'char*')
            for idx,arg in enumerate(('M','K','N'),3):
                ET.SubElement(kernel,'arg',name=arg,addressQualifier='0',id=str(idx),port='S_AXI_CONTROL',size='0x4',offset=hex(r[arg]),
                    hostOffset='0x0',hostSize='0x4',type='unsigned int')
        ET.SubElement(kernel,'compileWorkGroupSize',x='1',y='1',z='1')
        ET.SubElement(kernel,'maxWorkGroupSize',x='1',y='1',z='1')
        ET.SubElement(ET.SubElement(kernel,'instance',name=instance),'addrRemap',base=base,range='0x10000',port='S_AXI_CONTROL')
    ET.indent(project)
    ET.ElementTree(project).write(out/'embedded_metadata.xml',encoding='utf-8',xml_declaration=True)
    (out/'xrt.ini').write_text('[Runtime]\nenable_flat=false\nenable_pr=false\nert_polling=true\nrw_shared=false\n',encoding='ascii')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('output',type=Path);p.add_argument('--register-map',type=Path,default=ROOT/'hardware/model_gemm/contracts/register_map.json')
    a=p.parse_args();generate(a.output,a.register_map)
