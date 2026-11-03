"""Compact, large-label OPT50 figures for a 160 mm report text column."""
from html import escape
import unicodedata


class Figure:
    def __init__(self, title, height, revision):
        self.height = height
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="{height}" viewBox="0 0 1000 {height}" role="img"><title>{escape(title)}</title>',
                      '<defs><marker id="arrow" markerWidth="9" markerHeight="9" refX="8" refY="4.5" orient="auto"><path d="M0 0 L9 4.5 L0 9 Z" fill="#344b60"/></marker></defs>',
                      '<style>text{font-family:"Microsoft YaHei","Segoe UI",sans-serif;fill:#152d42}path{fill:none;stroke:#344b60;stroke-width:2.5;marker-end:url(#arrow)}</style>',
                      f'<rect width="1000" height="{height}" fill="white"/>']
        self.text(36, 45, title, 30, 'start', True)
        self.text(36, 77, f'源码 {revision[:7]} · 报告精简框图', 18)

    def text(self, x, y, value, size=24, anchor='start', bold=False):
        size = 28 if size >= 30 else 20
        units = sum(1 if unicodedata.east_asian_width(c) in 'WF' else 0.58 for c in value)
        width = units*size
        left = x-width/2 if anchor == 'middle' else x
        if left < 0 or left+width > 1000 or y > self.height:
            raise ValueError('Report text exceeds its canvas: '+value)
        weight = ' font-weight="600"' if bold else ''
        self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}"{weight}>{escape(value)}</text>')
        return width

    def box(self, x, y, width, height, title, subtitle='', color='#eef6fa'):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="10" fill="{color}" stroke="#59798f" stroke-width="1.8"/>')
        if self.text(x+width/2, y+height/2+(0 if subtitle else 8), title, 24, 'middle', True) > width-20:
            raise ValueError('Report box title is too wide: '+title)
        if subtitle:
            if self.text(x+width/2, y+height/2+32, subtitle, 20, 'middle') > width-20:
                raise ValueError('Report box subtitle is too wide: '+subtitle)

    def arrow(self, points, label='', label_pos=None):
        self.parts.append('<path d="'+' '.join(('M' if i == 0 else 'L')+f'{x} {y}' for i,(x,y) in enumerate(points))+'"/>')
        if label:
            self.text(*label_pos, label, 21, 'middle')

    def finish(self):
        return '\n'.join(self.parts+['</svg>'])+'\n'


