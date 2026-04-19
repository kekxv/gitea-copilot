"""Review tools definition for OpenAI function calling."""

from typing import List

REVIEW_TOOLS = [
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
                                    "description": "评论内容。直接指出问题，严禁包含任何行号前缀，严禁在 body 中回显任何发现的 Key 或 Token 原始内容。"
                                }
                            },
                            "required": ["path", "body"]
                        }
                    },
                    "summary": {
                        "type": "string",
                        "description": "详细的审查总结报告。必须包含 Markdown 表格展示检查项状态，以及下方的概览统计列表。"
                    }
                },
                "required": ["comments", "summary"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_content",
            "description": "获取仓库中其他文件的内容，用于联合分析。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径"
                    }
                },
                "required": ["path"]
            }
        }
    }
]


def get_review_system_prompt() -> str:
    """Get system prompt for review skill with structured report requirement."""
    return """你是资深代码专家和首席安全审计员。请对 PR 进行深度审查。

### 总结报告模板（必须严格遵守）：
1. **审查结果表**：
| 检查项 | 状态 | 详细说明 |
| :--- | :--- | :--- |
| 🛡️ 安全审计 | ✅ 通过 / ❌ 发现风险 | 是否有硬编码泄露、越权等 |
| ⚖️ 逻辑校验 | ✅ 通过 / ❌ 存在缺陷 | 算法、死循环、空指针等 |
| 📝 代码质量 | ✅ 通过 / 💡 优化建议 | 规范、命名、冗余代码等 |

2. **概览统计**：
- 📌 **核心变更**：[简述 1-2 个最关键的改动点]
- 🛡️ **安全风险**：[如果有敏感信息泄露，请说明"发现泄露"，严禁写出具体泄露值]
- 💡 **改进方向**：[给出整体建议]

### 核心任务：
- **隐私保护**：绝对禁止在评论中回显任何 Key、Token 或密码。
- **行号提取**：必须从 diff 的 `[N|+]` 或 `[N|-]` 中提取行号 N。
- **LGTM 准则**：若无功能性缺陷且通过安全评估，请在表格首行状态写“✅ 通过”并在末尾附上积极信号。"""


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
