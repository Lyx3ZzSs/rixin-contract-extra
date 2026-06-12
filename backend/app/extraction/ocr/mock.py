"""Mock OCR provider for development and testing.

Returns realistic block-level results that mirror what a real PaddleX /
PP-OCRv5 service would produce, including bbox coordinates, confidence,
and sort_order for each text block.
"""

from __future__ import annotations

from app.extraction.base import (
    BBox,
    OCRDetailedResult,
    OCRPageResult,
    OCRTextBlock,
)
from app.extraction.ocr.base import OCRProvider


# ---------------------------------------------------------------------------
# Mock data — each tuple is (block_type, text, bbox, confidence, sort_order)
# ---------------------------------------------------------------------------

_MOCK_BLOCKS: list[tuple[str, str, list[float] | None, float, int]] = [
    ("title",  "合同编号：HT-2024-001",
             [120, 80, 540, 115],   0.97, 1),
    ("title",  "甲方：北京日新科技有限公司",
             [120, 140, 520, 175],  0.98, 2),
    ("title",  "乙方：上海恒信信息技术有限公司",
             [120, 190, 520, 225],  0.98, 3),
    ("text",   "根据《中华人民共和国民法典》及相关法律法规的规定，甲乙双方在"
               "平等、自愿、公平、诚实信用的原则基础上，经协商一致，就项目合作事宜达成如下协议：",
             [120, 260, 900, 320],  0.96, 4),
    ("title",  "第一条 项目概述",
             [120, 350, 380, 385],  0.97, 5),
    ("text",   "甲方委托乙方进行企业管理系统的设计、开发、测试及部署工作。"
               '项目名称为\u201c企业综合管理平台\u201d，'
               "项目总金额为人民币壹佰贰拾万元整（\u00a51,200,000.00）。",
             [120, 400, 900, 445],  0.95, 6),
    ("title",  "第二条 付款方式",
             [120, 475, 380, 510],  0.97, 7),
    ("text",   "本合同总金额为人民币1,200,000.00元，分三期支付：\n"
               "1. 合同签订后5个工作日内，甲方向乙方支付合同总金额的30%，即360,000.00元；\n"
               "2. 系统开发完成并通过验收后10个工作日内，甲方向乙方支付合同总金额的40%，即480,000.00元；\n"
               "3. 系统上线运行满6个月且无重大故障后10个工作日内，甲方向乙方支付合同总金额的30%，即360,000.00元。",
             [120, 520, 900, 620],  0.94, 8),
    ("title",  "第三条 项目工期",
             [120, 650, 380, 685],  0.97, 9),
    ("text",   "项目开发周期为6个月，自合同签订之日起计算。"
               "乙方应于2024年6月30日前完成系统的全部开发、测试工作并交付甲方验收。",
             [120, 700, 900, 745],  0.95, 10),
    ("title",  "第四条 违约责任",
             [120, 775, 380, 810],  0.97, 11),
    ("text",   "1. 如乙方未按约定时间完成项目交付，每逾期一天，应向甲方支付合同总金额的0.1%作为违约金，"
               "违约金总额不超过合同总金额的10%。\n"
               "2. 如甲方未按约定时间支付款项，每逾期一天，应向乙方支付应付未付金额的0.05%作为滞纳金。",
             [120, 825, 900, 905],  0.94, 12),
    ("title",  "第五条 保密条款",
             [120, 935, 380, 970],  0.97, 13),
    ("text",   "双方应对在合作过程中知悉的对方商业秘密、技术秘密及其他保密信息承担保密义务，"
               "保密期限为合同终止后3年。",
             [120, 980, 900, 1020], 0.95, 14),
    ("title",  "第六条 知识产权",
             [120, 1050, 380, 1085], 0.97, 15),
    ("text",   "项目成果的知识产权归甲方所有。乙方不得将项目成果用于其他用途。",
             [120, 1095, 900, 1130], 0.95, 16),
    ("title",  "第七条 不可抗力",
             [120, 1160, 380, 1195], 0.97, 17),
    ("text",   "因不可抗力导致合同无法履行的，受影响的一方应在不可抗力发生后15日内书面通知对方，"
               "并提供相关证明文件。",
             [120, 1205, 900, 1250], 0.95, 18),
    ("title",  "第八条 争议解决",
             [120, 1280, 380, 1315], 0.97, 19),
    ("text",   "因本合同引起的或与本合同有关的任何争议，双方应首先通过友好协商解决；"
               "协商不成的，任何一方均可向合同签订地有管辖权的人民法院提起诉讼。",
             [120, 1325, 900, 1370], 0.95, 20),
    ("title",  "第九条 合同期限",
             [120, 1400, 380, 1435], 0.97, 21),
    ("text",   "本合同自双方签字盖章之日起生效，有效期至双方义务全部履行完毕之日止。",
             [120, 1445, 900, 1480], 0.95, 22),
    ("title",  "第十条 其他约定",
             [120, 1510, 380, 1545], 0.97, 23),
    ("text",   "本合同一式两份，甲乙双方各执一份，具有同等法律效力。",
             [120, 1555, 900, 1590], 0.95, 24),
    ("text",   "甲方（签章）：北京日新科技有限公司",
             [120, 1640, 520, 1675], 0.96, 25),
    ("text",   "法定代表人：张三",
             [120, 1690, 380, 1725], 0.94, 26),
    ("text",   "日期：2024年1月15日",
             [120, 1740, 380, 1775], 0.96, 27),
    ("text",   "乙方（签章）：上海恒信信息技术有限公司",
             [120, 1810, 520, 1845], 0.96, 28),
    ("text",   "法定代表人：李四",
             [120, 1860, 380, 1895], 0.94, 29),
    ("text",   "日期：2024年1月15日",
             [120, 1910, 380, 1945], 0.96, 30),
]

# Pre-built page result (single page document)
_MOCK_PAGE = OCRPageResult(
    page_no=1,
    width=1024,
    height=2000,
    confidence=0.95,
    blocks=[
        OCRTextBlock(
            block_type=btype,
            text=text,
            bbox=BBox.from_list(bbox) if bbox else None,
            confidence=conf,
            sort_order=sort_order,
        )
        for btype, text, bbox, conf, sort_order in _MOCK_BLOCKS
    ],
)

MOCK_DETAILED_RESULT = OCRDetailedResult(
    pages=[_MOCK_PAGE],
    provider="mock",
)

# Legacy full text — used by tests that reference MOCK_CONTRACT_TEXT
MOCK_CONTRACT_TEXT = _MOCK_PAGE.full_text


class MockOCRProvider(OCRProvider):
    """Mock provider that returns pre-built block-level results."""

    def extract_detailed(self, file_path: str, file_type: str) -> OCRDetailedResult:
        return MOCK_DETAILED_RESULT.model_copy(deep=True)