def report_figures(data):
    revision = data['revision']
    output = {}
    f = Figure('系统整体：图片、PS 模型与 FPGA 协同推理', 790, revision)
    f.box(30,130,220,100,'图片输入','JPEG → 张量')
    f.box(355,130,290,100,'PS 模型：49 Linear','36 PL + 13 PS')
    f.box(750,130,220,100,'1000 类分类','Top1 / Top5')
    f.box(355,340,290,100,'PS / PL 适配','量化 · 同步 · 调度')
    f.box(355,550,290,100,'OPT50 共享引擎','512 MAC · 125 MHz','#e4f4ee')
    f.arrow([(250,180),(355,180)])
    f.arrow([(645,180),(750,180)])
    f.arrow([(420,230),(420,340)],'调用',(340,290))
    f.arrow([(580,340),(580,230)],'FP32 返回',(690,290))
    f.arrow([(420,440),(420,550)],'INT8 X / W',(300,500))
    f.arrow([(580,550),(580,440)],'INT32 Y',(705,500))
    f.text(40,708,'Sparse24 模型：每图 12 Dense + 24 Sparse。',24,bold=True)
    f.text(40,748,'原始 Dense 模型：每图 36 Dense。',24,bold=True)
    output['system'] = f.finish()

    f = Figure('硬件模块：PS、DDR 与共享计算引擎', 735, revision)
    f.box(30,130,245,100,'ARM PS','模型执行 / 控制')
    f.box(375,130,250,100,'控制互连','HPM0 → AXI-Lite')
    f.box(725,130,245,100,'OPT50 共享引擎','Dense / Sparse','#e4f4ee')
    f.box(375,300,250,90,'AXI observer','错误 / 在途事务')
    f.box(725,470,245,100,'数据互连','三路 AXI → 一路')
    f.box(375,470,250,100,'PS HP0','128 bit DDR 通道')
    f.box(30,470,245,100,'DDR / 四个 BO','X、Y 与两份权重')
    f.arrow([(275,180),(375,180)])
    f.arrow([(625,180),(725,180)])
    f.arrow([(500,230),(500,300)])
    f.arrow([(725,205),(675,205),(675,345),(625,345)])
    f.arrow([(847,230),(847,470)],'128 bit',(915,365))
    f.arrow([(725,520),(625,520)])
    f.arrow([(375,520),(275,520)])
    f.text(40,625,'gmem0 读 X；gmem1 共用 W_data / W_meta；gmem2 写 Y。',22)
    f.text(40,663,'数据箭头为 AXI 请求方向，读响应沿同一连接返回。',22)
    f.text(40,708,'依据平台 Tcl 等设计资料整理，非新生成的 Vivado 截图。',22,bold=True)
    output['hardware'] = f.finish()

    f = Figure('软件流程：每图检查与批次结束后的最终提交', 1110, revision)
    f.box(200,105,600,85,'批次初始化','模型 / CPU 与 Runner 会话 / owner')
    f.parts.append('<rect x="30" y="225" width="940" height="540" rx="12" fill="#f6fafc" stroke="#b1c6d2" stroke-width="1.6"/>')
    f.text(52,260,'每图重复：会话保持常驻',24,bold=True)
    f.box(90,300,365,90,'输入与整图推理','BEGIN → 36 PL → END')
    f.box(545,300,365,90,'生成比对与排名结果','计时外 · 结果尚待最终提交')
    f.box(545,455,365,90,'每图 INFO 检查','保护区 / 静态权重 / 计数')
    f.box(90,455,365,90,'暂存本图结果','INFO 通过，设备尚未关闭')
    f.box(90,625,365,85,'继续本批？','还有图片且预算、检查均通过')
    f.arrow([(500,190),(500,280),(272,280),(272,300)])
    f.arrow([(455,345),(545,345)])
    f.arrow([(727,390),(727,455)])
    f.arrow([(545,500),(455,500)])
    f.arrow([(272,545),(272,625)])
    f.arrow([(90,667),(60,667),(60,345),(90,345)])
    f.text(32,610,'是',22)
    f.box(40,835,275,100,'批次关闭会话','CPU session / Runner')
    f.box(365,835,275,100,'owner STOP / 退出','取得设备关闭证明')
    f.box(690,835,275,100,'最终验收提交','每图 accuracy / commit')
    f.arrow([(272,710),(272,790),(177,790),(177,835)],'否：结束本批',(370,787))
    f.arrow([(315,885),(365,885)])
    f.arrow([(640,885),(690,885)])
    f.text(40,1000,'比对结果生成 ≠ 最终验收提交。INFO 是每图检查，不关闭会话。',22,bold=True)
    f.text(40,1042,'批次结束后关闭会话、停止 owner；关闭证明通过后才提交验收记录。',22)
    output['software'] = f.finish()

    f = Figure('共享阵列：Dense 与 2:4 Sparse 复用同一套 MAC', 745, revision)
    f.box(30,125,280,90,'Dense 权重','mode=0：完整 W')
    f.box(30,285,280,90,'2:4 Sparse 权重','mode=1：保留值 + 位置码')
    f.box(410,205,260,100,'共用取数与选择','步数：K / K÷2')
    f.box(760,205,210,100,'32×16 MAC','共享 512 路','#e4f4ee')
    f.box(410,405,260,90,'X 行缓存','按每列位置选取激活')
    f.box(760,405,210,90,'结果块缓冲','计算 / 写回交接')
    f.box(410,585,260,90,'Burst 写回','128 bit')
    f.box(30,585,280,90,'DDR 输出 Y','INT32')
    f.arrow([(310,170),(355,170),(355,230),(410,230)])
    f.arrow([(310,330),(355,330),(355,280),(410,280)])
    f.arrow([(670,255),(760,255)])
    f.arrow([(540,405),(540,305)])
    f.arrow([(865,305),(865,405)])
    f.arrow([(865,495),(865,630),(670,630)])
    f.arrow([(410,630),(310,630)])
    f.text(40,720,'mode 改变计算路径；权重选择、乘加阵列与写回硬件共用。',22)
    output['shared-core'] = f.finish()
    return output
