"""将已选推理设置和输出配额转换为请求字段，不猜测供应商或模型的能力。"""

from xuanyue.types import ReasoningOption

REASONING_EFFORTS = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
)
THINKING_MODES = frozenset({"enabled", "disabled", "auto"})


def request_output_limit_kwargs(
    max_output_tokens: int | None = None,
    output_token_parameter: str = "max_tokens",
) -> dict[str, object]:
    """供聊天客户端设置一次调用的输出配额，只发送用户指定的一个字段。

    max_tokens 与 max_completion_tokens 的支持范围和计数口径由具体模型决定，
    不能按供应商名称自动替换。未填配额时沿用供应商默认值；输入容量另由配置层保存，
    它不是 Chat Completions 请求参数，也不触发本地截断。
    """
    if output_token_parameter not in ("max_tokens", "max_completion_tokens"):
        raise ValueError("unsupported output token parameter")
    if max_output_tokens is None:
        return {}
    # bool 是 int 的子类，但不能当成用户填写的 token 配额。
    if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 2147483647:
        raise ValueError("max_output_tokens must be a positive 32-bit integer")
    return {output_token_parameter: max_output_tokens}


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
