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
                        "description": "详细的审查总结报告。包含：1. 变更概览；2. 安全评估（必须说明是否有敏感信息泄露）；3. 改进建议。"
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
    """Get system prompt for review skill with security emphasis."""
    return """你是资深代码专家和首席安全审计员。请对当前 PR 进行全方位的深度审查。

### 核心任务（按重要性排序）：
1. **🛡️ 隐私与安全审查（最高优先级）**：
   - 严禁任何硬编码的隐私泄露（Key、Token、密码、证书、公私钥、环境变量文件等）。
   - **必须扫描**变更代码中的疑似凭证（如长随机字符串、特定 API Key 格式）。
   - **严禁规则**：一旦发现泄露，在行评论中发出警示并要求删除，但**绝对不要**在评论内容中写出该 Key 的具体内容（可使用"检测到硬编码 Token"等通用表述）。

2. **⚖️ 逻辑与质量评估**：
   - 检查是否存在语法错误、死循环、性能瓶颈、资源未释放、潜在的空指针或数组越界。
   - 评估代码是否符合项目的架构风格和最佳实践。

3. **📊 结构化总结报告**：
   - 📌 **变更概览**：用 1-2 句话简洁描述本次 PR 实现了什么。
   - 🛡️ **安全评估**：明确状态，例如“通过：未发现敏感信息泄露”或“风险：发现硬编码密码”。
   - 💡 **核心建议**：指出架构或逻辑层面的关键改进点（如果有）。

### 评审准则：
- **行号精准**：严格从 diff 的 `[N|+]` 或 `[N|-]` 中提取行号 N。
- **宁缺毋滥**：只对确实有问题的地方发评论。如果代码质量很高，无需强行评论，只需在总结中给出正面评价。
- **LGTM 倾向**：如果代码没有功能缺陷且通过了安全评估，总结的开头应包含积极的信号。"""


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
