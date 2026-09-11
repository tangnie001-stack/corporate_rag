"""AgentPresetRegistry — 聚合 AgentPreset 并按名索引（含懒重载）。

懒重载语义与 SkillRegistry 一致：signature = 文件列表 + 每个 .md 的
(mtime_ns, size)，变化才重扫；同名声明 fail-fast（配置错误，非运行时降级场景）。
"""

from __future__ import annotations

import hashlib

from src.agents.presets.loader import AgentPresetLoader
from src.agents.presets.models import AgentPreset


class AgentPresetRegistry:
    """按名聚合 AgentPreset，提供懒重载与列表输出。"""

    def __init__(self, loader: AgentPresetLoader) -> None:
        """初始化注册表。

        Args:
            loader: AgentPresetLoader 实例（agents_root 已注入）
        """
        self._loader = loader
        self._presets: dict[str, AgentPreset] = {}
        self._last_signature: str | None = None

    def reload_if_changed(self) -> None:
        """扫描 signature 变化时重载（无变化跳过）。

        Raises:
            ValueError: 两个文件声明同名（fail-fast，禁止静默覆盖）
        """
        signature = self._compute_signature()
        if signature == self._last_signature:
            return
        presets: dict[str, AgentPreset] = {}
        for preset in self._loader.load_all():
            if preset.name in presets:
                existing = presets[preset.name]
                raise ValueError(
                    f"智能体预设名称冲突: {preset.name}（来自 {existing.source_path} "
                    f"与 {preset.source_path}，fail-fast 禁止静默覆盖）"
                )
            presets[preset.name] = preset
        self._presets = presets
        self._last_signature = signature

    def get(self, name: str) -> AgentPreset | None:
        """按名取预设；不存在返回 None（调用方降级系统默认 prompt）。"""
        return self._presets.get(name)

    def all(self) -> list[AgentPreset]:
        """返回全部预设（按名排序，供清单接口与启动日志）。"""
        return [self._presets[name] for name in sorted(self._presets)]

    def _compute_signature(self) -> str:
        """计算 agents_root 的扫描 signature（文件级 mtime + size）。"""
        root = self._loader.agents_root
        if not root.exists():
            return "empty"
        parts: list[str] = []
        for path in sorted(root.glob("*.md")):
            stat = path.stat()
            parts.append(f"{path.name}:{stat.st_mtime_ns}:{stat.st_size}")
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
