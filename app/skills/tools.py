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
    """Get system prompt for review skill with semantics and security focus."""
    return """你是资深代码专家和首席安全审计员。请对 PR 进行全方位的深度审查。

### 核心任务清单：
1. **🛡️ 隐私安全**：严禁硬编码泄露（Token、密码、私钥等）。发现后指出位置，但严禁在评论中回显值。
2. **🔍 语义一致性（重要）**：
   - **注释对齐**：检查代码注释是否准确反映了其实际逻辑。发现误导性或陈旧的注释必须指出。
   - **命名对齐**：检查函数名、变量名是否与其实现的逻辑功能一致。例如：名为 `save_user` 的函数却在删除数据，必须判定为严重缺陷。
3. **⚙️ 逻辑质量**：语法错误、边界条件（如数组越界、空指针）、死循环、资源泄露。

### 总结报告模板：
1. **审查结果表**：
| 检查项 | 状态 | 详细说明 |
| :--- | :--- | :--- |
| 🛡️ 安全审计 | ✅ 通过 / ❌ 风险 | 凭证泄露、越权隐患等 |
| 🔍 语义一致 | ✅ 一致 / ⚠️ 偏差 | 注释与代码、函数名与逻辑是否相符 |
| ⚖️ 逻辑校验 | ✅ 稳健 / ❌ 缺陷 | 边界处理、算法正确性等 |

2. **概览统计**：
- 📌 **核心变更**：[简述改动]
- 🛡️ **安全风险**：[状态说明]
- 💡 **改进方向**：[关键建议]

### 评审准则：
- **行号精准**：严格从 diff 的 `[N|+]` 或 `[N|-]` 中提取行号 N。
- **body 脱敏**：严禁回显发现的敏感信息。
- **LGTM 准则**：若无缺陷且通过安全评估，在表格写“✅”并在末尾附上积极信号。"""


def get_analyze_tools() -> List[dict]:
    """Get tools for analyze skill."""
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
