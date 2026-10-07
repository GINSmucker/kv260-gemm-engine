"""Cross-check actual paired artifacts/HWH against the generated new ABI."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[3]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser();p.add_argument('build',type=Path);a=p.parse_args();build=a.build.resolve()
    assert build.is_relative_to(ROOT/'results/model_integration')
    identity=json.loads((build/'run_manifest.json').read_text(encoding='utf-8-sig'))
    ledger=json.loads((build/'artifact_sha256.json').read_text(encoding='utf-8-sig'))
    for n,v in ledger.items():assert sha(build/'artifacts'/n)==v['sha256'] and (build/'artifacts'/n).stat().st_size==v['bytes']
    assert (build/'observer_pass.txt').read_text().strip()=='PASS' and (build/'bd_pass.txt').read_text().strip()=='PASS'
    match=re.fullmatch(r'PASS WNS=([\d.]+) WHS=([\d.]+)',(build/'build_pass.txt').read_text().strip());assert match
    setup,hold=map(float,match.groups());assert setup>=0 and hold>=0
    hwh=ET.parse(build/'artifacts/kv260_model.hwh');modules={m.attrib['INSTANCE']:m for m in hwh.findall('.//MODULE')}
    assert modules['gemm'].attrib['VLNV']=='xilinx.com:hls:model_gemm_top:1.0' and modules['status'].attrib['MODTYPE']=='f8_status'
    register_path=(build/identity['register_map']).resolve() if 'register_map' in identity else ROOT/'hardware/model_gemm/contracts/register_map.json'
    if 'register_map' in identity:
        assert register_path.is_relative_to(build/'source/contracts')
        assert sha(register_path)==identity['source_sha256'][identity['register_map']]
        manifest=build/'source/A_delivery_manifest.json'
        assert sha(manifest)==identity['a1_manifest_sha256']
        delivery=json.loads(manifest.read_text(encoding='utf-8-sig'))
        if 'contracts/register_map.json' in delivery['files']:
            assert sha(register_path)==delivery['files']['contracts/register_map.json']
        for name,digest in delivery['files'].items():
            if name.startswith('ip/unpacked/') and name!='ip/unpacked/component.xml':
                assert sha(build/'source/ip'/name.removeprefix('ip/unpacked/'))==digest
        assert sha(build/'source/ip/component.xml')==identity['metadata_change']['after_sha256']
    r=json.loads(register_path.read_text(encoding='utf-8-sig'))['registers']
    aliases={'GIER':'GIE','IP_IER':'IER','IP_ISR':'ISR','ap_return':'ERROR','X_1':'X_LOW','X_2':'X_HIGH','W_1':'W_LOW','W_2':'W_HIGH','Y_1':'Y_LOW','Y_2':'Y_HIGH'}
    actual={}
    for reg in modules['gemm'].findall('.//REGISTER'):
        properties={e.attrib['NAME']:e.attrib['VALUE'] for e in reg.findall('PROPERTY')};name=aliases.get(reg.attrib['NAME'],reg.attrib['NAME'])
        assert int(properties['SIZE'])==32;actual[name]=int(properties['ADDRESS_OFFSET'])
    assert actual==r and len(actual)==14
    params={e.attrib['NAME']:e.attrib['VALUE'] for e in modules['gemm'].findall('./PARAMETERS/PARAMETER')}
    for i in range(3):assert params[f'C_M_AXI_GMEM{i}_ADDR_WIDTH']=='64' and params[f'C_M_AXI_GMEM{i}_DATA_WIDTH']=='32'
    ranges=[e.attrib for e in hwh.findall('.//MEMRANGE')]
    for i in range(3):
        entries={e['ADDRESSBLOCK']:e for e in ranges if e['MASTERBUSINTERFACE']==f'm_axi_gmem{i}'}
        assert int(entries['HP0_DDR_LOW']['BASEVALUE'],0)==0 and int(entries['HP0_DDR_LOW']['HIGHVALUE'],0)==0x7fffffff
        assert int(entries['HP0_DDR_HIGH']['BASEVALUE'],0)==0x800000000 and int(entries['HP0_DDR_HIGH']['HIGHVALUE'],0)>=0x87fffffff
    for name,base in [('gemm',0xa0000000),('status',0xa0010000)]:
        e=next(e for e in ranges if e['INSTANCE']==name);assert int(e['BASEVALUE'],0)==base and int(e['HIGHVALUE'],0)==base+65535
    drc=(build/'drc.rpt').read_text();assert not re.search(r'\|\s*Error\s*\|',drc)
    result={'status':'PASS','schema':'model_platform_crosscheck_v1','build':build.name,'a1_manifest_sha256':identity['a1_manifest_sha256'],
        'WNS_ns':setup,'WHS_ns':hold,'artifact_count':len(ledger),'register_count':len(actual),'address_ranges':ranges,
        'axi_address_bits':64,'axi_data_bits':32,'HPM0_HP0_bits':128,'clock_constraint_ns':10,'formal_A2':'NOT_RUN','artifacts':ledger}
    (build/'platform_verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:v for k,v in result.items() if k not in ('address_ranges','artifacts')},indent=2))

if __name__=='__main__':main()
