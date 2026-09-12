"""来源声明的文案与 stage 常量（turn-provenance-observability design D2/D4/D9）。"""

from src.config.const import SSEInteractionTexts


def test_agent_in_use_template():
    """智能体声明文案：模板可填展示名。"""
    assert SSEInteractionTexts.AGENT_IN_USE_TMPL.format(agent="财务专家") == (
        "当前使用了 财务专家"
    )


def test_skills_loaded_template_uses_fullwidth_colon_and_ideographic_comma():
    """技能声明文案：全角冒号 + 顿号（与 UNKNOWN_SKILL_PREFIX 的列表标点一致）。"""
    assert SSEInteractionTexts.SKILLS_LOADED_TMPL.format(skills="a、b、c") == (
        "成功加载 skills：a、b、c"
    )


def test_skill_in_use_template_is_fork_wording():
    """fork 轮措辞：称"使用/执行"而非"加载"（fork 不注入上下文）。"""
    assert SSEInteractionTexts.SKILL_IN_USE_TMPL.format(skill="finance-analyst") == (
        "使用技能：/finance-analyst（子代理执行）"
    )


def test_turn_stages_are_dedicated():
    """来源声明的 stage 不得复用工具态/思考态（否则前端触发正文旁白重分类）。"""
    reserved = {
        SSEInteractionTexts.STAGE_RETRIEVE,
        SSEInteractionTexts.STAGE_WEB_SEARCH,
        SSEInteractionTexts.STAGE_AGENT,
    }
    turn_stages = {
        SSEInteractionTexts.STAGE_TURN_AGENT,
        SSEInteractionTexts.STAGE_TURN_SKILL,
    }
    assert not (turn_stages & reserved)
    assert len(turn_stages) == 2
