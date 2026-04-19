"""Review tools definition for OpenAI function calling."""

from typing import List

REVIEW_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_file_content",
            "description": "获取仓库中其他文件的内容，用于联合分析。调用此工具后继续分析，不要同时输出评论。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径，例如 src/main.py"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "submit_review",
            "description": "提交最终的代码审查结果。调用此工具表示审查完成，会话结束。只能调用一次。",
            "parameters": {
                "type": "object",
                "properties": {
                    "comments": {
                        "type": "array",
                        "description": "行评论列表，每个元素包含 path、body 以及 new_position 或 old_position",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {
                                    "type": "string",
                                    "description": "文件路径"
                                },
                                "new_position": {
                                    "type": "integer",
                                    "description": "对于新增或修改的代码行（标记为 [+]），必须填入其对应的 N 值"
                                },
                                "old_position": {
                                    "type": "integer",
                                    "description": "对于已删除的代码行（标记为 [-]），必须填入其对应的 N 值"
                                },
                                "body": {
                                    "type": "string",
                                    "description": "评论内容。直接指出问题，严禁包含任何行号前缀或“在第X行”之类的废话。"
                                }
                            },
                            "required": ["path", "body"]
                        }
                    },
                    "summary": {
                        "type": "string",
                        "description": "针对本次提供的代码块的简要总结（15字以内）"
                    }
                },
                "required": ["comments", "summary"]
            }
        }
    }
]


def get_review_system_prompt() -> str:
    """Get system prompt for review skill."""
    return """你是代码审查助手，正在分析 PR 的代码变更。

工作准则：
1. **行号提取**：必须且只能从 diff 内容前的 `[N|+]` 或 `[N|-]` 标记中提取行号 N。严禁自行计算或猜测。
2. **精准定位**：
   - 发现新增/修改行的问题，仅提供 `new_position`。
   - 发现已删除代码的问题（如不该删），仅提供 `old_position`。
   - 绝对不要同时提供 `new_position` 和 `old_position`。
3. **分块模式**：当前 PR 被拆分为多个部分。请仅针对当前看到的变更块提交 `submit_review`。
4. **精简评论**：
   - 只对确实存在逻辑错误、安全隐患或严重规范问题的代码行添加评论。
   - 如果一个逻辑块有多个问题，合并为一条评论提交到该块的核心行上。
   - 评论 body 应直接、客观，例如：“此处变量未定义可能导致运行时错误”。
5. **获取上下文**：如果当前变更块不足以判断逻辑（如调用了未见的函数），请先调用 `get_file_content` 查看源文件。"""


def get_analyze_tools() -> List[dict]:
    """Get tools for analyze skill (future extension)."""
    return [
        {
            "type": "function",
            "function": {
                "name": "get_file_content",
                "description": "获取仓库中其他文件的内容",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "文件路径"}
                    },
                    "required": ["path"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "submit_answer",
                "description": "提交最终回答",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string", "description": "回答内容"}
                    },
                    "required": ["answer"]
                }
            }
        }
    ]
