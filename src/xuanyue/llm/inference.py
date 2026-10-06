"""将已选推理设置转换为请求字段，不推断不同供应商档位之间的对应关系。"""

from xuanyue.types import ReasoningOption

REASONING_EFFORTS = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
)
THINKING_MODES = frozenset({"enabled", "disabled", "auto"})


def request_inference_kwargs(option: ReasoningOption | None) -> dict[str, object]:
    """供 Chat Completions 客户端使用；未选择表示沿用供应商默认值。

    仅允许这两个已知协议字段。配置层负责模型支持哪些选项，这里再次检查值，
    避免直接构造客户端时发送非法或互相矛盾的参数。
    """
    if option is None:
        return {}
    if not isinstance(option, ReasoningOption):
        raise TypeError("reasoning option must be a ReasoningOption")
    if any(
        not isinstance(value, str) or not value.strip()
        for value in (option.id, option.label)
    ):
        raise ValueError("reasoning option requires an id and label")
    if option.effort is not None and (
        not isinstance(option.effort, str) or option.effort not in REASONING_EFFORTS
    ):
        raise ValueError("unsupported reasoning effort")
    if option.thinking is not None and (
        not isinstance(option.thinking, str) or option.thinking not in THINKING_MODES
    ):
        raise ValueError("unsupported thinking mode")
    if (option.thinking == "disabled" and option.effort not in (None, "none")) or (
        option.thinking == "enabled" and option.effort == "none"
    ):
        raise ValueError("reasoning effort conflicts with thinking mode")

    kwargs: dict[str, object] = {}
    if option.effort is not None:
        kwargs["reasoning_effort"] = option.effort
    if option.thinking is not None:
        kwargs["extra_body"] = {"thinking": {"type": option.thinking}}
    return kwargs
