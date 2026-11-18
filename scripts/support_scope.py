"""支持范围声明：数据侧保留归档，但生成链跳过的系列。

列在此处的系列不参与流水线：extract 脚本不产出 CSV 行，
chiptool_registers.py 不处理其 SVD，芯片不进入 metapac。
svd/ 归档与既有审计记录仍保留，恢复支持时删除对应条目，
并复核 peripheral-version-map.json 的实例覆盖后重跑全链。
"""

UNSUPPORTED_SERIES: dict[str, dict[str, str]] = {
    # 侧（'ddl' / 'll'）→ {系列名: 暂不支持的原因}
    'll': {
        # 外设 IP 多代缝合：无 AOS/EVT_SRC，DMAC 用固定请求编号，
        # 大量旧式寄存器布局与 LL 主线并存，暂不做支持。
        'HC32F175': 'IP 多代缝合（旧式布局、固定 DMAC 请求编号），暂不做支持',
    },
}


def unsupported(side: str) -> dict[str, str]:
    """返回某侧暂不支持的系列及原因。"""
    return UNSUPPORTED_SERIES.get(side, {})
