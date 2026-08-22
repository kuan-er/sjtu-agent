"""tests/test_prompts_epistemic.py — Harness 契约：知识审慎护栏必须存在。

哲学（问题驱动）：Agent = Model + Harness。模型知识库内≠事实（可能幻觉/
过时）；超出知识截止≠不存在（现实在演进）；当时正确≠当下仍正确；
用户提供的领域/环境事实优先采信。这些护栏必须在 SYSTEM_PROMPT 中，
否则模型会在"内部机制/原理"上脑补合理化解释（如把搜索结果差编成
"拆单字检索"）。
"""

from sjtu_agent.agent.prompts import SYSTEM_PROMPT, build_system_prompt


def test_system_prompt_has_epistemic_block():
    p = SYSTEM_PROMPT
    assert "知识审慎" in p, "缺少显式的知识审慎元认知块"


def test_system_prompt_knowledge_not_equal_to_fact():
    p = SYSTEM_PROMPT
    assert "知识库" in p
    assert "不等于" in p or "≠" in p or "不是事实" in p
    assert "幻觉" in p or "过时" in p


def test_system_prompt_beyond_cutoff_not_necessarily_false():
    p = SYSTEM_PROMPT
    assert "知识截止" in p
    assert "不一定" in p or "不一定不存在" in p or "现实" in p


def test_system_prompt_forbids_fabricated_mechanism_explanations():
    p = SYSTEM_PROMPT
    assert "猜测" in p  # 无法验证的机制/原理必须标注猜测
    assert "不要编造" in p or "严禁编造" in p or "绝不编造" in p


def test_system_prompt_trusts_user_provided_domain_facts():
    p = SYSTEM_PROMPT
    assert "采信" in p or "优先" in p


def test_build_system_prompt_includes_epistemic_block():
    assert "知识审慎" in build_system_prompt()